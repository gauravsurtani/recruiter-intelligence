"""PostgreSQL storage adapter for Supabase.

This module provides storage classes that work with the Supabase PostgreSQL schema.
Used when DATABASE_URL starts with 'postgresql://'.
"""

import os
from datetime import datetime
from typing import Optional, List
from contextlib import contextmanager

import structlog

logger = structlog.get_logger()

# Import psycopg2 only when needed
_psycopg2 = None

def get_psycopg2():
    global _psycopg2
    if _psycopg2 is None:
        import psycopg2
        import psycopg2.extras
        _psycopg2 = psycopg2
    return _psycopg2


class PostgresArticleStorage:
    """PostgreSQL-based storage for articles (Supabase schema)."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._test_connection()

    def _test_connection(self):
        """Test database connection on init."""
        psycopg2 = get_psycopg2()
        try:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
            logger.info("postgres_connected", url=self.database_url[:50] + "...")
        except Exception as e:
            logger.error("postgres_connection_failed", error=str(e))
            raise

    @contextmanager
    def _connection(self):
        """Get a database connection."""
        psycopg2 = get_psycopg2()
        conn = psycopg2.connect(self.database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_article(self, article) -> Optional[str]:
        """Save article, return ID or None if duplicate."""
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO articles (url, title, content, summary, content_hash,
                                         published_at, fetched_at, classification_status,
                                         extraction_status, is_high_signal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 'pending', false)
                    ON CONFLICT (url) DO NOTHING
                    RETURNING id
                """, (
                    article.url,
                    article.title,
                    article.content,
                    article.summary,
                    article.content_hash,
                    article.published_at,
                    article.fetched_at or datetime.utcnow(),
                ))
                result = cursor.fetchone()
                if result:
                    logger.debug("article_saved", id=str(result[0])[:8], url=article.url[:50])
                    return str(result[0])
                return None
            except Exception as e:
                logger.debug("article_save_error", error=str(e), url=article.url[:50])
                return None

    def save_articles(self, articles: list) -> int:
        """Save multiple articles, return count of new articles saved."""
        saved_count = 0
        for article in articles:
            if self.save_article(article) is not None:
                saved_count += 1
        logger.info("articles_saved", count=saved_count, total=len(articles))
        return saved_count

    def get_unprocessed(self, limit: int = 100) -> list:
        """Get articles not yet classified."""
        from ..ingestion.interfaces import RawArticle

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, url, title, content, summary, content_hash,
                       published_at, fetched_at
                FROM articles
                WHERE classification_status = 'pending'
                ORDER BY published_at DESC NULLS LAST
                LIMIT %s
            """, (limit,))

            results = []
            for row in cursor.fetchall():
                results.append(RawArticle(
                    id=str(row[0]),
                    source='rss',
                    url=row[1],
                    title=row[2],
                    content=row[3],
                    summary=row[4],
                    content_hash=row[5],
                    published_at=row[6],
                    fetched_at=row[7],
                ))
            return results

    def mark_processed(
        self,
        article_id: str,
        event_type: str = None,
        confidence: float = None,
        is_high_signal: bool = False
    ) -> None:
        """Mark article as classified."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE articles
                SET classification_status = 'classified',
                    classified_at = %s,
                    event_type = %s,
                    classification_confidence = %s,
                    is_high_signal = %s
                WHERE id = %s
            """, (
                datetime.utcnow(),
                event_type,
                confidence,
                is_high_signal,
                article_id
            ))
            logger.debug("article_classified", id=str(article_id)[:8])

    def mark_extracted(self, article_id: str) -> None:
        """Mark article as extracted."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE articles
                SET extraction_status = 'extracted',
                    extracted_at = %s
                WHERE id = %s
            """, (datetime.utcnow(), article_id))
            logger.debug("article_extracted", id=str(article_id)[:8])

    def get_unextracted_high_signal(self, limit: int = 100) -> list:
        """Get high-signal articles that haven't been extracted yet."""
        from ..ingestion.interfaces import RawArticle

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, url, title, content, summary, content_hash,
                       published_at, fetched_at, event_type
                FROM articles
                WHERE is_high_signal = true
                  AND extraction_status = 'pending'
                ORDER BY published_at DESC NULLS LAST
                LIMIT %s
            """, (limit,))

            results = []
            for row in cursor.fetchall():
                results.append(RawArticle(
                    id=str(row[0]),
                    source='rss',
                    url=row[1],
                    title=row[2],
                    content=row[3],
                    summary=row[4],
                    content_hash=row[5],
                    published_at=row[6],
                    fetched_at=row[7],
                ))
            return results

    def get_high_signal_articles(
        self,
        limit: int = 100,
        since: datetime = None
    ) -> list:
        """Get high-signal articles."""
        from ..ingestion.interfaces import RawArticle

        with self._connection() as conn:
            cursor = conn.cursor()

            sql = """
                SELECT id, url, title, content, summary, content_hash,
                       published_at, fetched_at, event_type
                FROM articles
                WHERE is_high_signal = true
            """
            params = []

            if since:
                sql += " AND published_at >= %s"
                params.append(since)

            sql += " ORDER BY published_at DESC NULLS LAST LIMIT %s"
            params.append(limit)

            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                results.append(RawArticle(
                    id=str(row[0]),
                    source='rss',
                    url=row[1],
                    title=row[2],
                    content=row[3],
                    summary=row[4],
                    content_hash=row[5],
                    published_at=row[6],
                    fetched_at=row[7],
                ))
            return results

    def get_stats(self) -> dict:
        """Get database statistics."""
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM articles")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM articles WHERE classification_status = 'classified'")
            processed = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM articles WHERE is_high_signal = true")
            high_signal = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM articles WHERE extraction_status = 'pending' AND is_high_signal = true")
            pending_extraction = cursor.fetchone()[0]

            return {
                "total_articles": total,
                "processed_articles": processed,
                "unprocessed_articles": total - processed,
                "high_signal_articles": high_signal,
                "pending_extraction": pending_extraction,
            }

    def update_feed_stats(
        self,
        feed_name: str,
        articles: int = 0,
        high_signal: int = 0,
        error: str = None,
        fetch_time_ms: int = 0
    ) -> None:
        """Update feed statistics."""
        with self._connection() as conn:
            cursor = conn.cursor()

            # Check if feed exists
            cursor.execute("SELECT id FROM feeds WHERE name = %s", (feed_name,))
            result = cursor.fetchone()

            if result:
                feed_id = result[0]
                cursor.execute("""
                    UPDATE feeds
                    SET last_fetch_at = %s,
                        total_articles = total_articles + %s,
                        last_error = %s,
                        consecutive_failures = CASE WHEN %s IS NOT NULL THEN consecutive_failures + 1 ELSE 0 END
                    WHERE id = %s
                """, (datetime.utcnow(), articles, error, error, feed_id))
            else:
                # Create feed entry
                cursor.execute("""
                    INSERT INTO feeds (name, url, feed_type, total_articles, last_fetch_at, last_error)
                    VALUES (%s, %s, 'rss', %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        total_articles = feeds.total_articles + %s,
                        last_fetch_at = %s
                """, (feed_name, f"feed://{feed_name}", articles, datetime.utcnow(), error, articles, datetime.utcnow()))

    def get_all_feed_stats(self) -> list:
        """Get statistics for all feeds."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT name, last_fetch_at, total_articles, last_error, consecutive_failures
                FROM feeds
                ORDER BY name
            """)
            return [
                {
                    "feed_name": row[0],
                    "last_fetch_at": row[1],
                    "total_articles": row[2] or 0,
                    "last_error": row[3],
                    "consecutive_failures": row[4] or 0,
                }
                for row in cursor.fetchall()
            ]


class PostgresKnowledgeGraph:
    """PostgreSQL-backed knowledge graph (Supabase schema)."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def _connection(self):
        """Get a database connection."""
        psycopg2 = get_psycopg2()
        conn = psycopg2.connect(self.database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add_entity(
        self,
        name: str,
        entity_type: str,
        attributes: dict = None
    ) -> str:
        """Add or update an entity, return UUID."""
        import json
        normalized = name.lower().strip()

        with self._connection() as conn:
            cursor = conn.cursor()

            # Check if exists
            cursor.execute("""
                SELECT id, mention_count FROM entities
                WHERE normalized_name = %s AND entity_type = %s
            """, (normalized, entity_type))
            existing = cursor.fetchone()

            if existing:
                cursor.execute("""
                    UPDATE entities
                    SET mention_count = mention_count + 1,
                        last_seen_at = NOW(),
                        attributes = COALESCE(%s::jsonb, attributes)
                    WHERE id = %s
                    RETURNING id
                """, (json.dumps(attributes) if attributes else None, existing[0]))
                return str(existing[0])
            else:
                cursor.execute("""
                    INSERT INTO entities (name, normalized_name, entity_type, attributes)
                    VALUES (%s, %s, %s, %s::jsonb)
                    RETURNING id
                """, (name, normalized, entity_type, json.dumps(attributes) if attributes else '{}'))
                return str(cursor.fetchone()[0])

    def get_entity(self, name: str, entity_type: str = None):
        """Get an entity by name."""
        from .interfaces import GraphEntity
        import json
        from datetime import date

        normalized = name.lower().strip()

        with self._connection() as conn:
            cursor = conn.cursor()

            sql = "SELECT * FROM entities WHERE normalized_name = %s"
            params = [normalized]
            if entity_type:
                sql += " AND entity_type = %s"
                params.append(entity_type)

            cursor.execute(sql, params)
            row = cursor.fetchone()

            if row:
                return self._row_to_entity(cursor, row)
            return None

    def search_entities(self, query: str, entity_type: str = None, limit: int = 1000):
        """Search entities by name pattern."""
        from ..knowledge_graph.interfaces import GraphEntity

        pattern = f"%{query.lower()}%"

        with self._connection() as conn:
            cursor = conn.cursor()

            sql = "SELECT * FROM entities WHERE normalized_name LIKE %s"
            params = [pattern]
            if entity_type:
                sql += " AND entity_type = %s"
                params.append(entity_type)
            sql += " ORDER BY mention_count DESC LIMIT %s"
            params.append(limit)

            cursor.execute(sql, params)
            return [self._row_to_entity(cursor, row) for row in cursor.fetchall()]

    def add_relationship(
        self,
        subject_name: str, subject_type: str,
        predicate: str,
        object_name: str, object_type: str,
        event_date=None,
        confidence: float = 0.0,
        context: str = "",
        source_url: str = "",
        metadata: dict = None
    ) -> Optional[str]:
        """Add a relationship between entities."""
        subject_id = self.add_entity(subject_name, subject_type)
        object_id = self.add_entity(object_name, object_type)

        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO relationships
                    (subject_id, predicate, object_id, start_date, confidence, context, source_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                """, (
                    subject_id, predicate, object_id,
                    event_date.isoformat() if event_date else None,
                    confidence, context, source_url
                ))
                result = cursor.fetchone()
                if result:
                    logger.debug(
                        "relationship_added",
                        subject=subject_name,
                        predicate=predicate,
                        object=object_name
                    )
                    return str(result[0])
                return None
            except Exception as e:
                logger.debug("relationship_exists", subject=subject_name, predicate=predicate, object=object_name)
                return None

    def query(
        self,
        subject: str = None,
        predicate: str = None,
        obj: str = None,
        since_date=None,
        limit: int = 100
    ):
        """Query relationships with filters."""
        from ..knowledge_graph.interfaces import GraphEntity, GraphRelationship
        import json
        from datetime import date

        with self._connection() as conn:
            cursor = conn.cursor()

            sql = """
                SELECT
                    r.id, r.predicate, r.start_date, r.confidence, r.context, r.source_url,
                    s.id as s_id, s.name as s_name, s.normalized_name as s_norm,
                    s.entity_type as s_type, s.attributes as s_attrs,
                    s.mention_count as s_count, s.first_seen_at as s_first, s.last_seen_at as s_last,
                    o.id as o_id, o.name as o_name, o.normalized_name as o_norm,
                    o.entity_type as o_type, o.attributes as o_attrs,
                    o.mention_count as o_count, o.first_seen_at as o_first, o.last_seen_at as o_last
                FROM relationships r
                JOIN entities s ON r.subject_id = s.id
                JOIN entities o ON r.object_id = o.id
                WHERE 1=1
            """
            params = []

            if subject:
                sql += " AND s.normalized_name LIKE %s"
                params.append(f"%{subject.lower()}%")
            if predicate:
                sql += " AND r.predicate = %s"
                params.append(predicate)
            if obj:
                sql += " AND o.normalized_name LIKE %s"
                params.append(f"%{obj.lower()}%")
            if since_date:
                sql += " AND (r.start_date IS NULL OR r.start_date >= %s)"
                params.append(since_date.isoformat())

            sql += " ORDER BY r.start_date DESC NULLS LAST, r.id DESC LIMIT %s"
            params.append(limit)

            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                subject_entity = GraphEntity(
                    id=str(row[6]),
                    name=row[7],
                    normalized_name=row[8],
                    entity_type=row[9],
                    attributes=row[10] if isinstance(row[10], dict) else {},
                    mention_count=row[11] or 0,
                    first_seen=row[12].date() if row[12] else None,
                    last_seen=row[13].date() if row[13] else None,
                )
                object_entity = GraphEntity(
                    id=str(row[14]),
                    name=row[15],
                    normalized_name=row[16],
                    entity_type=row[17],
                    attributes=row[18] if isinstance(row[18], dict) else {},
                    mention_count=row[19] or 0,
                    first_seen=row[20].date() if row[20] else None,
                    last_seen=row[21].date() if row[21] else None,
                )
                results.append(GraphRelationship(
                    id=str(row[0]),
                    subject=subject_entity,
                    predicate=row[1],
                    object=object_entity,
                    event_date=row[2] if row[2] else None,
                    confidence=row[3] or 0.0,
                    context=row[4] or "",
                    source_url=row[5] or "",
                    metadata={},
                ))
            return results

    def who_hired(self, company: str, since=None):
        """Find people hired by a company."""
        return self.query(obj=company, predicate="HIRED_BY", since_date=since)

    def where_went(self, person: str):
        """Find where a person went."""
        return self.query(subject=person, predicate="HIRED_BY")

    def acquisitions(self, since=None):
        """Get recent acquisitions."""
        return self.query(predicate="ACQUIRED", since_date=since)

    def get_stats(self) -> dict:
        """Get graph statistics."""
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM entities")
            entities = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM relationships")
            relationships = cursor.fetchone()[0]

            entities_by_type = {}
            cursor.execute("SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type")
            for row in cursor.fetchall():
                entities_by_type[row[0]] = row[1]

            rels_by_type = {}
            cursor.execute("SELECT predicate, COUNT(*) FROM relationships GROUP BY predicate")
            for row in cursor.fetchall():
                rels_by_type[row[0]] = row[1]

            return {
                "total_entities": entities,
                "total_relationships": relationships,
                "entities_by_type": entities_by_type,
                "relationships_by_type": rels_by_type,
            }

    def add_extraction_result(self, result, source_url: str = ""):
        """Add entities and relationships from an extraction result."""
        if hasattr(result, 'entities'):
            for entity in result.entities:
                self.add_entity(entity.name, entity.entity_type, entity.attributes)

        if hasattr(result, 'relationships'):
            for rel in result.relationships:
                self.add_relationship(
                    subject_name=rel.subject,
                    subject_type=rel.subject_type,
                    predicate=rel.predicate,
                    object_name=rel.object,
                    object_type=rel.object_type,
                    event_date=rel.event_date,
                    confidence=rel.confidence,
                    context=rel.context,
                    source_url=source_url or getattr(result, 'source_url', ''),
                )

    def _row_to_entity(self, cursor, row):
        """Convert database row to GraphEntity."""
        from ..knowledge_graph.interfaces import GraphEntity

        # Get column names
        columns = [desc[0] for desc in cursor.description]
        row_dict = dict(zip(columns, row))

        return GraphEntity(
            id=str(row_dict.get('id', '')),
            name=row_dict.get('name', ''),
            normalized_name=row_dict.get('normalized_name', ''),
            entity_type=row_dict.get('entity_type', ''),
            attributes=row_dict.get('attributes') if isinstance(row_dict.get('attributes'), dict) else {},
            mention_count=row_dict.get('mention_count', 0),
            first_seen=row_dict.get('first_seen_at').date() if row_dict.get('first_seen_at') else None,
            last_seen=row_dict.get('last_seen_at').date() if row_dict.get('last_seen_at') else None,
        )

    # Enrichment methods
    def add_enrichment(self, entity_id: str, source: str, data: dict) -> bool:
        """Add enrichment data for an entity."""
        import json
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE entities
                    SET enrichment_data = %s::jsonb,
                        enrichment_status = 'enriched',
                        enriched_at = NOW()
                    WHERE id = %s
                """, (json.dumps(data), entity_id))
                return True
            except Exception as e:
                logger.error("enrichment_failed", error=str(e))
                return False

    def get_enrichment(self, entity_id: str, source: str = None) -> dict:
        """Get enrichment data for an entity."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT enrichment_data, enriched_at
                FROM entities
                WHERE id = %s
            """, (entity_id,))
            row = cursor.fetchone()
            if row and row[0]:
                return {
                    "data": row[0] if isinstance(row[0], dict) else {},
                    "enriched_at": row[1]
                }
            return {}

    def get_entity_by_id(self, entity_id: str):
        """Get an entity by ID."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM entities WHERE id = %s", (entity_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_entity(cursor, row)
            return None
