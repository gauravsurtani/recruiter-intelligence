"""Interface definitions for article classification."""

from dataclasses import dataclass, field
from typing import List
from enum import Enum


class EventType(Enum):
    """Types of events we track."""
    ACQUISITION = "acquisition"
    FUNDING = "funding"
    EXECUTIVE_MOVE = "executive_move"
    LAYOFF = "layoff"
    IPO = "ipo"
    OTHER = "other"


@dataclass
class ClassificationResult:
    """Result of classifying an article."""
    primary_type: EventType
    all_types: List[EventType]
    confidence: float  # 0.0 to 1.0
    matched_keywords: List[str]
    is_high_signal: bool  # True if not OTHER and confidence > threshold


@dataclass
class QualityScore:
    """Quality assessment for extraction potential."""
    overall_score: float  # 0.0 to 1.0
    has_company_names: bool
    has_person_names: bool
    has_amounts: bool
    has_dates: bool
    extraction_potential: str  # "high", "medium", "low"


class ClassifierInterface:
    """Interface for article classification."""

    def classify(self, title: str, content: str) -> ClassificationResult:
        """Classify a single article."""
        raise NotImplementedError

    def classify_batch(self, articles: List[dict]) -> List[ClassificationResult]:
        """Classify multiple articles."""
        raise NotImplementedError


class QualityEvaluatorInterface:
    """Interface for quality evaluation."""

    def evaluate(self, title: str, content: str) -> QualityScore:
        """Evaluate extraction quality potential."""
        raise NotImplementedError
