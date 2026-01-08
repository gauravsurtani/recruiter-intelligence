"""Source validation and confidence scoring for extracted data."""

from typing import Dict, List, Optional
from dataclasses import dataclass
from collections import defaultdict
import structlog

from ..knowledge_graph.graph import KnowledgeGraph

logger = structlog.get_logger()


@dataclass
class SourceQuality:
    """Quality metrics for a news source."""
    name: str
    domain: str
    tier: int  # 1=highest quality, 3=lowest
    base_confidence: float  # 0.0-1.0


# Source quality tiers
SOURCE_TIERS: Dict[str, SourceQuality] = {
    # Tier 1: Primary sources with high editorial standards
    'bloomberg.com': SourceQuality('Bloomberg', 'bloomberg.com', 1, 0.95),
    'wsj.com': SourceQuality('Wall Street Journal', 'wsj.com', 1, 0.95),
    'reuters.com': SourceQuality('Reuters', 'reuters.com', 1, 0.95),
    'sec.gov': SourceQuality('SEC EDGAR', 'sec.gov', 1, 1.0),  # Official filings
    'crunchbase.com': SourceQuality('Crunchbase', 'crunchbase.com', 1, 0.90),
    'news.crunchbase.com': SourceQuality('Crunchbase News', 'news.crunchbase.com', 1, 0.90),

    # Tier 2: Reputable tech publications
    'techcrunch.com': SourceQuality('TechCrunch', 'techcrunch.com', 2, 0.85),
    'geekwire.com': SourceQuality('GeekWire', 'geekwire.com', 2, 0.85),
    'venturebeat.com': SourceQuality('VentureBeat', 'venturebeat.com', 2, 0.85),
    'techmeme.com': SourceQuality('Techmeme', 'techmeme.com', 2, 0.80),
    'axios.com': SourceQuality('Axios', 'axios.com', 2, 0.85),
    'theverge.com': SourceQuality('The Verge', 'theverge.com', 2, 0.80),
    'wired.com': SourceQuality('Wired', 'wired.com', 2, 0.80),
    'fortune.com': SourceQuality('Fortune', 'fortune.com', 2, 0.85),
    'forbes.com': SourceQuality('Forbes', 'forbes.com', 2, 0.80),

    # Tier 3: Secondary sources, aggregators, press releases
    'prnewswire.com': SourceQuality('PR Newswire', 'prnewswire.com', 3, 0.70),
    'businesswire.com': SourceQuality('Business Wire', 'businesswire.com', 3, 0.70),
    'siliconangle.com': SourceQuality('SiliconANGLE', 'siliconangle.com', 3, 0.75),
    'inc.com': SourceQuality('Inc', 'inc.com', 3, 0.75),
    'fastcompany.com': SourceQuality('Fast Company', 'fastcompany.com', 3, 0.75),
    'arstechnica.com': SourceQuality('Ars Technica', 'arstechnica.com', 3, 0.75),
    'news.ycombinator.com': SourceQuality('Hacker News', 'news.ycombinator.com', 3, 0.60),
}

# Default for unknown sources
DEFAULT_SOURCE = SourceQuality('Unknown', 'unknown', 3, 0.50)


