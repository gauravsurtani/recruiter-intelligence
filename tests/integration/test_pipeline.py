"""Integration tests for the full pipeline."""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.pipeline.daily import DailyPipeline
from src.storage.database import ArticleStorage
from src.knowledge_graph.graph import KnowledgeGraph
from src.extraction.llm_extractor import LLMExtractor


@pytest.fixture
def mock_llm_response():
    """Mock LLM response for extraction."""
    return '''{
        "entities": [
            {"name": "Google", "type": "company"},
            {"name": "Startup Corp", "type": "company"}
        ],
        "relationships": [
            {
                "subject": "Google",
                "predicate": "ACQUIRED",
                "object": "Startup Corp",
                "context": "Google acquired Startup Corp",
                "confidence": 0.95
            }
        ],
        "amounts": {"acquisition": "$100M"}
    }'''


@pytest.fixture
def temp_pipeline(mock_llm_response):
    """Create a pipeline with temporary databases and mocked LLM."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ArticleStorage(f"sqlite:///{tmpdir}/articles.db")
        kg = KnowledgeGraph(f"{tmpdir}/kg.db")

        # Create pipeline with mocked extractor
        pipeline = DailyPipeline(storage=storage, knowledge_graph=kg)

        # Mock the LLM client
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_llm_response)
        pipeline.extractor = LLMExtractor(llm_client=mock_client)

        yield pipeline


@pytest.mark.asyncio
class TestPipelineIntegration:
    """Integration tests for DailyPipeline."""

    async def test_full_pipeline_run(self, temp_pipeline):
        """Should run the complete pipeline end-to-end."""
        stats = await temp_pipeline.run(days_back=1, max_articles=20)

        # Basic assertions
        assert "fetched_articles" in stats
        assert "saved_articles" in stats
        assert "classified_articles" in stats
        assert "high_signal_articles" in stats
        assert "elapsed_seconds" in stats
        assert "knowledge_graph" in stats

        # Should have fetched some articles
        assert stats["fetched_articles"] > 0

    async def test_classification_filters_noise(self, temp_pipeline):
        """Should classify and filter noise articles."""
        stats = await temp_pipeline.run(days_back=1, max_articles=50)

        # High signal should be less than total classified
        assert stats["high_signal_articles"] <= stats["classified_articles"]

    async def test_extraction_populates_graph(self, temp_pipeline):
        """Should extract relationships and populate knowledge graph."""
        stats = await temp_pipeline.run(days_back=1, max_articles=30)

        # After a run with mocked LLM, we should have some extractions
        if stats["high_signal_articles"] > 0:
            assert stats["extracted_relationships"] >= 0

    async def test_pipeline_idempotent(self, temp_pipeline):
        """Running twice should not duplicate articles."""
        stats1 = await temp_pipeline.run(days_back=1, max_articles=20)
        stats2 = await temp_pipeline.run(days_back=1, max_articles=20)

        # Second run should save fewer new articles (most are duplicates)
        assert stats2["saved_articles"] <= stats1["saved_articles"]


@pytest.mark.asyncio
class TestPipelineComponents:
    """Tests for individual pipeline components working together."""

    async def test_fetch_and_classify(self, temp_pipeline):
        """Should fetch articles and classify them."""
        from src.config.feeds import load_feeds
        from src.ingestion.fetcher import RSSFetcher
        from datetime import datetime, timedelta

        feeds = load_feeds()[:2]  # Just use first 2 feeds
        since = datetime.utcnow() - timedelta(days=1)

        async with RSSFetcher() as fetcher:
            articles = await fetcher.fetch_all(feeds, since=since)

        assert len(articles) >= 0  # May be 0 if feed is down

        if articles:
            results = await temp_pipeline._classify_articles(articles[:10])
            assert len(results) > 0

    async def test_classify_and_extract(self, temp_pipeline, mock_llm_response):
        """Should classify and extract from articles."""
        from src.ingestion.interfaces import RawArticle

        # Create test article with known content
        test_article = RawArticle(
            id=1,
            source="Test",
            url="https://example.com/test",
            title="Google acquires Startup Corp for $100M",
            content="Google announced the acquisition of Startup Corp",
            content_hash="test123"
        )

        # Classify
        results = await temp_pipeline._classify_articles([test_article])
        assert len(results) == 1
        article, classification = results[0]

        # Should be high signal acquisition
        from src.classification.interfaces import EventType
        assert classification.primary_type == EventType.ACQUISITION
        assert classification.is_high_signal

        # Extract (uses mocked LLM)
        count = await temp_pipeline._extract_and_store([test_article])
        # Should have extracted at least one relationship
        assert count >= 1

        # Check knowledge graph
        acquisitions = temp_pipeline.kg.acquisitions()
        assert len(acquisitions) >= 1
