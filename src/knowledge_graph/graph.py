"""SQLite-backed knowledge graph implementation."""

import sqlite3
import json
from datetime import date
from typing import List, Optional
from contextlib import contextmanager
from pathlib import Path

import structlog

from .interfaces import (
    KnowledgeGraphInterface, GraphEntity, GraphRelationship
)
from ..config.settings import settings

logger = structlog.get_logger()


class KnowledgeGraph(KnowledgeGraphInterface):
    """SQLite-backed knowledge graph."""

    SCHEMA = """
    -- Entities
    CREATE TABLE IF NOT EXISTS kg_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        attributes_json TEXT,
        mention_count INTEGER DEFAULT 1,
        first_seen DATE DEFAULT CURRENT_DATE,
        last_seen DATE DEFAULT CURRENT_DATE,
        UNIQUE(normalized_name, entity_type)
    );

    -- Entity aliases for resolution
    CREATE TABLE IF NOT EXISTS kg_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id INTEGER REFERENCES kg_entities(id),
        alias TEXT NOT NULL,
        normalized_alias TEXT NOT NULL,
        UNIQUE(normalized_alias, entity_id)
    );

    -- Relationships
    CREATE TABLE IF NOT EXISTS kg_relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER REFERENCES kg_entities(id),
        predicate TEXT NOT NULL,
        object_id INTEGER REFERENCES kg_entities(id),
        event_date DATE,
        confidence REAL DEFAULT 0.0,
        context TEXT,
        source_url TEXT,
        source_article_id INTEGER,
        metadata_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(subject_id, predicate, object_id, event_date)
    );

    -- Entity enrichment data from external sources
    CREATE TABLE IF NOT EXISTS kg_enrichment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id INTEGER REFERENCES kg_entities(id),
        source TEXT NOT NULL,
        data_json TEXT,
        enriched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(entity_id, source)
    );

    -- Entity tags for recruiter workflow
    CREATE TABLE IF NOT EXISTS kg_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id INTEGER REFERENCES kg_entities(id),
        tag TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(entity_id, tag)
    );

    -- Indexes
    CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(entity_type);
    CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(normalized_name);
    CREATE INDEX IF NOT EXISTS idx_kg_rel_subject ON kg_relationships(subject_id);
    CREATE INDEX IF NOT EXISTS idx_kg_rel_object ON kg_relationships(object_id);
    CREATE INDEX IF NOT EXISTS idx_kg_rel_predicate ON kg_relationships(predicate);
    CREATE INDEX IF NOT EXISTS idx_kg_rel_date ON kg_relationships(event_date DESC);
    CREATE INDEX IF NOT EXISTS idx_kg_aliases_alias ON kg_aliases(normalized_alias);
    CREATE INDEX IF NOT EXISTS idx_kg_enrichment_entity ON kg_enrichment(entity_id);
    CREATE INDEX IF NOT EXISTS idx_kg_tags_entity ON kg_tags(entity_id);
    CREATE INDEX IF NOT EXISTS idx_kg_tags_tag ON kg_tags(tag);
    """

    def __init__(self, db_path: str = None):
        if db_path:
            self.db_path = db_path
        else:
            self.db_path = str(settings.data_dir / "knowledge_graph.db")
            # Ensure directory exists
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        """Initialize database schema."""
        with self._connection() as conn:
            conn.executescript(self.SCHEMA)
            # Migration: add metadata_json column if it doesn't exist
            try:
                conn.execute("SELECT metadata_json FROM kg_relationships LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE kg_relationships ADD COLUMN metadata_json TEXT")

    def add_entity(
        self,
        name: str,
        entity_type: str,
        attributes: dict = None
    ) -> int:
        """Add or update an entity."""
        normalized = name.lower().strip()

        with self._connection() as conn:
            cursor = conn.execute("""
                SELECT id, mention_count FROM kg_entities
                WHERE normalized_name = ? AND entity_type = ?
            """, (normalized, entity_type))
            existing = cursor.fetchone()

            if existing:
                conn.execute("""
                    UPDATE kg_entities
                    SET mention_count = mention_count + 1,
                        last_seen = CURRENT_DATE,
                        attributes_json = COALESCE(?, attributes_json)
                    WHERE id = ?
                """, (json.dumps(attributes) if attributes else None, existing["id"]))
                return existing["id"]
            else:
                cursor = conn.execute("""
                    INSERT INTO kg_entities
                    (name, normalized_name, entity_type, attributes_json)
                    VALUES (?, ?, ?, ?)
                """, (name, normalized, entity_type, json.dumps(attributes) if attributes else None))
                return cursor.lastrowid

    def get_entity(self, name: str, entity_type: str = None) -> Optional[GraphEntity]:
        """Get an entity by name."""
        normalized = name.lower().strip()

        with self._connection() as conn:
            sql = "SELECT * FROM kg_entities WHERE normalized_name = ?"
            params = [normalized]
            if entity_type:
                sql += " AND entity_type = ?"
                params.append(entity_type)

            cursor = conn.execute(sql, params)
            row = cursor.fetchone()

            if row:
                return self._row_to_entity(row)
            return None

    def search_entities(self, query: str, entity_type: str = None, limit: int = 1000) -> List[GraphEntity]:
        """Search entities by name pattern."""
        pattern = f"%{query.lower()}%"

        with self._connection() as conn:
            sql = "SELECT * FROM kg_entities WHERE normalized_name LIKE ?"
            params = [pattern]
            if entity_type:
                sql += " AND entity_type = ?"
                params.append(entity_type)
            sql += f" ORDER BY mention_count DESC LIMIT {limit}"

            cursor = conn.execute(sql, params)
            return [self._row_to_entity(row) for row in cursor.fetchall()]

    def add_relationship(
        self,
        subject_name: str, subject_type: str,
        predicate: str,
        object_name: str, object_type: str,
        event_date: date = None,
        confidence: float = 0.0,
        context: str = "",
        source_url: str = "",
        metadata: dict = None
    ) -> Optional[int]:
        """Add a relationship between entities."""
        subject_id = self.add_entity(subject_name, subject_type)
        object_id = self.add_entity(object_name, object_type)

        with self._connection() as conn:
            try:
                cursor = conn.execute("""
                    INSERT INTO kg_relationships
                    (subject_id, predicate, object_id, event_date, confidence, context, source_url, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    subject_id, predicate, object_id,
                    event_date.isoformat() if event_date else None,
                    confidence, context, source_url,
                    json.dumps(metadata) if metadata else None
                ))

                logger.debug(
                    "relationship_added",
                    subject=subject_name,
                    predicate=predicate,
                    object=object_name
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                return None  # Duplicate

    def query(
        self,
        subject: str = None,
        predicate: str = None,
        obj: str = None,
        since_date: date = None,
        limit: int = 100
    ) -> List[GraphRelationship]:
        """Query relationships with filters."""
        with self._connection() as conn:
            sql = """
                SELECT
                    r.id, r.predicate, r.event_date, r.confidence, r.context, r.source_url, r.metadata_json,
                    s.id as s_id, s.name as s_name, s.normalized_name as s_norm,
                    s.entity_type as s_type, s.attributes_json as s_attrs,
                    s.mention_count as s_count, s.first_seen as s_first, s.last_seen as s_last,
                    o.id as o_id, o.name as o_name, o.normalized_name as o_norm,
                    o.entity_type as o_type, o.attributes_json as o_attrs,
                    o.mention_count as o_count, o.first_seen as o_first, o.last_seen as o_last
                FROM kg_relationships r
                JOIN kg_entities s ON r.subject_id = s.id
                JOIN kg_entities o ON r.object_id = o.id
                WHERE 1=1
            """
            params = []

            if subject:
                sql += " AND s.normalized_name LIKE ?"
                params.append(f"%{subject.lower()}%")
            if predicate:
                sql += " AND r.predicate = ?"
                params.append(predicate)
            if obj:
                sql += " AND o.normalized_name LIKE ?"
                params.append(f"%{obj.lower()}%")
            if since_date:
                # Include relationships with NULL dates OR dates >= since_date
                sql += " AND (r.event_date IS NULL OR r.event_date >= ?)"
                params.append(since_date.isoformat())

            sql += " ORDER BY r.event_date DESC, r.id DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [self._row_to_relationship(row) for row in cursor.fetchall()]

    def who_hired(self, company: str, since: date = None) -> List[GraphRelationship]:
        """Find people hired by a company."""
        return self.query(obj=company, predicate="HIRED_BY", since_date=since)

    def where_went(self, person: str) -> List[GraphRelationship]:
        """Find where a person went."""
        return self.query(subject=person, predicate="HIRED_BY")

    def acquisitions(self, since: date = None) -> List[GraphRelationship]:
        """Get recent acquisitions."""
        return self.query(predicate="ACQUIRED", since_date=since)

    def person_trajectory(self, person: str) -> List[GraphRelationship]:
        """Get full career trajectory of a person."""
        hired = self.query(subject=person, predicate="HIRED_BY")
        departed = self.query(subject=person, predicate="DEPARTED_FROM")
        return sorted(hired + departed, key=lambda r: r.event_date or date.min, reverse=True)

    def get_stats(self) -> dict:
        """Get graph statistics."""
        with self._connection() as conn:
            entities = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
            relationships = conn.execute("SELECT COUNT(*) FROM kg_relationships").fetchone()[0]

            entities_by_type = {}
            for row in conn.execute(
                "SELECT entity_type, COUNT(*) as cnt FROM kg_entities GROUP BY entity_type"
            ):
                entities_by_type[row["entity_type"]] = row["cnt"]

            rels_by_type = {}
            for row in conn.execute(
                "SELECT predicate, COUNT(*) as cnt FROM kg_relationships GROUP BY predicate"
            ):
                rels_by_type[row["predicate"]] = row["cnt"]

            return {
                "total_entities": entities,
                "total_relationships": relationships,
                "entities_by_type": entities_by_type,
                "relationships_by_type": rels_by_type,
            }

    def add_extraction_result(self, result, source_url: str = ""):
        """Add entities and relationships from an extraction result."""
        from ..extraction.interfaces import ExtractionResult

        if hasattr(result, 'entities'):
            for entity in result.entities:
                self.add_entity(entity.name, entity.entity_type, entity.attributes)

        # Get amounts from extraction result
        amounts = getattr(result, 'amounts', {}) or {}

        if hasattr(result, 'relationships'):
            for rel in result.relationships:
                # Build metadata dict with amounts relevant to this relationship type
                metadata = {}
                if amounts:
                    if rel.predicate == 'ACQUIRED' and amounts.get('acquisition'):
                        metadata['amount'] = amounts['acquisition']
                        if amounts.get('valuation'):
                            metadata['valuation'] = amounts['valuation']
                    elif rel.predicate == 'FUNDED_BY' and amounts.get('funding'):
                        metadata['amount'] = amounts['funding']
                        if amounts.get('valuation'):
                            metadata['valuation'] = amounts['valuation']
                    elif rel.predicate == 'LAID_OFF' and amounts.get('layoff_count'):
                        metadata['count'] = amounts['layoff_count']

                self.add_relationship(
                    subject_name=rel.subject,
                    subject_type=rel.subject_type,
                    predicate=rel.predicate,
                    object_name=rel.object,
                    object_type=rel.object_type,
                    event_date=rel.event_date,
                    confidence=rel.confidence,
                    context=rel.context,
                    source_url=source_url or getattr(result, 'source_url', ''),
                    metadata=metadata if metadata else None
                )

    def _row_to_entity(self, row) -> GraphEntity:
        """Convert database row to GraphEntity."""
        attrs = json.loads(row["attributes_json"]) if row["attributes_json"] else {}
        return GraphEntity(
            id=row["id"],
            name=row["name"],
            normalized_name=row["normalized_name"],
            entity_type=row["entity_type"],
            attributes=attrs,
            mention_count=row["mention_count"],
            first_seen=date.fromisoformat(row["first_seen"]) if row["first_seen"] else None,
            last_seen=date.fromisoformat(row["last_seen"]) if row["last_seen"] else None,
        )

    def _row_to_relationship(self, row) -> GraphRelationship:
        """Convert database row to GraphRelationship."""
        subject = GraphEntity(
            id=row["s_id"],
            name=row["s_name"],
            normalized_name=row["s_norm"],
            entity_type=row["s_type"],
            attributes=json.loads(row["s_attrs"]) if row["s_attrs"] else {},
            mention_count=row["s_count"],
            first_seen=date.fromisoformat(row["s_first"]) if row["s_first"] else None,
            last_seen=date.fromisoformat(row["s_last"]) if row["s_last"] else None,
        )
        object_entity = GraphEntity(
            id=row["o_id"],
            name=row["o_name"],
            normalized_name=row["o_norm"],
            entity_type=row["o_type"],
            attributes=json.loads(row["o_attrs"]) if row["o_attrs"] else {},
            mention_count=row["o_count"],
            first_seen=date.fromisoformat(row["o_first"]) if row["o_first"] else None,
            last_seen=date.fromisoformat(row["o_last"]) if row["o_last"] else None,
        )
        return GraphRelationship(
            id=row["id"],
            subject=subject,
            predicate=row["predicate"],
            object=object_entity,
            event_date=date.fromisoformat(row["event_date"]) if row["event_date"] else None,
            confidence=row["confidence"],
            context=row["context"] or "",
            source_url=row["source_url"] or "",
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    # === Enrichment Methods ===

    def add_enrichment(self, entity_id: int, source: str, data: dict) -> bool:
        """Add or update enrichment data for an entity."""
        with self._connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO kg_enrichment (entity_id, source, data_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(entity_id, source) DO UPDATE SET
                        data_json = excluded.data_json,
                        enriched_at = CURRENT_TIMESTAMP
                """, (entity_id, source, json.dumps(data)))
                logger.info("enrichment_added", entity_id=entity_id, source=source)
                return True
            except Exception as e:
                logger.error("enrichment_failed", error=str(e))
                return False

    def get_enrichment(self, entity_id: int, source: str = None) -> dict:
        """Get enrichment data for an entity."""
        with self._connection() as conn:
            if source:
                cursor = conn.execute("""
                    SELECT source, data_json, enriched_at
                    FROM kg_enrichment
                    WHERE entity_id = ? AND source = ?
                """, (entity_id, source))
                row = cursor.fetchone()
                if row:
                    return {
                        "source": row["source"],
                        "data": json.loads(row["data_json"]) if row["data_json"] else {},
                        "enriched_at": row["enriched_at"]
                    }
                return {}
            else:
                cursor = conn.execute("""
                    SELECT source, data_json, enriched_at
                    FROM kg_enrichment
                    WHERE entity_id = ?
                """, (entity_id,))
                result = {}
                for row in cursor.fetchall():
                    result[row["source"]] = {
                        "data": json.loads(row["data_json"]) if row["data_json"] else {},
                        "enriched_at": row["enriched_at"]
                    }
                return result

    def get_entity_by_id(self, entity_id: int) -> Optional[GraphEntity]:
        """Get an entity by ID."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM kg_entities WHERE id = ?",
                (entity_id,)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_entity(row)
            return None

    # === Tagging Methods ===

    def add_tag(self, entity_id: int, tag: str) -> bool:
        """Add a tag to an entity."""
        with self._connection() as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO kg_tags (entity_id, tag)
                    VALUES (?, ?)
                """, (entity_id, tag.lower().strip()))
                return True
            except Exception as e:
                logger.error("tag_add_failed", error=str(e))
                return False

    def remove_tag(self, entity_id: int, tag: str) -> bool:
        """Remove a tag from an entity."""
        with self._connection() as conn:
            try:
                conn.execute("""
                    DELETE FROM kg_tags
                    WHERE entity_id = ? AND tag = ?
                """, (entity_id, tag.lower().strip()))
                return True
            except Exception as e:
                logger.error("tag_remove_failed", error=str(e))
                return False

    def get_entity_tags(self, entity_id: int) -> List[str]:
        """Get all tags for an entity."""
        with self._connection() as conn:
            cursor = conn.execute("""
                SELECT tag FROM kg_tags WHERE entity_id = ?
            """, (entity_id,))
            return [row["tag"] for row in cursor.fetchall()]

    def get_entities_by_tag(self, tag: str) -> List[GraphEntity]:
        """Get all entities with a specific tag."""
        with self._connection() as conn:
            cursor = conn.execute("""
                SELECT e.* FROM kg_entities e
                JOIN kg_tags t ON e.id = t.entity_id
                WHERE t.tag = ?
                ORDER BY e.mention_count DESC
            """, (tag.lower().strip(),))
            return [self._row_to_entity(row) for row in cursor.fetchall()]

    def get_all_tags(self) -> List[dict]:
        """Get all tags with counts."""
        with self._connection() as conn:
            cursor = conn.execute("""
                SELECT tag, COUNT(*) as count
                FROM kg_tags
                GROUP BY tag
                ORDER BY count DESC
            """)
            return [{"tag": row["tag"], "count": row["count"]} for row in cursor.fetchall()]