class SourceValidator:
    """Validates and scores data sources."""

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    def get_source_quality(self, url: str) -> SourceQuality:
        """Get quality metrics for a source URL."""
        if not url:
            return DEFAULT_SOURCE

        # Extract domain from URL
        try:
            parts = url.split('/')
            if len(parts) >= 3:
                domain = parts[2].lower()
                # Remove www. prefix
                if domain.startswith('www.'):
                    domain = domain[4:]

                # Look up in tiers
                if domain in SOURCE_TIERS:
                    return SOURCE_TIERS[domain]

                # Try subdomain matching
                for known_domain, quality in SOURCE_TIERS.items():
                    if domain.endswith(known_domain):
                        return quality
        except Exception:
            pass

        return DEFAULT_SOURCE

    def calculate_entity_confidence(self, entity_id: int) -> Dict:
        """Calculate confidence score for an entity based on sources."""
        with self.kg._connection() as conn:
            # Get all relationships involving this entity
            cursor = conn.execute("""
                SELECT DISTINCT source_url
                FROM kg_relationships
                WHERE (subject_id = ? OR object_id = ?) AND source_url IS NOT NULL
            """, (entity_id, entity_id))

            sources = []
            for row in cursor:
                quality = self.get_source_quality(row['source_url'])
                sources.append(quality)

            if not sources:
                return {
                    'confidence': 0.5,
                    'source_count': 0,
                    'tier1_sources': 0,
                    'tier2_sources': 0,
                    'tier3_sources': 0,
                }

            # Calculate metrics
            tier1 = sum(1 for s in sources if s.tier == 1)
            tier2 = sum(1 for s in sources if s.tier == 2)
            tier3 = sum(1 for s in sources if s.tier == 3)

            # Weighted confidence: more sources = higher confidence
            # Tier 1 sources count more
            base_confidence = max(s.base_confidence for s in sources)
            source_bonus = min(0.15, len(sources) * 0.03)  # Up to 15% bonus for multiple sources
            tier1_bonus = min(0.10, tier1 * 0.05)  # Up to 10% bonus for tier 1 sources

            final_confidence = min(1.0, base_confidence + source_bonus + tier1_bonus)

            return {
                'confidence': final_confidence,
                'source_count': len(sources),
                'tier1_sources': tier1,
                'tier2_sources': tier2,
                'tier3_sources': tier3,
            }

    def calculate_relationship_confidence(self, rel_id: int) -> float:
        """Calculate confidence for a specific relationship."""
        with self.kg._connection() as conn:
            cursor = conn.execute("""
                SELECT source_url, confidence FROM kg_relationships WHERE id = ?
            """, (rel_id,))
            row = cursor.fetchone()

            if not row:
                return 0.5

            source_quality = self.get_source_quality(row['source_url'])
            base_confidence = row['confidence'] or 0.8

            # Adjust based on source quality
            adjusted = base_confidence * source_quality.base_confidence
            return round(adjusted, 2)

    def get_validation_report(self) -> Dict:
        """Generate a validation report for all data."""
        with self.kg._connection() as conn:
            # Source distribution
            cursor = conn.execute("""
                SELECT source_url, COUNT(*) as cnt
                FROM kg_relationships
                WHERE source_url IS NOT NULL
                GROUP BY source_url
            """)

            source_counts = defaultdict(int)
            tier_counts = {1: 0, 2: 0, 3: 0}

            for row in cursor:
                quality = self.get_source_quality(row['source_url'])
                source_counts[quality.name] += row['cnt']
                tier_counts[quality.tier] += row['cnt']

            # Entity coverage
            total_entities = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
            enriched = conn.execute("SELECT COUNT(DISTINCT entity_id) FROM kg_enrichment").fetchone()[0]

            # Multi-source entities (more validated)
            cursor = conn.execute("""
                SELECT e.id, e.name, COUNT(DISTINCT r.source_url) as source_count
                FROM kg_entities e
                JOIN kg_relationships r ON (e.id = r.subject_id OR e.id = r.object_id)
                WHERE r.source_url IS NOT NULL
                GROUP BY e.id
                HAVING source_count > 1
            """)
            multi_source_entities = len(list(cursor))

            return {
                'total_entities': total_entities,
                'enriched_entities': enriched,
                'enrichment_coverage': round(enriched / total_entities * 100, 1) if total_entities > 0 else 0,
                'multi_source_entities': multi_source_entities,
                'source_distribution': dict(source_counts),
                'tier_distribution': {
                    'tier1_primary': tier_counts[1],
                    'tier2_reputable': tier_counts[2],
                    'tier3_secondary': tier_counts[3],
                },
                'data_quality_score': self._calculate_quality_score(tier_counts),
            }

    def _calculate_quality_score(self, tier_counts: Dict[int, int]) -> float:
        """Calculate overall data quality score (0-100)."""
        total = sum(tier_counts.values())
        if total == 0:
            return 0

        # Weight: Tier 1 = 100%, Tier 2 = 70%, Tier 3 = 40%
        weighted = (tier_counts[1] * 1.0 + tier_counts[2] * 0.7 + tier_counts[3] * 0.4)
        score = (weighted / total) * 100
        return round(score, 1)


def add_more_diverse_feeds() -> List[Dict]:
    """Suggest additional feeds for better coverage of smaller companies."""
    return [
        # Regional tech news
        {
            "name": "Austin Business Journal Tech",
            "url": "https://www.bizjournals.com/austin/news/technology/rss.xml",
            "reason": "Covers Austin tech startups",
        },
        {
            "name": "Built In SF",
            "url": "https://www.builtin.com/san-francisco/rss.xml",
            "reason": "Covers SF startups and hiring",
        },
        {
            "name": "Built In NYC",
            "url": "https://www.builtin.com/new-york-city/rss.xml",
            "reason": "Covers NYC startups and hiring",
        },
        # Startup-specific
        {
            "name": "EU Startups",
            "url": "https://www.eu-startups.com/feed/",
            "reason": "European startup coverage",
        },
        {
            "name": "YC News",
            "url": "https://blog.ycombinator.com/feed/",
            "reason": "Y Combinator company news",
        },
        # Industry specific
        {
            "name": "Fierce Healthcare",
            "url": "https://www.fiercehealthcare.com/rss/xml",
            "reason": "Healthcare/biotech companies",
        },
        {
            "name": "Finextra",
            "url": "https://www.finextra.com/rss/headlines.aspx",
            "reason": "Fintech coverage",
        },
        # Job/hiring signals
        {
            "name": "Layoffs.fyi RSS",
            "url": "https://layoffs.fyi/feed.xml",
            "reason": "Layoff tracking for displaced talent",
        },
    ]
