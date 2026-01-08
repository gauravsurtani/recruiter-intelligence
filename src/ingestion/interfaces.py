"""Interface definitions for data ingestion."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class FeedPriority(Enum):
    """Priority levels for feeds - affects processing order."""
    HIGH = 0      # Crunchbase, VentureBeat - high signal
    MEDIUM = 1    # TechCrunch, SEC - medium signal
    LOW = 2       # Hacker News, Forbes - noisy


@dataclass
class FeedConfig:
    """Configuration for a single feed."""
    name: str
    url: str
    priority: FeedPriority
    enabled: bool = True
    fetch_interval_minutes: int = 60
    event_types: List[str] = field(default_factory=list)


@dataclass
class RawArticle:
    """An article fetched from a feed."""
    id: Optional[int] = None
    source: str = ""
    url: str = ""
    title: str = ""
    content: str = ""
    summary: str = ""
    published_at: Optional[datetime] = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    content_hash: str = ""  # For deduplication
    feed_priority: int = 1

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "source": self.source,
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "summary": self.summary,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "content_hash": self.content_hash,
            "feed_priority": self.feed_priority,
        }


class FetcherInterface:
    """Interface for feed fetching."""

    async def fetch_feed(self, config: FeedConfig) -> List[RawArticle]:
        """Fetch articles from a single feed."""
        raise NotImplementedError

    async def fetch_all(self, configs: List[FeedConfig]) -> List[RawArticle]:
        """Fetch from all configured feeds."""
        raise NotImplementedError


class StorageInterface:
    """Interface for article storage."""

    def save_article(self, article: RawArticle) -> Optional[int]:
        """Save article, return ID or None if duplicate."""
        raise NotImplementedError

    def save_articles(self, articles: List[RawArticle]) -> int:
        """Save multiple articles, return count of new articles saved."""
        raise NotImplementedError

    def get_unprocessed(self, limit: int = 100) -> List[RawArticle]:
        """Get articles not yet processed."""
        raise NotImplementedError

    def mark_processed(self, article_id: int) -> None:
        """Mark article as processed."""
        raise NotImplementedError

    def get_by_url(self, url: str) -> Optional[RawArticle]:
        """Get article by URL."""
        raise NotImplementedError

    def exists(self, content_hash: str) -> bool:
        """Check if article with given hash exists."""
        raise NotImplementedError
