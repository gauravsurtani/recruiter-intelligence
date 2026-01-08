"""Unit tests for ingestion module."""

import pytest
from datetime import datetime, timedelta

# Add src to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.ingestion.interfaces import FeedConfig, FeedPriority, RawArticle


class TestFeedConfig:
    """Tests for FeedConfig."""

    def test_feed_config_creation(self):
        """Should create a valid FeedConfig."""
        config = FeedConfig(
            name="Test Feed",
            url="https://example.com/feed.xml",
            priority=FeedPriority.HIGH
        )
        assert config.name == "Test Feed"
        assert config.url == "https://example.com/feed.xml"
        assert config.priority == FeedPriority.HIGH
        assert config.enabled is True
        assert config.fetch_interval_minutes == 60

    def test_feed_priority_values(self):
        """Priority values should be correct."""
        assert FeedPriority.HIGH.value == 0
        assert FeedPriority.MEDIUM.value == 1
        assert FeedPriority.LOW.value == 2


class TestRawArticle:
    """Tests for RawArticle."""

    def test_raw_article_creation(self):
        """Should create a valid RawArticle."""
        article = RawArticle(
            source="Test Source",
            url="https://example.com/article",
            title="Test Title"
        )
        assert article.source == "Test Source"
        assert article.url == "https://example.com/article"
        assert article.title == "Test Title"
        assert article.fetched_at is not None

    def test_raw_article_to_dict(self):
        """Should convert to dict."""
        article = RawArticle(
            source="Test",
            url="https://example.com",
            title="Test"
        )
        data = article.to_dict()
        assert data["source"] == "Test"
        assert data["url"] == "https://example.com"
        assert "fetched_at" in data


@pytest.mark.asyncio
class TestRSSFetcher:
    """Tests for RSSFetcher - requires network."""

    async def test_fetch_single_feed(self, sample_feed_config):
        """Should fetch articles from a real feed."""
        from src.ingestion.fetcher import RSSFetcher

        async with RSSFetcher() as fetcher:
            articles = await fetcher.fetch_feed(sample_feed_config)

        assert len(articles) > 0
        assert all(a.url for a in articles)
        assert all(a.title for a in articles)
        assert all(a.source == "TechCrunch" for a in articles)

    async def test_deduplication_by_url(self, sample_feed_config):
        """Should deduplicate articles by URL."""
        from src.ingestion.fetcher import RSSFetcher

        async with RSSFetcher() as fetcher:
            articles = await fetcher.fetch_all([sample_feed_config, sample_feed_config])

        urls = [a.url for a in articles]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"

    async def test_content_hash_generated(self, sample_feed_config):
        """Should generate content hash for each article."""
        from src.ingestion.fetcher import RSSFetcher

        async with RSSFetcher() as fetcher:
            articles = await fetcher.fetch_feed(sample_feed_config)

        assert all(a.content_hash for a in articles)
        assert all(len(a.content_hash) == 32 for a in articles)
