"""Entity enrichment module for adding external data to entities."""

from .interfaces import EnrichmentResult, CompanyEnrichment, PersonEnrichment
from .enrichment_service import EnrichmentService

__all__ = [
    "EnrichmentResult",
    "CompanyEnrichment",
    "PersonEnrichment",
    "EnrichmentService",
]
