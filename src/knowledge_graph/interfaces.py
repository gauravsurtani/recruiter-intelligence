"""Interface definitions for knowledge graph operations."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import date


@dataclass
class GraphEntity:
    """An entity in the knowledge graph."""
    id: int
    name: str
    normalized_name: str
    entity_type: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    mention_count: int = 1
    first_seen: date = None
    last_seen: date = None


@dataclass
class GraphRelationship:
    """A relationship in the knowledge graph."""
    id: int
    subject: GraphEntity
    predicate: str
    object: GraphEntity
    event_date: Optional[date] = None
    confidence: float = 0.0
    context: str = ""
    source_url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)  # Amounts, deal terms, etc.


class KnowledgeGraphInterface:
    """Interface for knowledge graph operations."""

    # Entity operations
    def add_entity(self, name: str, entity_type: str, attributes: dict = None) -> int:
        """Add or update an entity. Returns entity ID."""
        raise NotImplementedError

    def get_entity(self, name: str, entity_type: str = None) -> Optional[GraphEntity]:
        """Get an entity by name."""
        raise NotImplementedError

    def search_entities(self, query: str, entity_type: str = None) -> List[GraphEntity]:
        """Search entities by name pattern."""
        raise NotImplementedError

    # Relationship operations
    def add_relationship(
        self,
        subject_name: str, subject_type: str,
        predicate: str,
        object_name: str, object_type: str,
        event_date: date = None,
        confidence: float = 0.0,
        context: str = "",
        source_url: str = ""
    ) -> Optional[int]:
        """Add a relationship. Returns ID or None if duplicate."""
        raise NotImplementedError

    # Query operations
    def query(
        self,
        subject: str = None,
        predicate: str = None,
        obj: str = None,
        since_date: date = None,
        limit: int = 100
    ) -> List[GraphRelationship]:
        """Query relationships with filters."""
        raise NotImplementedError

    # High-level queries
    def who_hired(self, company: str, since: date = None) -> List[GraphRelationship]:
        """Find people hired by a company."""
        raise NotImplementedError

    def where_went(self, person: str) -> List[GraphRelationship]:
        """Find where a person went."""
        raise NotImplementedError

    def acquisitions(self, since: date = None) -> List[GraphRelationship]:
        """Get recent acquisitions."""
        raise NotImplementedError

    def person_trajectory(self, person: str) -> List[GraphRelationship]:
        """Get full career trajectory of a person."""
        raise NotImplementedError

    def get_stats(self) -> dict:
        """Get graph statistics."""
        raise NotImplementedError


class EntityResolverInterface:
    """Interface for entity resolution (deduplication)."""

    def resolve(self, name: str, entity_type: str) -> str:
        """Resolve a name to its canonical form."""
        raise NotImplementedError

    def merge(self, name1: str, name2: str, entity_type: str) -> None:
        """Merge two entities as the same."""
        raise NotImplementedError

    def add_alias(self, canonical: str, alias: str, entity_type: str) -> None:
        """Add an alias for an entity."""
        raise NotImplementedError
