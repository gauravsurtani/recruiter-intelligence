"""LLM-powered entity and relationship extractor."""

import json
import asyncio
from typing import List, Optional
from datetime import date
from pathlib import Path

import yaml
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from .interfaces import (
    ExtractorInterface, ExtractionResult,
    Entity, Relationship
)
from .llm_client import LLMClient
from ..config.settings import settings

logger = structlog.get_logger()


class LLMExtractor(ExtractorInterface):
    """LLM-powered entity and relationship extractor."""

    EXTRACTION_PROMPT = """Extract entities and relationships from this business news article.

ENTITY EXTRACTION RULES (STRICT):
- Company names: Use official name only (e.g., "Google" not "Google Inc." or "the tech giant")
- Person names: Full name only (e.g., "John Smith" not "CEO John Smith")
- Investor names: Fund name (e.g., "Sequoia Capital" not "Sequoia")
- DO NOT include sentence fragments, descriptions, or partial phrases
- DO NOT include generic terms like "the company", "the startup", "the firm"
- DO NOT include titles in names - put titles in the "role" field
- Names must be 2-50 characters, no longer

ENTITIES to extract:
- Companies (type: "company") - startups, corporations, tech companies
- People (type: "person") - executives, founders, employees with named roles
- Investors (type: "investor") - VCs, PE firms, angel investors, investment banks

RELATIONSHIPS to extract:
- ACQUIRED: Company acquired another company
- FUNDED_BY: Company received funding from investor
- HIRED_BY: Person joined a company (new hire or promotion)
- DEPARTED_FROM: Person left a company (resignation, layoff, retirement)
- FOUNDED: Person founded a company
- CEO_OF: Person is CEO of company
- CTO_OF: Person is CTO of company
- CFO_OF: Person is CFO of company
- INVESTED_IN: Investor invested in company
- LAID_OFF: Company laid off employees (object = "employees" with count in context)

CONFIDENCE GUIDELINES:
- 0.95: Explicitly stated fact with clear attribution
- 0.85: Strongly implied or from reliable source
- 0.70: Mentioned but details unclear
- Below 0.70: Do not include

Return ONLY valid JSON (no markdown, no explanation):
{{
  "entities": [
    {{"name": "Exact Official Name", "type": "company|person|investor", "role": "CEO|CTO|VP Engineering|etc or null"}}
  ],
  "relationships": [
    {{
      "subject": "Entity name (must match an entity above)",
      "predicate": "RELATIONSHIP_TYPE",
      "object": "Entity name (must match an entity above)",
      "context": "Exact quote or close paraphrase from article",
      "confidence": 0.70-1.0
    }}
  ],
  "event_date": "YYYY-MM-DD or null",
  "amounts": {{
    "funding": "$XM or null",
    "acquisition": "$XM or null",
    "valuation": "$XB or null",
    "layoff_count": "number or null"
  }}
}}

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content}"""

    # Patterns that indicate bad entity extraction
    BAD_ENTITY_PATTERNS = [
        'says', 'said', 'announced', 'reported', 'according', 'stated',
        'the company', 'the startup', 'the firm', 'the investor',
        'in a', 'for a', 'with a', 'to a', 'from a',
        'which', 'that', 'this', 'their', 'its',
        'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december'
    ]

    # Common company suffixes to normalize
    COMPANY_SUFFIXES = [
        ' Inc.', ' Inc', ' Corp.', ' Corp', ' LLC', ' Ltd.', ' Ltd',
        ' Corporation', ' Company', ' Co.', ' Co', ' PLC', ' LP', ' LLP'
    ]

    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient()
        self.system_prompt = """You are an expert at extracting structured business intelligence from news articles.

CRITICAL RULES:
1. Extract ONLY explicitly named entities - no pronouns, no generic terms
2. Company names must be the official name (Google, not Alphabet's Google)
3. Person names must be full names (first + last minimum)
4. Return ONLY valid JSON - no markdown code blocks, no explanations
5. If unsure about an entity, omit it rather than guess
6. Confidence below 0.70 = do not include

You extract data for a recruiting intelligence system that tracks:
- Company acquisitions (hiring signals)
- Funding rounds (growth/hiring signals)
- Executive movements (available talent signals)
- Layoffs (displaced talent signals)"""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10)
    )
    async def extract(self, title: str, content: str) -> ExtractionResult:
        """Extract entities and relationships using LLM."""
        prompt = self.EXTRACTION_PROMPT.format(
            title=title,
            content=content[:4000]  # Limit content length
        )

        try:
            response = await self.llm_client.complete(
                prompt=prompt,
                system=self.system_prompt
            )
            return self._parse_response(response)
        except Exception as e:
            logger.error("extraction_failed", error=str(e))
            return ExtractionResult(entities=[], relationships=[])

    def _validate_entity(self, name: str, entity_type: str) -> bool:
        """Validate extracted entity quality."""
        if not name:
            return False

        name_lower = name.lower().strip()

        # Reject if too short or too long
        if len(name) < 2 or len(name) > 50:
            return False

        # Reject if contains bad patterns (sentence fragments)
        for pattern in self.BAD_ENTITY_PATTERNS:
            if pattern in name_lower:
                logger.debug("entity_rejected_pattern", name=name, pattern=pattern)
                return False

        # Reject single common words
        common_words = {'company', 'startup', 'firm', 'investor', 'ceo', 'cto', 'employee'}
        if name_lower in common_words:
            return False

        # Person names should have at least 2 parts (first + last)
        if entity_type == 'person':
            parts = name.split()
            if len(parts) < 2:
                logger.debug("entity_rejected_single_name", name=name)
                return False

        return True

    def _normalize_entity_name(self, name: str, entity_type: str) -> str:
        """Normalize entity name for consistency."""
        name = name.strip()

        # Remove company suffixes for cleaner matching
        if entity_type == 'company':
            for suffix in self.COMPANY_SUFFIXES:
                if name.endswith(suffix):
                    name = name[:-len(suffix)].strip()
                    break

        return name

    def _parse_response(self, response: str) -> ExtractionResult:
        """Parse LLM response into structured result."""
        try:
            # Handle markdown code blocks
            if '```json' in response:
                start = response.find('```json') + 7
                end = response.find('```', start)
                if end > start:
                    response = response[start:end]
            elif '```' in response:
                start = response.find('```') + 3
                end = response.find('```', start)
                if end > start:
                    response = response[start:end]

            # Find JSON in response
            start = response.find('{')
            end = response.rfind('}') + 1
            if start < 0 or end <= start:
                return ExtractionResult(entities=[], relationships=[])

            data = json.loads(response[start:end])

            # Parse and validate entities
            entities = []
            for e in data.get("entities", []):
                name = e.get("name", "")
                entity_type = e.get("type", "unknown")

                if not self._validate_entity(name, entity_type):
                    continue

                normalized_name = self._normalize_entity_name(name, entity_type)
                entities.append(Entity(
                    name=normalized_name,
                    entity_type=entity_type,
                    attributes={"role": e.get("role")} if e.get("role") else {},
                    confidence=0.9
                ))

            # Parse relationships (only if both entities are valid)
            entity_names = {e.name.lower() for e in entities}
            relationships = []
            for r in data.get("relationships", []):
                subject = r.get("subject", "")
                predicate = r.get("predicate", "")
                obj = r.get("object", "")
                confidence = r.get("confidence", 0.8)

                # Skip low confidence relationships
                if confidence < 0.70:
                    continue

                if subject and predicate and obj:
                    # Normalize names to match entities
                    subject_norm = self._normalize_entity_name(subject, "unknown")
                    obj_norm = self._normalize_entity_name(obj, "unknown")

                    relationships.append(Relationship(
                        subject=subject_norm,
                        subject_type=self._get_entity_type(subject_norm, entities),
                        predicate=predicate,
                        object=obj_norm,
                        object_type=self._get_entity_type(obj_norm, entities),
                        confidence=confidence,
                        context=r.get("context", "")
                    ))

            # Parse date
            event_date = None
            if data.get("event_date"):
                try:
                    event_date = date.fromisoformat(data["event_date"])
                except ValueError:
                    pass

            logger.info(
                "extraction_complete",
                entities=len(entities),
                relationships=len(relationships)
            )

            return ExtractionResult(
                entities=entities,
                relationships=relationships,
                event_date=event_date,
                amounts=data.get("amounts", {}),
                raw_response=response
            )

        except json.JSONDecodeError:
            logger.warning("json_parse_failed", response=response[:200])
            return ExtractionResult(entities=[], relationships=[])

    def _get_entity_type(self, name: str, entities: List[Entity]) -> str:
        """Look up entity type from extracted entities."""
        for entity in entities:
            if entity.name.lower() == name.lower():
                return entity.entity_type
        return "unknown"

    async def extract_batch(
        self,
        articles: List[dict],
        max_concurrent: int = 5
    ) -> List[ExtractionResult]:
        """Extract from multiple articles with concurrency control."""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_one(article):
            async with semaphore:
                result = await self.extract(
                    article.get("title", ""),
                    article.get("content", "") or article.get("summary", "")
                )
                result.source_url = article.get("url", "")
                return result

        results = await asyncio.gather(
            *[extract_one(a) for a in articles],
            return_exceptions=True
        )

        # Filter out exceptions
        return [r for r in results if isinstance(r, ExtractionResult)]
