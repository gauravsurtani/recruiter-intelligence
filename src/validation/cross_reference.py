"""Cross-reference news articles with SEC Form D filings."""

import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Try importing rapidfuzz, fall back to difflib
try:
    from rapidfuzz import fuzz
    USING_RAPIDFUZZ = True
except ImportError:
    from difflib import SequenceMatcher
    USING_RAPIDFUZZ = False
    logger.info("using_difflib", msg="Install rapidfuzz for 10x faster matching")


@dataclass
class FundingEvent:
    """A funding event extracted from news or Form D."""
    company_name: str
    amount: Optional[float]
    date: datetime
    round_type: Optional[str] = None
    source_type: str = "news"  # "news" or "form_d"
    source_url: Optional[str] = None
    confidence: float = 0.8


@dataclass
class CrossRefMatch:
    """A match between news and Form D filing."""
    news: FundingEvent
    form_d: FundingEvent
    name_similarity: float
    date_diff_days: int
    amount_match: bool
    combined_confidence: float


class CrossReferencer:
    """Cross-reference news funding data with SEC Form D filings."""

    def __init__(
        self,
        name_threshold: float = 0.85,
        date_window_days: int = 30,
        amount_tolerance: float = 0.20,
    ):
        self.name_threshold = name_threshold
        self.date_window_days = date_window_days
        self.amount_tolerance = amount_tolerance

    def normalize_company_name(self, name: str) -> str:
        """Normalize company name for comparison."""
        name = name.lower().strip()

        # Remove common suffixes
        suffixes = [
            ' inc.', ' inc', ' corp.', ' corp', ' llc', ' ltd.', ' ltd',
            ' corporation', ' company', ' co.', ' co', ' plc', ' lp', ' llp',
            ' holdings', ' group', ' technologies', ' technology', ' systems',
            ' solutions', ' software', ' labs', ' ai', ' io',
        ]
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()

        # Remove special characters
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()

        return name

    def name_similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two company names."""
        n1 = self.normalize_company_name(name1)
        n2 = self.normalize_company_name(name2)

        if USING_RAPIDFUZZ:
            return fuzz.ratio(n1, n2) / 100.0
        else:
            return SequenceMatcher(None, n1, n2).ratio()

    def amounts_compatible(self, amount1: Optional[float], amount2: Optional[float]) -> bool:
        """Check if two amounts are compatible within tolerance."""
        if amount1 is None or amount2 is None:
            return True  # Can't verify, assume compatible

        if amount1 == 0 or amount2 == 0:
            return True

        # Calculate relative difference
        diff = abs(amount1 - amount2) / max(amount1, amount2)
        return diff <= self.amount_tolerance

    def match_news_to_form_d(
        self,
        news_events: List[FundingEvent],
        form_d_events: List[FundingEvent],
    ) -> List[CrossRefMatch]:
        """Match news funding events to Form D filings."""
        matches = []

        for news in news_events:
            best_match = None
            best_score = 0

            for form_d in form_d_events:
                # Check name similarity
                name_sim = self.name_similarity(news.company_name, form_d.company_name)
                if name_sim < self.name_threshold:
                    continue

                # Check date proximity
                date_diff = abs((news.date - form_d.date).days)
                if date_diff > self.date_window_days:
                    continue

                # Check amount compatibility
                amount_match = self.amounts_compatible(news.amount, form_d.amount)

                # Calculate combined score
                score = self._calculate_match_score(name_sim, date_diff, amount_match)

                if score > best_score:
                    best_score = score
                    best_match = CrossRefMatch(
                        news=news,
                        form_d=form_d,
                        name_similarity=name_sim,
                        date_diff_days=date_diff,
                        amount_match=amount_match,
                        combined_confidence=score,
                    )

            if best_match:
                matches.append(best_match)
                logger.debug(
                    "cross_ref_match",
                    news_company=news.company_name,
                    form_d_company=best_match.form_d.company_name,
                    similarity=f"{best_match.name_similarity:.2f}",
                    confidence=f"{best_match.combined_confidence:.2f}",
                )

        logger.info(
            "cross_reference_complete",
            news_events=len(news_events),
            form_d_events=len(form_d_events),
            matches=len(matches),
        )

        return matches

    def _calculate_match_score(
        self,
        name_sim: float,
        date_diff: int,
        amount_match: bool,
    ) -> float:
        """Calculate combined confidence score for a match."""
        # Base score from name similarity
        score = name_sim * 0.5

        # Date proximity bonus (closer = better)
        date_score = max(0, (self.date_window_days - date_diff) / self.date_window_days)
        score += date_score * 0.3

        # Amount match bonus
        if amount_match:
            score += 0.2

        return min(1.0, score)

    def boost_confidence(self, matches: List[CrossRefMatch]) -> dict:
        """Generate confidence boosts for matched events."""
        boosts = {}

        for match in matches:
            company = self.normalize_company_name(match.news.company_name)

            # Calculate boosted confidence
            # News + Form D agreement → 90-98% confidence
            base_confidence = 0.90
            if match.amount_match and match.news.amount and match.form_d.amount:
                base_confidence = 0.95
            if match.name_similarity > 0.95:
                base_confidence = min(0.98, base_confidence + 0.03)

            boosts[company] = {
                "original_confidence": match.news.confidence,
                "boosted_confidence": base_confidence,
                "form_d_amount": match.form_d.amount,
                "news_amount": match.news.amount,
                "form_d_date": match.form_d.date.isoformat() if match.form_d.date else None,
                "verified": True,
            }

            logger.info(
                "confidence_boosted",
                company=company,
                original=f"{match.news.confidence:.2f}",
                boosted=f"{base_confidence:.2f}",
            )

        return boosts

    def find_unmatched_form_d(
        self,
        form_d_events: List[FundingEvent],
        matches: List[CrossRefMatch],
    ) -> List[FundingEvent]:
        """Find Form D filings that didn't match any news articles."""
        matched_form_d = {m.form_d for m in matches}
        return [f for f in form_d_events if f not in matched_form_d]

    def find_unverified_news(
        self,
        news_events: List[FundingEvent],
        matches: List[CrossRefMatch],
    ) -> List[FundingEvent]:
        """Find news events that didn't match any Form D filing."""
        matched_news = {m.news for m in matches}
        return [n for n in news_events if n not in matched_news]


def create_funding_event_from_relationship(rel, source_date: datetime = None) -> Optional[FundingEvent]:
    """Create FundingEvent from a knowledge graph relationship."""
    if rel.predicate not in ("FUNDED_BY", "RAISED_FUNDING"):
        return None

    # Extract amount from metadata
    amount = None
    if hasattr(rel, 'metadata') and rel.metadata:
        amount = rel.metadata.get('amount')

    return FundingEvent(
        company_name=rel.subject if rel.predicate == "FUNDED_BY" else rel.object,
        amount=amount,
        date=rel.event_date or source_date or datetime.now(),
        round_type=rel.metadata.get('round_type') if hasattr(rel, 'metadata') and rel.metadata else None,
        source_type="news",
        source_url=rel.source_url if hasattr(rel, 'source_url') else None,
        confidence=rel.confidence if hasattr(rel, 'confidence') else 0.8,
    )


def create_funding_event_from_form_d(filing) -> FundingEvent:
    """Create FundingEvent from a Form D filing."""
    return FundingEvent(
        company_name=filing.company_name,
        amount=filing.total_amount or filing.amount_sold,
        date=filing.filing_date,
        round_type=None,  # Form D doesn't specify round type
        source_type="form_d",
        source_url=filing.source_url,
        confidence=0.95,  # High confidence - legal source
    )
