"""Entity resolution for deduplication."""

import sqlite3
from typing import Optional, Dict, Set
from contextlib import contextmanager
from pathlib import Path

import structlog

from .interfaces import EntityResolverInterface
from ..config.settings import settings

logger = structlog.get_logger()


class EntityResolver(EntityResolverInterface):
    """Resolves entity names to canonical forms using aliases."""

    # Known company name variations
    COMPANY_ALIASES: Dict[str, Set[str]] = {
        "google": {"alphabet", "google llc", "google inc"},
        "meta": {"facebook", "meta platforms"},
        "amazon": {"amazon.com", "amazon inc"},
        "microsoft": {"msft", "microsoft corp"},
        "apple": {"apple inc", "apple computer"},
    }

    def __init__(self, db_path: str = None):
        if db_path:
            self.db_path = db_path
        else:
            self.db_path = str(settings.data_dir / "knowledge_graph.db")
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
        """Initialize entities and aliases tables."""
        with self._connection() as conn:
            # Create entities table if not exists
            conn.execute("""
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
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kg_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER REFERENCES kg_entities(id),
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    UNIQUE(normalized_alias, entity_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_aliases_alias ON kg_aliases(normalized_alias)")

    def resolve(self, name: str, entity_type: str) -> str:
        """Resolve a name to its canonical form."""
        normalized = name.lower().strip()

        # Check built-in aliases first
        for canonical, aliases in self.COMPANY_ALIASES.items():
            if normalized in aliases or normalized == canonical:
                return canonical

        # Check database aliases
        with self._connection() as conn:
            cursor = conn.execute("""
                SELECT e.name
                FROM kg_aliases a
                JOIN kg_entities e ON a.entity_id = e.id
                WHERE a.normalized_alias = ? AND e.entity_type = ?
            """, (normalized, entity_type))
            row = cursor.fetchone()
            if row:
                return row["name"]

        # No alias found, return original
        return name

    def merge(self, name1: str, name2: str, entity_type: str) -> None:
        """Merge two entities as the same (make name2 an alias of name1)."""
        self.add_alias(name1, name2, entity_type)

    def add_alias(self, canonical: str, alias: str, entity_type: str) -> None:
        """Add an alias for an entity."""
        canonical_norm = canonical.lower().strip()
        alias_norm = alias.lower().strip()

        with self._connection() as conn:
            # Find or create canonical entity
            cursor = conn.execute("""
                SELECT id FROM kg_entities
                WHERE normalized_name = ? AND entity_type = ?
            """, (canonical_norm, entity_type))
            row = cursor.fetchone()

            if not row:
                # Create the entity
                cursor = conn.execute("""
                    INSERT INTO kg_entities (name, normalized_name, entity_type)
                    VALUES (?, ?, ?)
                """, (canonical, canonical_norm, entity_type))
                entity_id = cursor.lastrowid
            else:
                entity_id = row["id"]

            # Add alias
            try:
                conn.execute("""
                    INSERT INTO kg_aliases (entity_id, alias, normalized_alias)
                    VALUES (?, ?, ?)
                """, (entity_id, alias, alias_norm))
                logger.debug("alias_added", canonical=canonical, alias=alias)
            except sqlite3.IntegrityError:
                pass  # Alias already exists

    def get_aliases(self, name: str, entity_type: str) -> Set[str]:
        """Get all known aliases for an entity."""
        normalized = name.lower().strip()

        with self._connection() as conn:
            # Get entity ID
            cursor = conn.execute("""
                SELECT id FROM kg_entities
                WHERE normalized_name = ? AND entity_type = ?
            """, (normalized, entity_type))
            row = cursor.fetchone()

            if not row:
                return set()

            # Get aliases
            cursor = conn.execute("""
                SELECT alias FROM kg_aliases WHERE entity_id = ?
            """, (row["id"],))

            return {r["alias"] for r in cursor.fetchall()}
