"""Migrate data from SQLite to Supabase PostgreSQL.

Usage:
    1. Set DATABASE_URL environment variable to your Supabase connection string
    2. Run: python scripts/migrate_to_supabase.py

Get connection string from:
    Supabase Dashboard → Settings → Database → Connection string (URI)
"""

import os
import sys
import sqlite3
from datetime import datetime
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file
from dotenv import load_dotenv
load_dotenv()

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("Installing psycopg2-binary...")
    os.system("pip install psycopg2-binary")
    import psycopg2
    from psycopg2.extras import execute_values


def get_sqlite_conn(db_path: str):
    """Get SQLite connection with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_feeds(sqlite_conn, pg_cursor):
    """Migrate feed configuration."""
    print("\n📡 Migrating feeds...")

    # Check if feed_stats exists in SQLite
    cursor = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='feed_stats'"
    )
    if not cursor.fetchone():
        print("   No feed_stats table found, skipping feeds migration")
        return 0

    cursor = sqlite_conn.execute("""
        SELECT feed_name, total_articles, last_fetch_at, last_error
        FROM feed_stats
    """)
    feeds = cursor.fetchall()

    count = 0
    for feed in feeds:
        try:
            # Skip if feed already exists from schema seed data
            pg_cursor.execute("""
                INSERT INTO feeds (name, url, feed_type, total_articles, last_fetch_at, last_error)
                VALUES (%s, %s, 'rss', %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    total_articles = EXCLUDED.total_articles,
                    last_fetch_at = EXCLUDED.last_fetch_at,
                    last_error = EXCLUDED.last_error
            """, (
                feed['feed_name'],
                f"https://example.com/feed/{feed['feed_name']}",  # Placeholder URL
                feed['total_articles'],
                feed['last_fetch_at'],
                feed['last_error']
            ))
            count += 1
        except Exception as e:
            print(f"   Warning: Failed to migrate feed {feed['feed_name']}: {e}")

    print(f"   ✓ Migrated {count} feeds")
    return count


def migrate_articles(sqlite_conn, pg_cursor):
    """Migrate articles from raw_articles table."""
    print("\n📰 Migrating articles...")

    cursor = sqlite_conn.execute("""
        SELECT url, title, content, summary, content_hash,
               published_at, fetched_at, processed, is_high_signal,
               event_type, extracted
        FROM raw_articles
        ORDER BY fetched_at ASC
    """)
    articles = cursor.fetchall()

    count = 0
    batch_size = 500
    batch = []

    for article in articles:
        classification_status = 'classified' if article['processed'] else 'pending'
        extraction_status = 'extracted' if article['extracted'] else 'pending'

        batch.append((
            article['url'],
            article['title'],
            article['content'],
            article['summary'],
            article['content_hash'],
            article['published_at'],
            article['fetched_at'],
            classification_status,
            extraction_status,
            article['event_type'],
            bool(article['is_high_signal'])
        ))

        if len(batch) >= batch_size:
            _insert_articles_batch(pg_cursor, batch)
            count += len(batch)
            print(f"   Migrated {count} articles...")
            batch = []

    if batch:
        _insert_articles_batch(pg_cursor, batch)
        count += len(batch)

    print(f"   ✓ Migrated {count} articles")
    return count


def _insert_articles_batch(pg_cursor, batch):
    """Insert a batch of articles."""
    execute_values(pg_cursor, """
        INSERT INTO articles (url, title, content, summary, content_hash,
                             published_at, fetched_at, classification_status,
                             extraction_status, event_type, is_high_signal)
        VALUES %s
        ON CONFLICT (url) DO NOTHING
    """, batch)


def migrate_entities(sqlite_conn, pg_cursor):
    """Migrate entities from kg_entities table."""
    print("\n🏢 Migrating entities...")

    cursor = sqlite_conn.execute("""
        SELECT id, name, normalized_name, entity_type, attributes_json, first_seen, last_seen, mention_count
        FROM kg_entities
    """)
    entities = cursor.fetchall()

    # Build ID mapping (SQLite integer ID -> PostgreSQL UUID)
    id_mapping = {}
    count = 0

    for entity in entities:
        try:
            pg_cursor.execute("""
                INSERT INTO entities (name, normalized_name, entity_type, attributes,
                                     first_seen_at, last_seen_at, mention_count)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
                RETURNING id
            """, (
                entity['name'],
                entity['normalized_name'],
                entity['entity_type'],
                entity['attributes_json'] or '{}',
                entity['first_seen'],
                entity['last_seen'],
                entity['mention_count'] or 1
            ))
            new_id = pg_cursor.fetchone()[0]
            id_mapping[entity['id']] = new_id
            count += 1
        except Exception as e:
            print(f"   Warning: Failed to migrate entity {entity['name']}: {e}")

    print(f"   ✓ Migrated {count} entities")
    return id_mapping


def migrate_relationships(sqlite_conn, pg_cursor, entity_id_mapping):
    """Migrate relationships from kg_relationships table."""
    print("\n🔗 Migrating relationships...")

    cursor = sqlite_conn.execute("""
        SELECT subject_id, predicate, object_id, context, source_url,
               confidence, created_at
        FROM kg_relationships
    """)
    relationships = cursor.fetchall()

    count = 0
    skipped = 0

    for rel in relationships:
        subject_uuid = entity_id_mapping.get(rel['subject_id'])
        object_uuid = entity_id_mapping.get(rel['object_id'])

        if not subject_uuid or not object_uuid:
            skipped += 1
            continue

        try:
            pg_cursor.execute("""
                INSERT INTO relationships (subject_id, predicate, object_id,
                                          context, source_url, confidence, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                subject_uuid,
                rel['predicate'],
                object_uuid,
                rel['context'],
                rel['source_url'],
                rel['confidence'] or 0.8,
                rel['created_at']
            ))
            count += 1
        except Exception as e:
            print(f"   Warning: Failed to migrate relationship: {e}")
            skipped += 1

    print(f"   ✓ Migrated {count} relationships (skipped {skipped})")
    return count


