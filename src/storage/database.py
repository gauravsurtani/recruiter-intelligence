"""Database operations for article storage."""

from datetime import datetime
from typing import Optional, List
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
import structlog

from .models import Base, RawArticleModel, FeedStatsModel, init_db
from ..ingestion.interfaces import RawArticle, StorageInterface
from ..config.settings import settings

logger = structlog.get_logger()


class ArticleStorage(StorageInterface):
    """SQLite-based storage for articles."""

    def __init__(self, database_url: str = None):
        if database_url is None:
            database_url = settings.database_url

        # Ensure data directory exists
        if database_url.startswith("sqlite:///"):
            db_path = database_url.replace("sqlite:///", "")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.engine = init_db(database_url)
        self.Session = sessionmaker(bind=self.engine)

    def save_article(self, article: RawArticle) -> Optional[int]:
        """Save article, return ID or None if duplicate."""
        session = self.Session()
        try:
            model = RawArticleModel(
                source=article.source,
                url=article.url,
                title=article.title,
                content=article.content,
                summary=article.summary,
                published_at=article.published_at,
                fetched_at=article.fetched_at,
                content_hash=article.content_hash,
                feed_priority=article.feed_priority,
            )
            session.add(model)
            session.commit()
            article_id = model.id
            logger.debug("article_saved", id=article_id, url=article.url[:50])
            return article_id
        except IntegrityError:
            session.rollback()
            logger.debug("article_duplicate", url=article.url[:50])
            return None
        finally:
            session.close()

    def save_articles(self, articles: List[RawArticle]) -> int:
        """Save multiple articles, return count of new articles saved."""
        saved_count = 0
        for article in articles:
            if self.save_article(article) is not None:
                saved_count += 1
        logger.info("articles_saved", count=saved_count, total=len(articles))
        return saved_count

    def get_unprocessed(self, limit: int = 100) -> List[RawArticle]:
        """Get articles not yet processed."""
        session = self.Session()
        try:
            models = session.query(RawArticleModel)\
                .filter(RawArticleModel.processed == False)\
                .order_by(RawArticleModel.feed_priority, RawArticleModel.published_at.desc())\
                .limit(limit)\
                .all()

            return [self._model_to_article(m) for m in models]
        finally:
            session.close()

    def mark_processed(
        self,
        article_id: int,
        event_type: str = None,
        confidence: float = None,
        is_high_signal: bool = False
    ) -> None:
        """Mark article as processed with optional classification results."""
        session = self.Session()
        try:
            model = session.query(RawArticleModel).get(article_id)
            if model:
                model.processed = True
                model.processed_at = datetime.utcnow()
                if event_type:
                    model.event_type = event_type
                if confidence:
                    model.classification_confidence = confidence
                model.is_high_signal = is_high_signal
                session.commit()
                logger.debug("article_processed", id=article_id)
        finally:
            session.close()

    def mark_extracted(self, article_id: int) -> None:
        """Mark article as extracted (LLM extraction completed)."""
        session = self.Session()
        try:
            model = session.query(RawArticleModel).get(article_id)
            if model:
                model.extracted = True
                session.commit()
                logger.debug("article_extracted", id=article_id)
        finally:
            session.close()

    def get_unextracted_high_signal(self, limit: int = 100) -> List[RawArticle]:
        """Get high-signal articles that haven't been extracted yet."""
        session = self.Session()
        try:
            models = session.query(RawArticleModel)\
                .filter(RawArticleModel.is_high_signal == True)\
                .filter(RawArticleModel.extracted == False)\
                .order_by(RawArticleModel.published_at.desc())\
                .limit(limit)\
                .all()
            return [self._model_to_article(m) for m in models]
        finally:
            session.close()

    def get_by_url(self, url: str) -> Optional[RawArticle]:
        """Get article by URL."""
        session = self.Session()
        try:
            model = session.query(RawArticleModel)\
                .filter(RawArticleModel.url == url)\
                .first()
            return self._model_to_article(model) if model else None
        finally:
            session.close()

    def exists(self, content_hash: str) -> bool:
        """Check if article with given hash exists."""
        session = self.Session()
        try:
            return session.query(RawArticleModel)\
                .filter(RawArticleModel.content_hash == content_hash)\
                .count() > 0
        finally:
            session.close()

    def get_high_signal_articles(
        self,
        limit: int = 100,
        since: datetime = None
    ) -> List[RawArticle]:
        """Get high-signal articles for extraction."""
        session = self.Session()
        try:
            query = session.query(RawArticleModel)\
                .filter(RawArticleModel.is_high_signal == True)\
                .order_by(RawArticleModel.published_at.desc())

            if since:
                query = query.filter(RawArticleModel.published_at >= since)

            models = query.limit(limit).all()
            return [self._model_to_article(m) for m in models]
        finally:
            session.close()

    def get_stats(self) -> dict:
        """Get database statistics."""
        session = self.Session()
        try:
            total = session.query(RawArticleModel).count()
            processed = session.query(RawArticleModel)\
                .filter(RawArticleModel.processed == True).count()
            high_signal = session.query(RawArticleModel)\
                .filter(RawArticleModel.is_high_signal == True).count()

            return {
                "total_articles": total,
                "processed_articles": processed,
                "unprocessed_articles": total - processed,
                "high_signal_articles": high_signal,
            }
        finally:
            session.close()

    def _model_to_article(self, model: RawArticleModel) -> RawArticle:
        """Convert database model to RawArticle."""
        return RawArticle(
            id=model.id,
            source=model.source,
            url=model.url,
            title=model.title,
            content=model.content,
            summary=model.summary,
            published_at=model.published_at,
            fetched_at=model.fetched_at,
            content_hash=model.content_hash,
            feed_priority=model.feed_priority,
        )

    def update_feed_stats(
        self,
        feed_name: str,
        articles: int = 0,
        high_signal: int = 0,
        error: str = None,
        fetch_time_ms: int = 0
    ) -> None:
        """Update feed statistics after a fetch."""
        session = self.Session()
        try:
            stats = session.query(FeedStatsModel).get(feed_name)
            if not stats:
                stats = FeedStatsModel(feed_name=feed_name)
                session.add(stats)

            stats.last_fetch_at = datetime.utcnow()
            stats.total_articles = (stats.total_articles or 0) + articles
            stats.high_signal_articles = (stats.high_signal_articles or 0) + high_signal
            stats.fetch_count = (stats.fetch_count or 0) + 1

            if error:
                stats.last_error = error
                stats.consecutive_failures = (stats.consecutive_failures or 0) + 1
            else:
                stats.last_error = None
                stats.consecutive_failures = 0

            # Update success rate (rolling average)
            success = 0 if error else 1
            old_rate = stats.success_rate or 1.0
            stats.success_rate = (old_rate * 0.9) + (success * 0.1)

            # Update average fetch time
            old_avg = stats.avg_fetch_time_ms or 0
            stats.avg_fetch_time_ms = int((old_avg * 0.9) + (fetch_time_ms * 0.1))

            session.commit()
            logger.debug("feed_stats_updated", feed=feed_name, articles=articles)
        finally:
            session.close()

    def get_feed_stats(self, feed_name: str) -> Optional[dict]:
        """Get statistics for a specific feed."""
        session = self.Session()
        try:
            stats = session.query(FeedStatsModel).get(feed_name)
            if not stats:
                return None
            return {
                "feed_name": stats.feed_name,
                "last_fetch_at": stats.last_fetch_at,
                "total_articles": stats.total_articles or 0,
                "high_signal_articles": stats.high_signal_articles or 0,
                "last_error": stats.last_error,
                "consecutive_failures": stats.consecutive_failures or 0,
                "success_rate": stats.success_rate or 1.0,
                "avg_fetch_time_ms": stats.avg_fetch_time_ms or 0,
                "fetch_count": stats.fetch_count or 0,
            }
        finally:
            session.close()

    def get_all_feed_stats(self) -> List[dict]:
        """Get statistics for all feeds."""
        session = self.Session()
        try:
            all_stats = session.query(FeedStatsModel).all()
            return [
                {
                    "feed_name": s.feed_name,
                    "last_fetch_at": s.last_fetch_at,
                    "total_articles": s.total_articles or 0,
                    "high_signal_articles": s.high_signal_articles or 0,
                    "last_error": s.last_error,
                    "consecutive_failures": s.consecutive_failures or 0,
                    "success_rate": s.success_rate or 1.0,
                    "avg_fetch_time_ms": s.avg_fetch_time_ms or 0,
                    "fetch_count": s.fetch_count or 0,
                }
                for s in all_stats
            ]
        finally:
            session.close()
