# Recruiter Intelligence - Production Deployment Plan

## Vision

Transform from a local development tool into a **self-sustaining, always-on intelligence service** that:
- Runs automatically every day
- Builds a growing database of recruiting intelligence
- Accessible via web dashboard from anywhere
- Costs < $50/month to operate

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DEPLOYMENT                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│  │   Railway   │     │  Supabase   │     │   Vercel    │                   │
│  │   or Fly.io │     │ (PostgreSQL)│     │ (Optional)  │                   │
│  │             │     │             │     │             │                   │
│  │  Pipeline   │────▶│  Database   │◀────│  Dashboard  │                   │
│  │  Worker     │     │             │     │  (FastAPI)  │                   │
│  │  (Cron)     │     │             │     │             │                   │
│  └─────────────┘     └─────────────┘     └─────────────┘                   │
│         │                   ▲                   │                           │
│         │                   │                   │                           │
│         ▼                   │                   ▼                           │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                   │
│  │  External   │     │   Redis     │     │   Users     │                   │
│  │  APIs       │     │  (Cache)    │     │  (Browser)  │                   │
│  │  - RSS      │     │  Optional   │     │             │                   │
│  │  - SEC      │     │             │     │             │                   │
│  │  - LLM      │     │             │     │             │                   │
│  └─────────────┘     └─────────────┘     └─────────────┘                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Database Schema (PostgreSQL)

### Why PostgreSQL over SQLite?
- Concurrent access (multiple workers)
- Better query performance at scale
- Full-text search built-in
- JSON support for flexible attributes
- Managed hosting options (Supabase, Neon, Railway)

### Core Schema

