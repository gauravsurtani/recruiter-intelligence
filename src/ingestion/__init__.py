"""Data ingestion - fetching and parsing RSS feeds."""

from .interfaces import FeedConfig, FeedPriority, RawArticle, FetcherInterface, StorageInterface
from .fetcher import RSSFetcher

__all__ = [
    "FeedConfig", "FeedPriority", "RawArticle",
    "FetcherInterface", "StorageInterface", "RSSFetcher"
]
