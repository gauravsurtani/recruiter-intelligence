"""Unit tests for storage module."""

import pytest
from datetime import datetime

# Add src to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.ingestion.interfaces import RawArticle
from src.storage.database import ArticleStorage


class TestArticleStorage:
    """Tests for ArticleStorage."""

    def test_save_and_retrieve_article(self, temp_db, sample_article):
        """Should save and retrieve an article."""
        storage = ArticleStorage(temp_db)

        article_id = storage.save_article(sample_article)
        assert article_id is not None
        assert article_id > 0

        retrieved = storage.get_by_url(sample_article.url)
        assert retrieved is not None
        assert retrieved.title == sample_article.title
        assert retrieved.source == sample_article.source

    def test_duplicate_detection_by_url(self, temp_db, sample_article):
        """Should detect duplicate by URL."""
        storage = ArticleStorage(temp_db)

        first_id = storage.save_article(sample_article)
        assert first_id is not None

        # Try to save same article again
        second_id = storage.save_article(sample_article)
        assert second_id is None, "Duplicate should return None"

    def test_duplicate_detection_by_hash(self, temp_db, sample_article):
        """Should detect duplicate by content hash."""
        storage = ArticleStorage(temp_db)

        storage.save_article(sample_article)
        assert storage.exists(sample_article.content_hash) is True
        assert storage.exists("nonexistent_hash") is False

    def test_get_unprocessed_articles(self, temp_db, sample_article):
        """Should get unprocessed articles."""
        storage = ArticleStorage(temp_db)

        storage.save_article(sample_article)
        unprocessed = storage.get_unprocessed(limit=10)

        assert len(unprocessed) == 1
        assert unprocessed[0].url == sample_article.url

    def test_mark_processed(self, temp_db, sample_article):
        """Should mark article as processed."""
        storage = ArticleStorage(temp_db)

        article_id = storage.save_article(sample_article)
        storage.mark_processed(
            article_id,
            event_type="funding",
            confidence=0.95,
            is_high_signal=True
        )

        unprocessed = storage.get_unprocessed()
        assert len(unprocessed) == 0

    def test_save_multiple_articles(self, temp_db):
        """Should save multiple articles and return count."""
        storage = ArticleStorage(temp_db)

        articles = [
            RawArticle(
                source="Test",
                url=f"https://example.com/article-{i}",
                title=f"Article {i}",
                content_hash=f"hash{i}"
            )
            for i in range(5)
        ]

        saved_count = storage.save_articles(articles)
        assert saved_count == 5

        stats = storage.get_stats()
        assert stats["total_articles"] == 5
        assert stats["unprocessed_articles"] == 5

    def test_get_high_signal_articles(self, temp_db, sample_article):
        """Should get high-signal articles."""
        storage = ArticleStorage(temp_db)

        article_id = storage.save_article(sample_article)
        storage.mark_processed(
            article_id,
            event_type="acquisition",
            is_high_signal=True
        )

        high_signal = storage.get_high_signal_articles()
        assert len(high_signal) == 1

    def test_stats(self, temp_db, sample_article):
        """Should return correct stats."""
        storage = ArticleStorage(temp_db)

        storage.save_article(sample_article)
        stats = storage.get_stats()

        assert stats["total_articles"] == 1
        assert stats["processed_articles"] == 0
        assert stats["unprocessed_articles"] == 1
        assert stats["high_signal_articles"] == 0
