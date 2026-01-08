"""RSS Feed Fetcher with async support, rate limiting, and retries."""

import asyncio
import hashlib
import time
from datetime import datetime, timedelta
from typing import List, Optional, Callable

import aiohttp
import feedparser
from tenacity import retry, stop_after_attempt, wait_exponential
import structlog

from .interfaces import FeedConfig, RawArticle, FetcherInterface
from ..config.settings import settings

logger = structlog.get_logger()


class RSSFetcher(FetcherInterface):
    """Async RSS feed fetcher with rate limiting and retries."""

    def __init__(self, on_fetch_complete: Callable = None):
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(5)  # Max concurrent fetches
        self.last_fetch_time = {}
        self.on_fetch_complete = on_fetch_complete  # Callback for stats

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=settings.fetch_timeout_seconds),
            headers={"User-Agent": "RecruiterIntelBot/1.0"}
        )
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10)
    )
    async def fetch_feed(self, config: FeedConfig) -> List[RawArticle]:
        """Fetch articles from a single feed."""
        async with self.semaphore:
            await self._rate_limit(config.name)
            start_time = time.time()

            try:
                async with self.session.get(config.url) as response:
                    content = await response.text()

                feed = feedparser.parse(content)
                articles = []

                for entry in feed.entries:
                    article = self._parse_entry(entry, config)
                    if article:
                        articles.append(article)

                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "feed_fetched",
                    feed=config.name,
                    articles=len(articles),
                    time_ms=elapsed_ms
                )

                # Callback for stats
                if self.on_fetch_complete:
                    self.on_fetch_complete(
                        feed_name=config.name,
                        articles=len(articles),
                        fetch_time_ms=elapsed_ms
                    )

                return articles

            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.error("feed_fetch_failed", feed=config.name, error=str(e))

                # Callback for failure
                if self.on_fetch_complete:
                    self.on_fetch_complete(
                        feed_name=config.name,
                        error=str(e),
                        fetch_time_ms=elapsed_ms
                    )
                raise

    async def fetch_all(
        self,
        configs: List[FeedConfig],
        since: datetime = None
    ) -> List[RawArticle]:
        """Fetch from all feeds concurrently."""
        if since is None:
            since = datetime.utcnow() - timedelta(days=1)

        enabled_configs = [c for c in configs if c.enabled]

        tasks = [self.fetch_feed(config) for config in enabled_configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_articles = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "feed_fetch_exception",
                    feed=enabled_configs[i].name,
                    error=str(result)
                )
                continue

            if isinstance(result, list):
                # Filter by date
                filtered = [a for a in result
                           if a.published_at is None or a.published_at >= since]
                all_articles.extend(filtered)

        # Deduplicate by URL
        seen_urls = set()
        unique_articles = []
        for article in all_articles:
            if article.url not in seen_urls:
                seen_urls.add(article.url)
                unique_articles.append(article)

        logger.info(
            "all_feeds_fetched",
            total=len(unique_articles),
            feeds=len(enabled_configs)
        )
        return unique_articles

    def _parse_entry(self, entry, config: FeedConfig) -> Optional[RawArticle]:
        """Parse a feed entry into a RawArticle."""
        url = getattr(entry, 'link', None)
        if not url:
            return None

        title = getattr(entry, 'title', '')
        summary = getattr(entry, 'summary', '')

        # Get content if available
        content = summary
        if hasattr(entry, 'content') and entry.content:
            content = entry.content[0].get('value', summary)

        # Parse date
        published_at = None
        for attr in ['published_parsed', 'updated_parsed']:
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    published_at = datetime(*parsed[:6])
                    break
                except (TypeError, ValueError):
                    pass

        # Generate content hash
        content_hash = hashlib.sha256(
            f"{url}|{title}".encode()
        ).hexdigest()[:32]

        return RawArticle(
            source=config.name,
            url=url,
            title=title,
            content=content,
            summary=summary[:500] if summary else '',
            published_at=published_at,
            content_hash=content_hash,
            feed_priority=config.priority.value
        )

    async def _rate_limit(self, feed_name: str):
        """Enforce rate limiting per feed."""
        min_interval = 1.0 / settings.fetch_rate_limit_per_second
        last_time = self.last_fetch_time.get(feed_name, 0)
        now = asyncio.get_event_loop().time()
        elapsed = now - last_time

        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)

        self.last_fetch_time[feed_name] = asyncio.get_event_loop().time()