```sql
-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For fuzzy text search

-- ============================================
-- SOURCES: Where data comes from
-- ============================================

CREATE TABLE feeds (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL UNIQUE,
    url TEXT NOT NULL,
    feed_type VARCHAR(50) NOT NULL DEFAULT 'rss',  -- rss, api, scraper
    priority INTEGER DEFAULT 1,
    event_types TEXT[],  -- ['funding', 'acquisition', 'layoff']

    -- Health tracking
    is_active BOOLEAN DEFAULT true,
    last_fetch_at TIMESTAMP WITH TIME ZONE,
    last_success_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    total_articles INTEGER DEFAULT 0,

    -- Settings
    fetch_interval_minutes INTEGER DEFAULT 60,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================
-- ARTICLES: Raw ingested content
-- ============================================

CREATE TABLE articles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    feed_id UUID REFERENCES feeds(id),

    -- Content
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content TEXT,
    summary TEXT,

    -- Hashing for deduplication
    content_hash VARCHAR(64) UNIQUE,
    title_hash VARCHAR(64),

    -- Timestamps
    published_at TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Processing state
    classification_status VARCHAR(20) DEFAULT 'pending',  -- pending, classified, skipped
    classified_at TIMESTAMP WITH TIME ZONE,

    extraction_status VARCHAR(20) DEFAULT 'pending',  -- pending, extracted, failed, skipped
    extracted_at TIMESTAMP WITH TIME ZONE,
    extraction_error TEXT,

    -- Classification results
    event_type VARCHAR(50),  -- funding, acquisition, layoff, executive_move, ipo
    is_high_signal BOOLEAN DEFAULT false,
    classification_confidence FLOAT,
    matched_keywords TEXT[],

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_articles_status ON articles(classification_status, extraction_status);
CREATE INDEX idx_articles_high_signal ON articles(is_high_signal) WHERE is_high_signal = true;
CREATE INDEX idx_articles_published ON articles(published_at DESC);
CREATE INDEX idx_articles_event_type ON articles(event_type);

-- ============================================
-- ENTITIES: Companies, People, Investors
-- ============================================

CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Identity
    name VARCHAR(500) NOT NULL,
    normalized_name VARCHAR(500) NOT NULL,  -- Lowercase, trimmed for matching
    entity_type VARCHAR(50) NOT NULL,  -- company, person, investor, group

    -- Deduplication
    canonical_id UUID REFERENCES entities(id),  -- Points to canonical entity if duplicate
    aliases TEXT[] DEFAULT '{}',

    -- Attributes (flexible JSON)
    attributes JSONB DEFAULT '{}',
    /*
    For companies: { industry, founded_year, hq_location, employee_count, website }
    For people: { title, current_company, linkedin_url }
    For investors: { fund_type, aum, focus_areas }
    */

    -- Enrichment
    enrichment_status VARCHAR(20) DEFAULT 'pending',  -- pending, enriched, failed
    enriched_at TIMESTAMP WITH TIME ZONE,
    enrichment_data JSONB DEFAULT '{}',

    -- Tracking
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    mention_count INTEGER DEFAULT 1,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_entities_normalized ON entities(normalized_name);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_canonical ON entities(canonical_id) WHERE canonical_id IS NOT NULL;
CREATE INDEX idx_entities_name_trgm ON entities USING gin(name gin_trgm_ops);  -- Fuzzy search

-- ============================================
-- EVENTS: Discrete business events
-- ============================================

CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Event classification
    event_type VARCHAR(50) NOT NULL,  -- funding, acquisition, layoff, hire, departure, ipo

    -- Primary entities involved
    subject_entity_id UUID REFERENCES entities(id),  -- Who did the action
    object_entity_id UUID REFERENCES entities(id),   -- Who received the action

    -- Event details (type-specific)
    details JSONB DEFAULT '{}',
    /*
    For funding: { amount, currency, round, lead_investor, valuation }
    For acquisition: { amount, currency, deal_type }
    For layoff: { num_affected, percentage, department }
    For hire: { role, previous_company }
    For departure: { role, reason }
    For ipo: { exchange, initial_price, valuation }
    */

    -- When it happened
    event_date DATE,
    event_date_precision VARCHAR(10) DEFAULT 'day',  -- day, month, year

    -- Source attribution
    source_article_id UUID REFERENCES articles(id),
    source_url TEXT,
    source_type VARCHAR(50),  -- news, sec_filing, press_release, scraper

    -- Quality
    confidence FLOAT DEFAULT 0.8,
    is_verified BOOLEAN DEFAULT false,
    verified_by TEXT,  -- sec_filing, multiple_sources, manual

    -- Deduplication
    event_hash VARCHAR(64),  -- Hash of key event details for dedup
    canonical_event_id UUID REFERENCES events(id),  -- Points to canonical if duplicate

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_date ON events(event_date DESC);
CREATE INDEX idx_events_subject ON events(subject_entity_id);
CREATE INDEX idx_events_object ON events(object_entity_id);
CREATE INDEX idx_events_hash ON events(event_hash);

-- ============================================
-- RELATIONSHIPS: Entity connections
-- ============================================

CREATE TABLE relationships (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- The relationship
    subject_id UUID NOT NULL REFERENCES entities(id),
    predicate VARCHAR(50) NOT NULL,  -- FUNDED_BY, ACQUIRED, HIRED_BY, DEPARTED_FROM, etc.
    object_id UUID NOT NULL REFERENCES entities(id),

    -- Context
    context TEXT,  -- Extracted sentence/snippet
    event_id UUID REFERENCES events(id),  -- Link to event if applicable

    -- Source
    source_article_id UUID REFERENCES articles(id),
    source_url TEXT,

    -- Quality
    confidence FLOAT DEFAULT 0.8,
    extraction_method VARCHAR(50),  -- llm, rule_based, sec_filing

    -- Temporal
    start_date DATE,  -- When relationship started
    end_date DATE,    -- When relationship ended (for departures)
    is_current BOOLEAN DEFAULT true,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Prevent exact duplicates
    UNIQUE(subject_id, predicate, object_id, source_article_id)
);

CREATE INDEX idx_relationships_subject ON relationships(subject_id);
CREATE INDEX idx_relationships_object ON relationships(object_id);
CREATE INDEX idx_relationships_predicate ON relationships(predicate);
CREATE INDEX idx_relationships_event ON relationships(event_id);

-- ============================================
-- PIPELINE: Job tracking
-- ============================================

CREATE TABLE pipeline_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    run_type VARCHAR(50) NOT NULL,  -- full, fetch, classify, extract, enrich
    status VARCHAR(20) DEFAULT 'running',  -- running, completed, failed

    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,

    -- Stats
    articles_fetched INTEGER DEFAULT 0,
    articles_classified INTEGER DEFAULT 0,
    articles_extracted INTEGER DEFAULT 0,
    entities_created INTEGER DEFAULT 0,
    events_created INTEGER DEFAULT 0,
    relationships_created INTEGER DEFAULT 0,

    -- Errors
    error_message TEXT,
    error_count INTEGER DEFAULT 0,

    -- Cost tracking
    llm_tokens_used INTEGER DEFAULT 0,
    llm_cost_usd DECIMAL(10, 4) DEFAULT 0,

    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_pipeline_runs_type ON pipeline_runs(run_type, started_at DESC);

-- ============================================
-- NEWSLETTERS: Generated content
-- ============================================

CREATE TABLE newsletters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    period_type VARCHAR(20) NOT NULL,  -- daily, weekly
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,

    -- Content
    title VARCHAR(500),
    summary TEXT,
    html_content TEXT,
    markdown_content TEXT,

    -- Stats
    funding_count INTEGER DEFAULT 0,
    acquisition_count INTEGER DEFAULT 0,
    hire_count INTEGER DEFAULT 0,
    departure_count INTEGER DEFAULT 0,
    layoff_count INTEGER DEFAULT 0,

    -- Delivery
    is_published BOOLEAN DEFAULT false,
    published_at TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_newsletters_period ON newsletters(period_start DESC);

-- ============================================
-- VIEWS: Convenient queries
-- ============================================

-- Recent high-signal events
CREATE VIEW recent_events AS
SELECT
    e.id,
    e.event_type,
    e.event_date,
    e.confidence,
    e.details,
    s.name as subject_name,
    s.entity_type as subject_type,
    o.name as object_name,
    o.entity_type as object_type,
    e.source_url
FROM events e
LEFT JOIN entities s ON e.subject_entity_id = s.id
LEFT JOIN entities o ON e.object_entity_id = o.id
WHERE e.confidence >= 0.7
  AND e.canonical_event_id IS NULL  -- Only canonical events
ORDER BY e.event_date DESC NULLS LAST, e.created_at DESC;

-- Company signals (for ranking)
CREATE VIEW company_signals AS
SELECT
    e.id,
    e.name,
    e.attributes,
    COUNT(DISTINCT CASE WHEN ev.event_type = 'funding' THEN ev.id END) as funding_count,
    COUNT(DISTINCT CASE WHEN ev.event_type = 'acquisition' AND ev.subject_entity_id = e.id THEN ev.id END) as acquisitions_made,
    COUNT(DISTINCT CASE WHEN ev.event_type = 'acquisition' AND ev.object_entity_id = e.id THEN ev.id END) as was_acquired,
    COUNT(DISTINCT CASE WHEN ev.event_type = 'hire' THEN ev.id END) as recent_hires,
    COUNT(DISTINCT CASE WHEN ev.event_type = 'departure' THEN ev.id END) as recent_departures,
    COUNT(DISTINCT CASE WHEN ev.event_type = 'layoff' THEN ev.id END) as layoffs,
    MAX(ev.event_date) as last_event_date
FROM entities e
LEFT JOIN events ev ON e.id IN (ev.subject_entity_id, ev.object_entity_id)
    AND ev.event_date >= CURRENT_DATE - INTERVAL '90 days'
WHERE e.entity_type = 'company'
  AND e.canonical_id IS NULL
GROUP BY e.id, e.name, e.attributes;

-- ============================================
-- FUNCTIONS: Utility functions
-- ============================================

-- Update timestamp trigger
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to all tables
CREATE TRIGGER update_feeds_timestamp BEFORE UPDATE ON feeds FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_articles_timestamp BEFORE UPDATE ON articles FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_entities_timestamp BEFORE UPDATE ON entities FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_events_timestamp BEFORE UPDATE ON events FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_relationships_timestamp BEFORE UPDATE ON relationships FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

---

## Phase 2: Deployment Infrastructure

### Option A: Railway (Recommended for Simplicity)

**Cost: ~$5-20/month**

```yaml
# railway.toml
[build]
builder = "NIXPACKS"
buildCommand = "pip install -r requirements.txt"

