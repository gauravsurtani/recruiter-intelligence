"""Feed management - CRUD operations for RSS feeds."""

import json
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

import aiohttp
import feedparser
import structlog

from .feeds import load_feeds
from ..storage.database import ArticleStorage

logger = structlog.get_logger()

# Default config path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "feeds.json"

# Suggested feeds for one-click add
SUGGESTED_FEEDS = [
    # Regional
    {
        "name": "Built In SF",
        "url": "https://www.builtin.com/san-francisco/rss.xml",
        "category": "Regional",
        "description": "San Francisco startups and hiring",
        "priority": 1,
        "event_types": ["funding", "startup", "executive_move"],
    },
    {
        "name": "Built In NYC",
        "url": "https://www.builtin.com/new-york-city/rss.xml",
        "category": "Regional",
        "description": "New York City tech scene",
        "priority": 1,
        "event_types": ["funding", "startup", "executive_move"],
    },
    {
        "name": "EU Startups",
        "url": "https://www.eu-startups.com/feed/",
        "category": "Regional",
        "description": "European startup ecosystem",
        "priority": 1,
        "event_types": ["funding", "acquisition", "startup"],
    },
    # Industry
    {
        "name": "Fierce Healthcare",
        "url": "https://www.fiercehealthcare.com/rss/xml",
        "category": "Industry",
        "description": "Biotech and healthtech",
        "priority": 1,
        "event_types": ["funding", "acquisition"],
    },
    {
        "name": "Finextra",
        "url": "https://www.finextra.com/rss/headlines.aspx",
        "category": "Industry",
        "description": "Fintech news and funding",
        "priority": 1,
        "event_types": ["funding", "acquisition"],
    },
    # VC/Startup
    {
        "name": "YC Blog",
        "url": "https://blog.ycombinator.com/feed/",
        "category": "VC/Startup",
        "description": "Y Combinator company news",
        "priority": 0,
        "event_types": ["funding", "startup"],
    },
    {
        "name": "First Round Review",
        "url": "https://review.firstround.com/feed.xml",
        "category": "VC/Startup",
        "description": "First Round Capital insights",
        "priority": 1,
        "event_types": ["startup", "executive_move"],
    },
    # Layoffs/Hiring
    {
        "name": "Layoffs.fyi",
        "url": "https://layoffs.fyi/feed.xml",
        "category": "Layoffs/Hiring",
        "description": "Tech layoff tracking",
        "priority": 0,
        "event_types": ["layoff"],
    },
]


class FeedManager:
    """Manages RSS feed configuration - CRUD operations."""

    def __init__(self, config_path: str = None, storage: ArticleStorage = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.storage = storage or ArticleStorage()

    def _load_config(self) -> dict:
        """Load the feeds.json config file."""
        if not self.config_path.exists():
            return {"feeds": [], "settings": {}}
        with open(self.config_path) as f:
            return json.load(f)

    def _save_config(self, config: dict) -> None:
        """Save config atomically (write to temp, then rename)."""
        # Write to temp file in same directory
        fd, temp_path = tempfile.mkstemp(
            dir=self.config_path.parent,
            suffix=".json"
        )
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(config, f, indent=2)
            # Atomic rename
            os.replace(temp_path, self.config_path)
            logger.info("feeds_config_saved", path=str(self.config_path))
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def list_feeds(self) -> List[dict]:
        """List all configured feeds with their stats."""
        config = self._load_config()
        feeds = config.get("feeds", [])

        # Get stats from database
        stats_by_name = {s["feed_name"]: s for s in self.storage.get_all_feed_stats()}

        result = []
        for feed in feeds:
            stats = stats_by_name.get(feed["name"], {})
            result.append({
                **feed,
                "enabled": feed.get("enabled", True),
                "stats": {
                    "total_articles": stats.get("total_articles", 0),
                    "high_signal_articles": stats.get("high_signal_articles", 0),
                    "success_rate": stats.get("success_rate", 1.0),
                    "last_fetch_at": stats.get("last_fetch_at"),
                    "last_error": stats.get("last_error"),
                    "consecutive_failures": stats.get("consecutive_failures", 0),
                }
            })

        return result

    def get_feed(self, name: str) -> Optional[dict]:
        """Get a specific feed by name."""
        feeds = self.list_feeds()
        for feed in feeds:
            if feed["name"] == name:
                return feed
        return None

    def add_feed(
        self,
        url: str,
        name: str,
        priority: int = 1,
        event_types: List[str] = None
    ) -> dict:
        """Add a new feed."""
        config = self._load_config()

        # Check for duplicates
        for feed in config["feeds"]:
            if feed["url"] == url:
                raise ValueError(f"Feed with URL already exists: {url}")
            if feed["name"] == name:
                raise ValueError(f"Feed with name already exists: {name}")

        new_feed = {
            "name": name,
            "url": url,
            "priority": priority,
            "event_types": event_types or ["funding", "acquisition"],
            "enabled": True,
        }

        config["feeds"].append(new_feed)
        self._save_config(config)

        logger.info("feed_added", name=name, url=url)
        return new_feed

    def update_feed(self, name: str, **updates) -> bool:
        """Update an existing feed."""
        config = self._load_config()

        for i, feed in enumerate(config["feeds"]):
            if feed["name"] == name:
                # Apply updates
                for key, value in updates.items():
                    if key in ["url", "priority", "event_types", "enabled"]:
                        config["feeds"][i][key] = value
                self._save_config(config)
                logger.info("feed_updated", name=name, updates=updates)
                return True

        return False

    def delete_feed(self, name: str) -> bool:
        """Delete a feed."""
        config = self._load_config()
        original_len = len(config["feeds"])

        config["feeds"] = [f for f in config["feeds"] if f["name"] != name]

        if len(config["feeds"]) < original_len:
            self._save_config(config)
            logger.info("feed_deleted", name=name)
            return True

        return False

    def toggle_feed(self, name: str, enabled: bool) -> bool:
        """Enable or disable a feed."""
        return self.update_feed(name, enabled=enabled)

    async def validate_feed_url(self, url: str) -> dict:
        """Validate a feed URL by attempting to fetch and parse it."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {
                            "valid": False,
                            "error": f"HTTP {response.status}",
                        }

                    content = await response.text()

            # Parse with feedparser
            feed = feedparser.parse(content)

            if feed.bozo and not feed.entries:
                return {
                    "valid": False,
                    "error": "Not a valid RSS/Atom feed",
                }

            title = feed.feed.get("title", "Unknown Feed")
            item_count = len(feed.entries)

            return {
                "valid": True,
                "title": title,
                "item_count": item_count,
                "error": None,
            }

        except aiohttp.ClientError as e:
            return {
                "valid": False,
                "error": f"Connection error: {str(e)}",
            }
        except Exception as e:
            return {
                "valid": False,
                "error": str(e),
            }

    def get_suggested_feeds(self) -> List[dict]:
        """Get list of suggested feeds for one-click add."""
        config = self._load_config()
        existing_urls = {f["url"] for f in config["feeds"]}

        # Filter out already-added feeds
        return [
            feed for feed in SUGGESTED_FEEDS
            if feed["url"] not in existing_urls
        ]

    def add_suggested_feed(self, url: str) -> dict:
        """Add a suggested feed by URL."""
        for feed in SUGGESTED_FEEDS:
            if feed["url"] == url:
                return self.add_feed(
                    url=feed["url"],
                    name=feed["name"],
                    priority=feed["priority"],
                    event_types=feed["event_types"],
                )
        raise ValueError(f"Unknown suggested feed URL: {url}")
