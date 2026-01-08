"""Pytest configuration and shared fixtures."""

import pytest
import tempfile
import os
from pathlib import Path

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def temp_db():
    """Provide a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield f"sqlite:///{db_path}"
    # Cleanup
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def sample_feed_config():
    """Provide a sample feed configuration."""
    from src.ingestion.interfaces import FeedConfig, FeedPriority
    return FeedConfig(
        name="TechCrunch",
        url="https://techcrunch.com/feed/",
        priority=FeedPriority.MEDIUM,
        event_types=["funding", "acquisition", "startup"]
    )


@pytest.fixture
def sample_article():
    """Provide a sample RawArticle."""
    from datetime import datetime
    from src.ingestion.interfaces import RawArticle
    return RawArticle(
        source="TechCrunch",
        url="https://techcrunch.com/2024/01/01/test-article/",
        title="Test Company Raises $50M Series B",
        content="Test Company announced today that it has raised $50 million in Series B funding led by Sequoia Capital.",
        summary="Test Company raises $50M",
        published_at=datetime(2024, 1, 1, 12, 0, 0),
        content_hash="abc123def456",
        feed_priority=1
    )