[deploy]
startCommand = "python scripts/kg_viewer.py"
healthcheckPath = "/health"
healthcheckTimeout = 100

[service]
internalPort = 8000
```

**Cron Jobs via Railway:**
```yaml
# railway.json (separate service)
{
  "services": {
    "web": {
      "source": ".",
      "start": "python scripts/kg_viewer.py"
    },
    "worker": {
      "source": ".",
      "start": "python scripts/worker.py",
      "cron": "0 */6 * * *"  # Every 6 hours
    }
  }
}
```

### Option B: Fly.io + Supabase

**Cost: ~$10-30/month**

```toml
# fly.toml
app = "recruiter-intelligence"
primary_region = "sjc"

[build]
  dockerfile = "Dockerfile"

[http_service]
  internal_port = 8000
  force_https = true

[env]
  DATABASE_URL = "postgresql://..."
```

### Option C: DigitalOcean Droplet + Managed DB

**Cost: ~$15-25/month**

- $6/month: Basic droplet
- $15/month: Managed PostgreSQL

---

## Phase 3: Worker Service (Cron Jobs)

### Create `scripts/worker.py`

```python
"""Production worker for scheduled pipeline tasks."""

import os
import sys
import asyncio
from datetime import datetime, timedelta
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline.daily import DailyPipeline
from src.storage.database import ArticleStorage
from src.knowledge_graph.graph import KnowledgeGraph

