"""Factory functions to create storage instances.

This module detects the database type from DATABASE_URL and returns
the appropriate storage implementation:
- PostgreSQL (Supabase) for production
- SQLite for local development
"""

import os
from functools import lru_cache
from typing import Union

import structlog

logger = structlog.get_logger()


def get_database_url() -> str:
    """Get database URL from environment, with fallback to SQLite."""
    # Check for DATABASE_URL first (standard for cloud platforms)
    url = os.environ.get('DATABASE_URL')
    if url:
        return url

    # Check for RI_ prefixed version
    url = os.environ.get('RI_DATABASE_URL')
    if url:
        return url

    # Default to SQLite for local development
    from ..config.settings import settings
    return settings.database_url


def is_postgres() -> bool:
    """Check if we're using PostgreSQL."""
    url = get_database_url()
    return url.startswith('postgresql://') or url.startswith('postgres://')


@lru_cache(maxsize=1)
def get_article_storage():
    """Get the appropriate article storage instance.

    Returns PostgresArticleStorage for PostgreSQL, ArticleStorage for SQLite.
    """
    url = get_database_url()

    if is_postgres():
        from .postgres_storage import PostgresArticleStorage
        logger.info("using_postgres_storage", url=url[:40] + "...")
        return PostgresArticleStorage(url)
    else:
        from .database import ArticleStorage
        logger.info("using_sqlite_storage", url=url[:40] + "...")
        return ArticleStorage(url)


@lru_cache(maxsize=1)
def get_knowledge_graph():
    """Get the appropriate knowledge graph instance.

    Returns PostgresKnowledgeGraph for PostgreSQL, KnowledgeGraph for SQLite.
    """
    url = get_database_url()

    if is_postgres():
        from .postgres_storage import PostgresKnowledgeGraph
        logger.info("using_postgres_kg", url=url[:40] + "...")
        return PostgresKnowledgeGraph(url)
    else:
        from ..knowledge_graph.graph import KnowledgeGraph
        logger.info("using_sqlite_kg")
        return KnowledgeGraph()


def clear_cache():
    """Clear cached instances (useful for testing)."""
    get_article_storage.cache_clear()
    get_knowledge_graph.cache_clear()
