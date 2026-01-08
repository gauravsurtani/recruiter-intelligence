"""Feed configuration loader."""

import json
from pathlib import Path
from typing import List

from ..ingestion.interfaces import FeedConfig, FeedPriority


def load_feeds(config_path: str = None) -> List[FeedConfig]:
    """Load feed configurations from JSON file."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "feeds.json"

    with open(config_path) as f:
        data = json.load(f)

    feeds = []
    for feed_data in data.get("feeds", []):
        priority = FeedPriority(feed_data.get("priority", 1))
        feeds.append(FeedConfig(
            name=feed_data["name"],
            url=feed_data["url"],
            priority=priority,
            enabled=feed_data.get("enabled", True),
            fetch_interval_minutes=feed_data.get(
                "fetch_interval_minutes",
                data.get("settings", {}).get("default_fetch_interval_minutes", 60)
            ),
            event_types=feed_data.get("event_types", [])
        ))

    return feeds