logger = structlog.get_logger()

class PipelineWorker:
    """Manages scheduled pipeline tasks."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.storage = ArticleStorage()
        self.kg = KnowledgeGraph()

    def setup_jobs(self):
        """Configure scheduled jobs."""

        # Fetch new articles every 6 hours
        self.scheduler.add_job(
            self.fetch_articles,
            CronTrigger(hour='*/6'),
            id='fetch_articles',
            name='Fetch RSS articles',
            replace_existing=True
        )

        # Classify and extract every 6 hours (30 min after fetch)
        self.scheduler.add_job(
            self.process_articles,
            CronTrigger(hour='*/6', minute=30),
            id='process_articles',
            name='Classify and extract articles',
            replace_existing=True
        )

        # Entity resolution daily at 2am
        self.scheduler.add_job(
            self.resolve_entities,
            CronTrigger(hour=2),
            id='resolve_entities',
            name='Resolve duplicate entities',
            replace_existing=True
        )

        # Enrichment daily at 3am
        self.scheduler.add_job(
            self.enrich_entities,
            CronTrigger(hour=3),
            id='enrich_entities',
            name='Enrich entities with web data',
            replace_existing=True
        )

        # Generate newsletter daily at 6am
        self.scheduler.add_job(
            self.generate_newsletter,
            CronTrigger(hour=6),
            id='generate_newsletter',
            name='Generate daily newsletter',
            replace_existing=True
        )

        # Health check every hour
        self.scheduler.add_job(
            self.health_check,
            CronTrigger(minute=0),
            id='health_check',
            name='Pipeline health check',
            replace_existing=True
        )

    async def fetch_articles(self):
        """Fetch new articles from all feeds."""
        logger.info("job_started", job="fetch_articles")
        try:
            pipeline = DailyPipeline(
                storage=self.storage,
                kg=self.kg,
                use_form_d=True,
                use_gdelt=False,  # Optional
                use_layoffs=True,
                use_yc=True
            )

            # Just fetch, don't process
            articles = await pipeline._fetch(days_back=1)
            saved = self.storage.save_articles(articles)

            logger.info("job_completed", job="fetch_articles",
                       fetched=len(articles), saved=saved)
        except Exception as e:
            logger.error("job_failed", job="fetch_articles", error=str(e))

    async def process_articles(self):
        """Classify and extract unprocessed articles."""
        logger.info("job_started", job="process_articles")
        try:
            pipeline = DailyPipeline(storage=self.storage, kg=self.kg)

            # Get unprocessed
            unprocessed = self.storage.get_unprocessed(limit=200)
            high_signal = pipeline._classify(unprocessed)

            # Extract high signal
            to_extract = self.storage.get_unextracted_high_signal(limit=100)
            extracted = await pipeline._extract(to_extract)

            logger.info("job_completed", job="process_articles",
                       classified=len(unprocessed),
                       high_signal=len(high_signal),
                       extracted=extracted)
        except Exception as e:
            logger.error("job_failed", job="process_articles", error=str(e))

    async def resolve_entities(self):
        """Run entity resolution."""
        logger.info("job_started", job="resolve_entities")
        try:
            from src.knowledge_graph.entity_resolver import EntityResolver
            resolver = EntityResolver(self.kg)
            result = resolver.run_all()
            logger.info("job_completed", job="resolve_entities", result=result)
        except Exception as e:
            logger.error("job_failed", job="resolve_entities", error=str(e))

    async def enrich_entities(self):
        """Enrich unenriched entities."""
        logger.info("job_started", job="enrich_entities")
        try:
            from src.enrichment.enrichment_service import EnrichmentService
            service = EnrichmentService(self.kg)
            # Enrich 20 entities per day (rate limiting)
            # Implementation here...
            await service.close()
            logger.info("job_completed", job="enrich_entities")
        except Exception as e:
            logger.error("job_failed", job="enrich_entities", error=str(e))

    async def generate_newsletter(self):
        """Generate daily newsletter."""
        logger.info("job_started", job="generate_newsletter")
        try:
            from src.newsletter.generator import NewsletterGenerator
            gen = NewsletterGenerator()
            newsletter = gen.generate_daily()
            html = gen.to_html(newsletter)
            # Save to database or send via email
            logger.info("job_completed", job="generate_newsletter")
        except Exception as e:
            logger.error("job_failed", job="generate_newsletter", error=str(e))

    async def health_check(self):
        """Check system health and alert if issues."""
        try:
            stats = self.storage.get_stats()
            kg_stats = self.kg.get_stats()

            # Check for backlogs
            pending = stats.get('unprocessed_articles', 0)
            if pending > 500:
                logger.warning("health_alert",
                             message=f"Large backlog: {pending} unprocessed articles")

            logger.debug("health_check",
                        articles=stats,
                        knowledge_graph=kg_stats)
        except Exception as e:
            logger.error("health_check_failed", error=str(e))

    def start(self):
        """Start the worker."""
        self.setup_jobs()
        self.scheduler.start()
        logger.info("worker_started", jobs=len(self.scheduler.get_jobs()))

    def stop(self):
        """Stop the worker."""
        self.scheduler.shutdown()
        logger.info("worker_stopped")


async def main():
    """Main entry point."""
    worker = PipelineWorker()
    worker.start()

    # Run immediately on startup
    await worker.fetch_articles()
    await worker.process_articles()

    # Keep running
    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Phase 4: Environment Configuration

### Create `.env.example`

```bash
# Database
DATABASE_URL=postgresql://user:password@host:5432/recruiter_intel
# Or for SQLite (local dev): sqlite:///data/recruiter_intel.db

# LLM Provider
LLM_PROVIDER=gemini  # gemini, anthropic, openai
GEMINI_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here  # Optional
OPENAI_API_KEY=your_key_here     # Optional

# Optional: SEC EDGAR
SEC_EDGAR_EMAIL=your_email@example.com

# Optional: Monitoring
SENTRY_DSN=your_sentry_dsn
SLACK_WEBHOOK_URL=your_slack_webhook  # For alerts

# Feature Flags
ENABLE_ENRICHMENT=true
ENABLE_FORM_D=true
ENABLE_LAYOFFS=true
ENABLE_YC=true

# Rate Limiting
MAX_ARTICLES_PER_RUN=200
LLM_REQUESTS_PER_MINUTE=20
ENRICHMENT_REQUESTS_PER_DAY=50
```

---

## Phase 5: Docker Configuration

### Create `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command (can be overridden)
CMD ["python", "scripts/kg_viewer.py"]
```

### Create `docker-compose.yml`

```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/recruiter_intel
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  worker:
    build: .
    command: python scripts/worker.py
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/recruiter_intel
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:15-alpine
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=recruiter_intel
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/schema.sql:/docker-entrypoint-initdb.d/schema.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
```

---

## Phase 6: Migration Strategy

### Step 1: Export from SQLite

```python
# scripts/migrate_to_postgres.py
"""Migrate data from SQLite to PostgreSQL."""

import sqlite3
import psycopg2
from psycopg2.extras import execute_values
import os

def migrate():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect('data/recruiter_intel.db')
    sqlite_conn.row_factory = sqlite3.Row

    # Connect to PostgreSQL
    pg_conn = psycopg2.connect(os.environ['DATABASE_URL'])
    pg_cursor = pg_conn.cursor()

    # Migrate articles
    print("Migrating articles...")
    sqlite_cursor = sqlite_conn.execute("SELECT * FROM raw_articles")
    articles = sqlite_cursor.fetchall()

    for article in articles:
        pg_cursor.execute("""
            INSERT INTO articles (url, title, content, summary, published_at,
                                 content_hash, is_high_signal, event_type,
                                 classification_status, extraction_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
        """, (
            article['url'],
            article['title'],
            article['content'],
            article['summary'],
            article['published_at'],
            article['content_hash'],
            article['is_high_signal'],
            article['event_type'],
            'classified' if article['processed'] else 'pending',
            'extracted' if article['extracted'] else 'pending'
        ))

    # Similar for entities and relationships...

    pg_conn.commit()
    print(f"Migrated {len(articles)} articles")

if __name__ == "__main__":
    migrate()
```

---

## Phase 7: Monitoring & Alerts

### Add Health Endpoint

```python
# In kg_viewer.py
@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers."""
    try:
        # Check database
        stats = storage.get_stats()

        # Check for recent activity
        recent_articles = stats.get('total_articles', 0)

        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "database": "connected",
            "articles": recent_articles
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )
```

### Slack Alerts (Optional)

```python
# src/monitoring/alerts.py
import httpx
import os

