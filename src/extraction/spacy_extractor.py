"""spaCy-based NER extraction for fast first-pass entity extraction."""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Lazy load spacy to avoid import errors if not installed
_nlp = None


def get_nlp():
    """Lazy load spaCy model."""
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load("en_core_web_lg")
            logger.info("spacy_model_loaded", model="en_core_web_lg")
        except OSError:
            logger.warning("spacy_model_not_found", msg="Run: python -m spacy download en_core_web_lg")
            try:
                _nlp = spacy.load("en_core_web_sm")
                logger.info("spacy_model_loaded", model="en_core_web_sm (fallback)")
            except OSError:
                raise ImportError("No spaCy model found. Run: python -m spacy download en_core_web_lg")
        except ImportError:
            raise ImportError("spaCy not installed. Run: pip install spacy")
    return _nlp


@dataclass
class SpacyEntities:
    """Entities extracted by spaCy NER."""
    money: List[str]
    organizations: List[str]
    people: List[str]
    dates: List[str]
    locations: List[str]

    # Parsed values
    amounts: List[float]  # Parsed dollar amounts
    round_type: Optional[str] = None  # seed, series_a, etc.

    # Routing decision
    needs_llm: bool = False
    llm_reason: Optional[str] = None


class SpacyExtractor:
    """Fast NER extraction using spaCy, routes ambiguous cases to LLM."""

    # Keywords for event classification
    FUNDING_KEYWORDS = {
        'seed': ['seed', 'pre-seed', 'angel'],
        'series_a': ['series a', 'series-a'],
        'series_b': ['series b', 'series-b'],
        'series_c': ['series c', 'series-c', 'series d', 'series e'],
        'funding': ['funding', 'raises', 'raised', 'secures', 'closes', 'investment'],
    }

    ACQUISITION_KEYWORDS = ['acquires', 'acquired', 'acquisition', 'merger', 'merged', 'buys', 'bought', 'takeover']
    EXECUTIVE_KEYWORDS = ['ceo', 'cto', 'cfo', 'coo', 'chief', 'president', 'vp', 'vice president', 'joins', 'appointed', 'hired', 'names']
    LAYOFF_KEYWORDS = ['layoff', 'layoffs', 'laid off', 'cuts jobs', 'workforce reduction', 'downsizing']

    def __init__(self):
        self.nlp = None  # Lazy load

    def _ensure_nlp(self):
        """Ensure spaCy model is loaded."""
        if self.nlp is None:
            self.nlp = get_nlp()

    def extract(self, text: str) -> SpacyEntities:
        """Extract entities from text using spaCy NER."""
        self._ensure_nlp()

        doc = self.nlp(text[:10000])  # Limit text length

        # Extract named entities
        money = []
        organizations = []
        people = []
        dates = []
        locations = []

        for ent in doc.ents:
            if ent.label_ == "MONEY":
                money.append(ent.text)
            elif ent.label_ == "ORG":
                organizations.append(ent.text)
            elif ent.label_ == "PERSON":
                people.append(ent.text)
            elif ent.label_ == "DATE":
                dates.append(ent.text)
            elif ent.label_ in ("GPE", "LOC"):
                locations.append(ent.text)

        # Parse dollar amounts
        amounts = self._parse_amounts(money)

        # Detect funding round type
        round_type = self._detect_round_type(text.lower())

        # Determine if LLM is needed
        needs_llm, reason = self._needs_llm(
            text=text,
            orgs=organizations,
            people=people,
            amounts=amounts,
            round_type=round_type,
        )

        return SpacyEntities(
            money=money,
            organizations=organizations,
            people=people,
            dates=dates,
            locations=locations,
            amounts=amounts,
            round_type=round_type,
            needs_llm=needs_llm,
            llm_reason=reason,
        )

    def _parse_amounts(self, money_strings: List[str]) -> List[float]:
        """Parse dollar amounts from MONEY entities."""
        amounts = []
        for m in money_strings:
            amount = self._parse_single_amount(m)
            if amount is not None:
                amounts.append(amount)
        return sorted(amounts, reverse=True)

    def _parse_single_amount(self, text: str) -> Optional[float]:
        """Parse a single money string to float."""
        text = text.lower().replace('$', '').replace(',', '').strip()

        # Handle millions/billions
        multipliers = {
            'billion': 1_000_000_000,
            'bn': 1_000_000_000,
            'b': 1_000_000_000,
            'million': 1_000_000,
            'mm': 1_000_000,
            'mn': 1_000_000,
            'm': 1_000_000,
            'thousand': 1_000,
            'k': 1_000,
        }

        for suffix, mult in multipliers.items():
            if suffix in text:
                # Extract number before multiplier
                match = re.search(r'([\d.]+)\s*' + suffix, text)
                if match:
                    try:
                        return float(match.group(1)) * mult
                    except ValueError:
                        pass

        # Try direct parse
        try:
            return float(text)
        except ValueError:
            return None

    def _detect_round_type(self, text: str) -> Optional[str]:
        """Detect funding round type from text."""
        for round_type, keywords in self.FUNDING_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return round_type
        return None

    def _needs_llm(
        self,
        text: str,
        orgs: List[str],
        people: List[str],
        amounts: List[float],
        round_type: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        """Determine if LLM is needed for relationship extraction."""
        text_lower = text.lower()

        # Check event type
        is_acquisition = any(kw in text_lower for kw in self.ACQUISITION_KEYWORDS)
        is_executive = any(kw in text_lower for kw in self.EXECUTIVE_KEYWORDS)
        is_layoff = any(kw in text_lower for kw in self.LAYOFF_KEYWORDS)
        is_funding = round_type is not None or any(kw in text_lower for kw in self.FUNDING_KEYWORDS.get('funding', []))

        # Acquisition: need LLM to determine acquirer vs acquired
        if is_acquisition:
            if len(orgs) >= 2:
                return True, "acquisition_multiple_orgs"
            return True, "acquisition_unclear_parties"

        # Executive move: need LLM for role/company assignment
        if is_executive and people:
            if len(orgs) > 1:
                return True, "executive_multiple_orgs"
            if len(people) > 1:
                return True, "executive_multiple_people"
            # Single person + single org might be OK
            if len(orgs) == 1 and len(people) == 1:
                return False, None

        # Layoff: need LLM for details
        if is_layoff:
            return True, "layoff_details"

        # Funding: can skip LLM if clear
        if is_funding:
            # Clear case: single org + amount + round type
            if len(orgs) == 1 and amounts and round_type:
                return False, None
            # Multiple orgs could be company + investor
            if len(orgs) >= 2:
                return True, "funding_multiple_orgs"

        # No clear event detected
        if not (is_acquisition or is_executive or is_layoff or is_funding):
            return True, "unclear_event_type"

        # Default: need LLM
        return True, "complex_extraction"

    def create_simple_extraction(self, entities: SpacyEntities, text: str):
        """Create extraction result for simple cases without LLM."""
        from .interfaces import ExtractionResult, ExtractedEntity, ExtractedRelationship

        result_entities = []
        result_relationships = []

        # Add organization entities
        for org in entities.organizations[:5]:  # Limit
            if len(org) > 2:  # Skip very short
                result_entities.append(ExtractedEntity(
                    name=org,
                    entity_type="company",
                    confidence=0.75,  # Lower than LLM
                ))

        # Add person entities
        for person in entities.people[:5]:
            if len(person) > 2:
                result_entities.append(ExtractedEntity(
                    name=person,
                    entity_type="person",
                    confidence=0.75,
                ))

        # If funding round detected with single company
        if entities.round_type and len(entities.organizations) == 1 and entities.amounts:
            company = entities.organizations[0]
            amount = entities.amounts[0]

            result_relationships.append(ExtractedRelationship(
                subject=company,
                subject_type="company",
                predicate="FUNDED_BY",
                object="Undisclosed",
                object_type="investor",
                confidence=0.70,
                context=f"{entities.round_type} round: ${amount:,.0f}",
                metadata={"amount": amount, "round_type": entities.round_type},
            ))

        return ExtractionResult(
            entities=result_entities,
            relationships=result_relationships,
            source_type="spacy_ner",
            confidence=0.70,
        )


class HybridExtractor:
    """Hybrid extractor: spaCy first-pass, LLM for complex cases."""

    def __init__(self, llm_extractor=None):
        self.spacy = SpacyExtractor()
        self.llm = llm_extractor

    async def extract(self, title: str, content: str):
        """Extract entities using hybrid approach."""
        text = f"{title}\n\n{content}" if content else title

        # First pass: spaCy
        entities = self.spacy.extract(text)

        # Simple case: return spaCy result
        if not entities.needs_llm:
            logger.debug("spacy_extraction_sufficient", orgs=len(entities.organizations))
            return self.spacy.create_simple_extraction(entities, text)

        # Complex case: route to LLM
        if self.llm:
            logger.debug("routing_to_llm", reason=entities.llm_reason)
            return await self.llm.extract(title, content, pre_extracted=entities)

        # No LLM available, return spaCy best-effort
        return self.spacy.create_simple_extraction(entities, text)
