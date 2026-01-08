"""Entity resolution and deduplication for knowledge graph."""

import re
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher
import structlog

from .graph import KnowledgeGraph

logger = structlog.get_logger()


class EntityResolver:
    """Resolves and deduplicates entities in the knowledge graph."""

    # Common company suffixes to normalize
    COMPANY_SUFFIXES = [
        ' Inc.', ' Inc', ' Corp.', ' Corp', ' LLC', ' Ltd.', ' Ltd',
        ' Corporation', ' Company', ' Co.', ' Co', ' PLC', ' LP', ' LLP',
        ' Holdings', ' Group', ' Technologies', ' Technology', ' Systems',
    ]

    # Known aliases (canonical -> list of aliases)
    KNOWN_ALIASES = {
        'nvidia': ['nvidia corp', 'nvidia corporation', 'nvidia inc'],
        'meta': ['meta platforms', 'facebook', 'meta inc'],
        'google': ['alphabet', 'google inc', 'google llc', 'alphabet inc'],
        'amazon': ['amazon inc', 'amazon.com', 'amazon web services', 'aws'],
        'microsoft': ['microsoft corp', 'microsoft corporation', 'msft'],
        'apple': ['apple inc', 'apple computer'],
        'openai': ['open ai', 'openai inc'],
        'anthropic': ['anthropic ai', 'anthropic inc'],
    }

    # Invalid entity names to remove
    INVALID_ENTITIES = [
        'investor', 'company', 'startup', 'firm', 'corporation',
        'employees', 'staff', 'team', 'people', 'person',
        'the company', 'the startup', 'the firm',
    ]

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg
        self._alias_cache: Dict[str, int] = {}  # normalized name -> canonical entity_id

    def normalize_name(self, name: str) -> str:
        """Normalize entity name for comparison."""
        normalized = name.lower().strip()

        # Remove company suffixes
        for suffix in self.COMPANY_SUFFIXES:
            if normalized.endswith(suffix.lower()):
                normalized = normalized[:-len(suffix)].strip()

        # Remove special characters but keep spaces
        normalized = re.sub(r'[^\w\s]', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        return normalized

    def find_canonical(self, name: str, entity_type: str = None) -> Optional[int]:
        """Find the canonical entity ID for a name."""
        normalized = self.normalize_name(name)

        # Check cache first
        if normalized in self._alias_cache:
            return self._alias_cache[normalized]

        # Check known aliases
        for canonical, aliases in self.KNOWN_ALIASES.items():
            if normalized == canonical or normalized in aliases:
                # Find the canonical entity in the database
                entity = self.kg.get_entity(canonical, entity_type)
                if entity:
                    self._alias_cache[normalized] = entity.id
                    return entity.id

        return None

    def similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two entity names."""
        n1 = self.normalize_name(name1)
        n2 = self.normalize_name(name2)
        return SequenceMatcher(None, n1, n2).ratio()

    def find_duplicates(self, threshold: float = 0.85) -> List[Tuple[int, int, float]]:
        """Find potential duplicate entities.

        Returns list of (entity1_id, entity2_id, similarity_score).
        """
        duplicates = []

        with self.kg._connection() as conn:
            cursor = conn.execute("""
                SELECT id, name, entity_type, mention_count
                FROM kg_entities
                ORDER BY mention_count DESC
            """)
            entities = list(cursor.fetchall())

        # Compare each pair
        for i, e1 in enumerate(entities):
            for e2 in entities[i+1:]:
                # Only compare same type or if one is 'unknown'
                if e1['entity_type'] != e2['entity_type']:
                    if e1['entity_type'] != 'unknown' and e2['entity_type'] != 'unknown':
                        continue

                sim = self.similarity(e1['name'], e2['name'])
                if sim >= threshold:
                    duplicates.append((e1['id'], e2['id'], sim))
                    logger.info(
                        "duplicate_found",
                        entity1=e1['name'],
                        entity2=e2['name'],
                        similarity=sim
                    )

        return duplicates

    def merge_entities(self, keep_id: int, merge_id: int) -> bool:
        """Merge two entities, keeping the first and updating references."""
        with self.kg._connection() as conn:
            try:
                # Get entity info
                keep = conn.execute(
                    "SELECT * FROM kg_entities WHERE id = ?", (keep_id,)
                ).fetchone()
                merge = conn.execute(
                    "SELECT * FROM kg_entities WHERE id = ?", (merge_id,)
                ).fetchone()

                if not keep or not merge:
                    logger.error("merge_failed", reason="entity not found")
                    return False

                logger.info(
                    "merging_entities",
                    keep=keep['name'],
                    merge=merge['name']
                )

                # Update relationships to point to keep_id
                conn.execute("""
                    UPDATE kg_relationships
                    SET subject_id = ?
                    WHERE subject_id = ?
                """, (keep_id, merge_id))

                conn.execute("""
                    UPDATE kg_relationships
                    SET object_id = ?
                    WHERE object_id = ?
                """, (keep_id, merge_id))

                # Update mention count
                new_count = keep['mention_count'] + merge['mention_count']
                conn.execute("""
                    UPDATE kg_entities
                    SET mention_count = ?
                    WHERE id = ?
                """, (new_count, keep_id))

                # Update entity type if the kept one is 'unknown'
                if keep['entity_type'] == 'unknown' and merge['entity_type'] != 'unknown':
                    conn.execute("""
                        UPDATE kg_entities
                        SET entity_type = ?
                        WHERE id = ?
                    """, (merge['entity_type'], keep_id))

                # Move enrichment data
                conn.execute("""
                    UPDATE OR IGNORE kg_enrichment
                    SET entity_id = ?
                    WHERE entity_id = ?
                """, (keep_id, merge_id))

                # Move tags
                conn.execute("""
                    UPDATE OR IGNORE kg_tags
                    SET entity_id = ?
                    WHERE entity_id = ?
                """, (keep_id, merge_id))

                # Add alias
                conn.execute("""
                    INSERT OR IGNORE INTO kg_aliases
                    (entity_id, alias, normalized_alias)
                    VALUES (?, ?, ?)
                """, (keep_id, merge['name'], merge['normalized_name']))

                # Delete the merged entity
                conn.execute("DELETE FROM kg_entities WHERE id = ?", (merge_id,))

                conn.commit()
                logger.info("entities_merged", keep_id=keep_id, merge_id=merge_id)
                return True

            except Exception as e:
                logger.error("merge_failed", error=str(e))
                return False

    def remove_invalid_entities(self) -> int:
        """Remove entities with invalid names."""
        removed = 0

        with self.kg._connection() as conn:
            for invalid in self.INVALID_ENTITIES:
                cursor = conn.execute("""
                    SELECT id, name FROM kg_entities
                    WHERE LOWER(name) = ?
                """, (invalid.lower(),))

                for row in cursor.fetchall():
                    # Delete relationships first
                    conn.execute(
                        "DELETE FROM kg_relationships WHERE subject_id = ? OR object_id = ?",
                        (row['id'], row['id'])
                    )
                    # Delete entity
                    conn.execute("DELETE FROM kg_entities WHERE id = ?", (row['id'],))
                    logger.info("invalid_entity_removed", name=row['name'], id=row['id'])
                    removed += 1

            conn.commit()

        return removed

    def fix_entity_types(self) -> int:
        """Fix entities with 'unknown' type based on relationships."""
        fixed = 0

        with self.kg._connection() as conn:
            # Find unknown entities
            cursor = conn.execute("""
                SELECT id, name FROM kg_entities WHERE entity_type = 'unknown'
            """)

            for row in cursor.fetchall():
                entity_id = row['id']
                name = row['name']

                # Check relationships to infer type
                # If funded by investor -> company
                funded = conn.execute("""
                    SELECT COUNT(*) FROM kg_relationships
                    WHERE subject_id = ? AND predicate = 'FUNDED_BY'
                """, (entity_id,)).fetchone()[0]

                # If hiring -> company
                hiring = conn.execute("""
                    SELECT COUNT(*) FROM kg_relationships
                    WHERE object_id = ? AND predicate = 'HIRED_BY'
                """, (entity_id,)).fetchone()[0]

                # If acquired something -> company
                acquiring = conn.execute("""
                    SELECT COUNT(*) FROM kg_relationships
                    WHERE subject_id = ? AND predicate = 'ACQUIRED'
                """, (entity_id,)).fetchone()[0]

                # If hired by -> person
                hired = conn.execute("""
                    SELECT COUNT(*) FROM kg_relationships
                    WHERE subject_id = ? AND predicate = 'HIRED_BY'
                """, (entity_id,)).fetchone()[0]

                # If CEO/CTO/CFO of -> person
                exec_role = conn.execute("""
                    SELECT COUNT(*) FROM kg_relationships
                    WHERE subject_id = ? AND predicate IN ('CEO_OF', 'CTO_OF', 'CFO_OF', 'FOUNDED')
                """, (entity_id,)).fetchone()[0]

                # Determine type
                new_type = None
                if exec_role > 0 or hired > 0:
                    new_type = 'person'
                elif funded > 0 or hiring > 0 or acquiring > 0:
                    new_type = 'company'

                if new_type:
                    conn.execute("""
                        UPDATE kg_entities SET entity_type = ? WHERE id = ?
                    """, (new_type, entity_id))
                    logger.info("entity_type_fixed", name=name, new_type=new_type)
                    fixed += 1

            conn.commit()

        return fixed

    def run_all(self) -> Dict[str, int]:
        """Run all resolution and cleanup tasks."""
        results = {
            'invalid_removed': self.remove_invalid_entities(),
            'types_fixed': self.fix_entity_types(),
            'duplicates_found': 0,
            'duplicates_merged': 0,
        }

        # Find and merge duplicates
        duplicates = self.find_duplicates()
        results['duplicates_found'] = len(duplicates)

        for keep_id, merge_id, sim in duplicates:
            if self.merge_entities(keep_id, merge_id):
                results['duplicates_merged'] += 1

        logger.info("entity_resolution_complete", **results)
        return results