def migrate_enrichment(sqlite_conn, pg_cursor, entity_id_mapping):
    """Migrate enrichment data."""
    print("\n🔍 Migrating enrichment data...")

    # Check if kg_enrichment exists
    cursor = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_enrichment'"
    )
    if not cursor.fetchone():
        print("   No kg_enrichment table found, skipping")
        return 0

    cursor = sqlite_conn.execute("""
        SELECT entity_id, source, data_json, enriched_at
        FROM kg_enrichment
    """)
    enrichments = cursor.fetchall()

    count = 0
    for enrichment in enrichments:
        entity_uuid = entity_id_mapping.get(enrichment['entity_id'])
        if not entity_uuid:
            continue

        try:
            pg_cursor.execute("""
                UPDATE entities
                SET enrichment_data = %s::jsonb,
                    enrichment_status = 'enriched',
                    enriched_at = %s
                WHERE id = %s
            """, (
                enrichment['data_json'] or '{}',
                enrichment['enriched_at'],
                entity_uuid
            ))
            count += 1
        except Exception as e:
            print(f"   Warning: Failed to migrate enrichment: {e}")

    print(f"   ✓ Migrated {count} enrichment records")
    return count


def main():
    """Main migration function."""
    print("=" * 60)
    print("🚀 Recruiter Intelligence - SQLite to Supabase Migration")
    print("=" * 60)

    # Get database URL
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("\n❌ ERROR: DATABASE_URL not set!")
        print("\nTo get your Supabase connection string:")
        print("1. Go to https://supabase.com/dashboard")
        print("2. Select your project")
        print("3. Go to Settings → Database")
        print("4. Copy the 'Connection string' (URI format)")
        print("\nThen run:")
        print('  export DATABASE_URL="postgresql://postgres:PASSWORD@db.xxx.supabase.co:5432/postgres"')
        print("  python scripts/migrate_to_supabase.py")
        sys.exit(1)

    # Check SQLite files exist
    articles_db = 'data/recruiter_intel.db'
    kg_db = 'data/knowledge_graph.db'

    if not os.path.exists(articles_db):
        print(f"\n❌ ERROR: {articles_db} not found!")
        sys.exit(1)

    if not os.path.exists(kg_db):
        print(f"\n❌ ERROR: {kg_db} not found!")
        sys.exit(1)

    # Connect to databases
    print("\n📂 Connecting to databases...")
    articles_conn = get_sqlite_conn(articles_db)
    kg_conn = get_sqlite_conn(kg_db)

    try:
        pg_conn = psycopg2.connect(database_url)
        pg_conn.autocommit = False
        pg_cursor = pg_conn.cursor()
        print("   ✓ Connected to Supabase PostgreSQL")
    except Exception as e:
        print(f"\n❌ ERROR: Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

    try:
        # Run migrations
        migrate_feeds(articles_conn, pg_cursor)
        migrate_articles(articles_conn, pg_cursor)
        entity_id_mapping = migrate_entities(kg_conn, pg_cursor)
        migrate_relationships(kg_conn, pg_cursor, entity_id_mapping)
        migrate_enrichment(kg_conn, pg_cursor, entity_id_mapping)

        # Commit all changes
        pg_conn.commit()
        print("\n" + "=" * 60)
        print("✅ Migration completed successfully!")
        print("=" * 60)

        # Print summary
        pg_cursor.execute("SELECT COUNT(*) FROM articles")
        articles_count = pg_cursor.fetchone()[0]

        pg_cursor.execute("SELECT COUNT(*) FROM entities")
        entities_count = pg_cursor.fetchone()[0]

        pg_cursor.execute("SELECT COUNT(*) FROM relationships")
        relationships_count = pg_cursor.fetchone()[0]

        print(f"\n📊 Final counts in Supabase:")
        print(f"   Articles:      {articles_count}")
        print(f"   Entities:      {entities_count}")
        print(f"   Relationships: {relationships_count}")

    except Exception as e:
        pg_conn.rollback()
        print(f"\n❌ ERROR: Migration failed: {e}")
        print("   Transaction rolled back - no data was modified")
        raise
    finally:
        pg_cursor.close()
        pg_conn.close()
        articles_conn.close()
        kg_conn.close()


if __name__ == "__main__":
    main()