async def send_slack_alert(message: str, level: str = "warning"):
    """Send alert to Slack."""
    webhook_url = os.environ.get('SLACK_WEBHOOK_URL')
    if not webhook_url:
        return

    emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(level, "📢")

    async with httpx.AsyncClient() as client:
        await client.post(webhook_url, json={
            "text": f"{emoji} *Recruiter Intelligence*\n{message}"
        })
```

---

## Phase 8: Cost Estimation

### Monthly Costs

| Service | Provider | Cost |
|---------|----------|------|
| Web Server | Railway/Fly.io | $5-10 |
| Worker | Railway/Fly.io | $5-10 |
| PostgreSQL | Supabase Free / Railway | $0-15 |
| LLM API (Gemini) | Google | $5-20 |
| Domain (optional) | Cloudflare | $0-10 |
| **Total** | | **$15-65/month** |

### Cost Optimization Tips

1. **Use Gemini Flash** - Cheapest LLM option
2. **Batch extractions** - Reduce API calls
3. **Cache enrichments** - Don't re-enrich
4. **Supabase free tier** - 500MB database free
5. **Railway hobby plan** - $5/month includes cron

---

## Execution Checklist

### Phase 1: Database (Week 1)
- [ ] Create Supabase/Neon account
- [ ] Run schema.sql to create tables
- [ ] Migrate existing SQLite data
- [ ] Update connection strings in code

### Phase 2: Code Updates (Week 1)
- [ ] Add PostgreSQL support to storage layer
- [ ] Create worker.py with scheduler
- [ ] Add health endpoint
- [ ] Test locally with Docker Compose

### Phase 3: Deployment (Week 2)
- [ ] Create Railway/Fly.io account
- [ ] Deploy web service
- [ ] Deploy worker service
- [ ] Configure environment variables
- [ ] Set up domain (optional)

### Phase 4: Monitoring (Week 2)
- [ ] Set up Sentry for errors
- [ ] Configure Slack alerts
- [ ] Create dashboard for pipeline stats
- [ ] Test cron jobs are running

### Phase 5: Optimization (Week 3+)
- [ ] Monitor costs
- [ ] Tune batch sizes
- [ ] Add caching layer
- [ ] Optimize slow queries

---

## Future Enhancements

1. **Email delivery** - Send newsletters via email
2. **User accounts** - Multi-tenant support
3. **API access** - REST API for integrations
4. **Search** - Full-text search with filters
5. **Alerts** - Custom alerts for specific companies/people
6. **Mobile app** - React Native companion app
