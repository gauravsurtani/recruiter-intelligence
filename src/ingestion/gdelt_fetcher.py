"""GDELT news fetcher for historical and supplementary news data."""

from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class GDELTArticle:
    """Article from GDELT."""
    url: str
    title: str
    source_domain: str
    date: datetime
    themes: List[str]
    organizations: List[str]
    people: List[str]
    locations: List[str]
    tone: float  # Sentiment: -100 to +100
    language: str = "en"


class GDELTFetcher:
    """Fetch news from GDELT Project API."""

    # Relevant GDELT themes for startup intelligence
    RELEVANT_THEMES = [
        'ECON_BANKRUPTCY',
        'ECON_COST',
        'ECON_INVEST',
        'BUS_ACQUISITION',
        'BUS_MERGER',
        'GOV_SEC',  # SEC related
        'LEADER',  # Leadership changes
        'UNEMPLOYMENT',  # Layoffs
    ]

    # Search queries for startup news
    STARTUP_QUERIES = [
        'startup funding',
        'series a funding',
        'series b funding',
        'seed round',
        'venture capital',
        'tech acquisition',
        'tech merger',
        'ceo appointed',
        'layoffs tech',
    ]

    def __init__(self):
        self._gd = None

    def _init_gdelt(self):
        """Lazy initialize GDELT library."""
        if self._gd is not None:
            return

        try:
            import gdelt
            self._gd = gdelt.gdelt(version=2)
            logger.info("gdelt_initialized")
        except ImportError:
            logger.warning("gdelt_not_installed")
            raise ImportError("gdelt not installed")

    def fetch_startup_news(
        self,
        days_back: int = 7,
        max_results: int = 250,
        queries: List[str] = None,
    ) -> List[GDELTArticle]:
        """Fetch startup-related news from GDELT."""
        self._init_gdelt()

        queries = queries or self.STARTUP_QUERIES[:3]  # Limit queries
        all_articles = []

        for query in queries:
            try:
                articles = self._search_query(query, days_back, max_results // len(queries))
                all_articles.extend(articles)
            except Exception as e:
                logger.warning("gdelt_query_failed", query=query, error=str(e))

        # Deduplicate by URL
        seen_urls = set()
        unique_articles = []
        for article in all_articles:
            if article.url not in seen_urls:
                seen_urls.add(article.url)
                unique_articles.append(article)

        logger.info("gdelt_fetch_complete", total=len(unique_articles))
        return unique_articles

    def _search_query(
        self,
        query: str,
        days_back: int,
        max_results: int,
    ) -> List[GDELTArticle]:
        """Search GDELT for a specific query."""
        try:
            results = self._gd.Search(
                [query],
                table='gkg',  # Global Knowledge Graph for richer data
                coverage=True,
            )

            if results is None or len(results) == 0:
                return []

            articles = []
            for idx, row in results.head(max_results).iterrows():
                try:
                    article = self._parse_gkg_row(row)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.debug("gdelt_row_parse_error", error=str(e))

            logger.debug("gdelt_query_results", query=query, count=len(articles))
            return articles

        except Exception as e:
            logger.warning("gdelt_search_error", query=query, error=str(e))
            return []

    def _parse_gkg_row(self, row) -> Optional[GDELTArticle]:
        """Parse a GDELT GKG row into GDELTArticle."""
        try:
            # Extract URL
            url = row.get('DocumentIdentifier', '')
            if not url:
                return None

            # Extract date
            date_str = str(row.get('DATE', ''))
            if len(date_str) >= 8:
                date = datetime.strptime(date_str[:8], '%Y%m%d')
            else:
                date = datetime.now()

            # Parse themes
            themes = self._parse_semicolon_list(row.get('V2Themes', ''))

            # Parse organizations
            orgs = self._parse_semicolon_list(row.get('V2Organizations', ''))

            # Parse people
            people = self._parse_semicolon_list(row.get('V2Persons', ''))

            # Parse locations
            locations = self._parse_semicolon_list(row.get('V2Locations', ''))

            # Get tone (sentiment)
            tone_str = row.get('V2Tone', '0')
            tone = float(str(tone_str).split(',')[0]) if tone_str else 0

            # Get source domain
            source = row.get('SourceCommonName', '')

            return GDELTArticle(
                url=url,
                title=url.split('/')[-1].replace('-', ' ')[:100],  # Approximate title from URL
                source_domain=source,
                date=date,
                themes=themes[:10],
                organizations=orgs[:10],
                people=people[:10],
                locations=locations[:5],
                tone=tone,
            )

        except Exception as e:
            logger.debug("gdelt_parse_error", error=str(e))
            return None

    def _parse_semicolon_list(self, value: str) -> List[str]:
        """Parse GDELT semicolon-separated values."""
        if not value or value == 'nan':
            return []
        items = str(value).split(';')
        return [item.split(',')[0].strip() for item in items if item.strip()]

    def fetch_historical(
        self,
        query: str,
        start_date: datetime,
        end_date: datetime = None,
        max_results: int = 500,
    ) -> List[GDELTArticle]:
        """Fetch historical news for a specific query and date range."""
        self._init_gdelt()

        end_date = end_date or datetime.now()

        try:
            # GDELT DOC API for historical data
            results = self._gd.Search(
                [query],
                table='gkg',
                coverage=True,
            )

            if results is None:
                return []

            articles = []
            for idx, row in results.head(max_results).iterrows():
                article = self._parse_gkg_row(row)
                if article and start_date <= article.date <= end_date:
                    articles.append(article)

            logger.info(
                "gdelt_historical_fetch",
                query=query,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                count=len(articles),
            )
            return articles

        except Exception as e:
            logger.error("gdelt_historical_error", error=str(e))
            return []

    def filter_by_themes(
        self,
        articles: List[GDELTArticle],
        themes: List[str] = None,
    ) -> List[GDELTArticle]:
        """Filter articles by relevant GDELT themes."""
        themes = themes or self.RELEVANT_THEMES

        filtered = []
        for article in articles:
            for theme in themes:
                if any(theme.lower() in t.lower() for t in article.themes):
                    filtered.append(article)
                    break

        return filtered

    def to_raw_articles(self, gdelt_articles: List[GDELTArticle]):
        """Convert GDELT articles to RawArticle format for pipeline."""
        from .interfaces import RawArticle
        import hashlib

        raw_articles = []
        for g in gdelt_articles:
            content_hash = hashlib.sha256(g.url.encode()).hexdigest()

            raw_articles.append(RawArticle(
                source=f"gdelt:{g.source_domain}",
                url=g.url,
                title=g.title,
                content=None,  # GDELT doesn't provide full content
                summary=f"Organizations: {', '.join(g.organizations[:3])}. Themes: {', '.join(g.themes[:3])}",
                published_at=g.date,
                fetched_at=datetime.now(),
                content_hash=content_hash,
                feed_priority=2,  # Lower priority than direct RSS
            ))

        return raw_articles


async def fetch_gdelt_news(days_back: int = 7, max_results: int = 250) -> List[GDELTArticle]:
    """Async wrapper for GDELT fetching."""
    fetcher = GDELTFetcher()
    return fetcher.fetch_startup_news(days_back, max_results)
