"""SQLAlchemy models for the recruiter intelligence database."""

from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, Float, Index
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class RawArticleModel(Base):
    """Database model for raw articles."""
    __tablename__ = "raw_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Source identification
    source = Column(String(255), nullable=False)
    url = Column(String(2048), unique=True, nullable=False)

    # Content
    title = Column(Text)
    content = Column(Text)
    summary = Column(Text)

    # Timestamps
    published_at = Column(DateTime)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    # Processing state
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime)

    # Deduplication
    content_hash = Column(String(64), unique=True)

    # Metadata
    feed_priority = Column(Integer, default=1)

    # Classification results (filled after processing)
    event_type = Column(String(50))
    classification_confidence = Column(Float)
    is_high_signal = Column(Boolean, default=False)

    __table_args__ = (
        Index('idx_articles_processed', 'processed'),
        Index('idx_articles_published', 'published_at'),
        Index('idx_articles_source', 'source'),
        Index('idx_articles_high_signal', 'is_high_signal'),
    )


class ClassifiedArticleModel(Base):
    """Database model for classified articles with extracted data."""
    __tablename__ = "classified_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_article_id = Column(Integer, nullable=False)

    # Classification
    event_type = Column(String(50), nullable=False)
    confidence = Column(Float)
    matched_keywords = Column(Text)  # JSON array

    # Quality scores
    quality_score = Column(Float)
    extraction_potential = Column(String(20))

    # Timestamps
    classified_at = Column(DateTime, default=datetime.utcnow)
    extracted_at = Column(DateTime)

    __table_args__ = (
        Index('idx_classified_event_type', 'event_type'),
        Index('idx_classified_article', 'raw_article_id'),
    )


class EntityModel(Base):
    """Database model for extracted entities."""
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False)
    entity_type = Column(String(50), nullable=False)  # company, person, investor
    aliases = Column(Text)  # JSON array
    attributes = Column(Text)  # JSON object
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_entity_name', 'normalized_name'),
        Index('idx_entity_type', 'entity_type'),
    )


class RelationshipModel(Base):
    """Database model for entity relationships."""
    __tablename__ = "relationships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(Integer, nullable=False)
    predicate = Column(String(50), nullable=False)  # ACQUIRED, HIRED_BY, FUNDED_BY, DEPARTED_FROM
    object_id = Column(Integer, nullable=False)
    confidence = Column(Float, default=1.0)
    context = Column(Text)
    source_url = Column(String(2048))
    event_date = Column(DateTime)
    extracted_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_rel_subject', 'subject_id'),
        Index('idx_rel_object', 'object_id'),
        Index('idx_rel_predicate', 'predicate'),
        Index('idx_rel_event_date', 'event_date'),
    )


class FeedStatsModel(Base):
    """Database model for feed statistics."""
    __tablename__ = "feed_stats"

    feed_name = Column(String(255), primary_key=True)
    last_fetch_at = Column(DateTime)
    total_articles = Column(Integer, default=0)
    high_signal_articles = Column(Integer, default=0)
    last_error = Column(Text)
    consecutive_failures = Column(Integer, default=0)
    success_rate = Column(Float, default=1.0)
    avg_fetch_time_ms = Column(Integer, default=0)
    fetch_count = Column(Integer, default=0)


def init_db(database_url: str):
    """Initialize database and create all tables."""
    engine = create_engine(database_url, echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    """Get a new database session."""
    Session = sessionmaker(bind=engine)
    return Session()
