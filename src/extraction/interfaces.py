"""Interface definitions for entity and relationship extraction."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import date


@dataclass
class Entity:
    """An extracted entity."""
    name: str
    entity_type: str  # "company", "person", "investor"
    normalized_name: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0

    def __post_init__(self):
        if not self.normalized_name:
            self.normalized_name = self.name.lower().strip()


@dataclass
class Relationship:
    """A relationship between two entities."""
    subject: str           # Entity name
    subject_type: str      # Entity type
    predicate: str         # ACQUIRED, HIRED_BY, FUNDED_BY, etc.
    object: str            # Entity name
    object_type: str       # Entity type
    event_date: Optional[date] = None
    confidence: float = 0.0
    context: str = ""      # Supporting text


@dataclass
class ExtractionResult:
    """Result of extracting from an article."""
    entities: List[Entity]
    relationships: List[Relationship]
    event_date: Optional[date] = None
    amounts: Dict[str, str] = field(default_factory=dict)
    source_url: str = ""
    raw_response: str = ""


class ExtractorInterface:
    """Interface for entity/relationship extraction."""

    async def extract(self, title: str, content: str) -> ExtractionResult:
        """Extract entities and relationships from an article."""
        raise NotImplementedError

    async def extract_batch(
        self,
        articles: List[dict],
        max_concurrent: int = 5
    ) -> List[ExtractionResult]:
        """Extract from multiple articles."""
        raise NotImplementedError
