-- Recruiter Intelligence - PostgreSQL Schema
-- Run this to initialize the production database

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For fuzzy text search

-- ============================================
-- SOURCES: Where data comes from
-- ============================================

CREATE TABLE IF NOT EXISTS feeds (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL UNIQUE,
    url TEXT NOT NULL,
    feed_type VARCHAR(50) NOT NULL DEFAULT 'rss',
    priority INTEGER DEFAULT 1,
    event_types TEXT[],

    is_active BOOLEAN DEFAULT true,
    last_fetch_at TIMESTAMP WITH TIME ZONE,
    last_success_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    total_articles INTEGER DEFAULT 0,
    fetch_interval_minutes INTEGER DEFAULT 60,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================
-- ARTICLES: Raw ingested content
-- ============================================

CREATE TABLE IF NOT EXISTS articles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    feed_id UUID REFERENCES feeds(id),

    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content TEXT,
    summary TEXT,
    content_hash VARCHAR(64) UNIQUE,
    title_hash VARCHAR(64),

    published_at TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    classification_status VARCHAR(20) DEFAULT 'pending',
    classified_at TIMESTAMP WITH TIME ZONE,
    extraction_status VARCHAR(20) DEFAULT 'pending',
    extracted_at TIMESTAMP WITH TIME ZONE,
    extraction_error TEXT,

    event_type VARCHAR(50),
    is_high_signal BOOLEAN DEFAULT false,
    classification_confidence FLOAT,
    matched_keywords TEXT[],

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(classification_status, extraction_status);
CREATE INDEX IF NOT EXISTS idx_articles_high_signal ON articles(is_high_signal) WHERE is_high_signal = true;
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_event_type ON articles(event_type);

-- ============================================
-- ENTITIES: Companies, People, Investors
-- ============================================

CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    name VARCHAR(500) NOT NULL,
    normalized_name VARCHAR(500) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,

    canonical_id UUID REFERENCES entities(id),
    aliases TEXT[] DEFAULT '{}',
    attributes JSONB DEFAULT '{}',

    enrichment_status VARCHAR(20) DEFAULT 'pending',
    enriched_at TIMESTAMP WITH TIME ZONE,
    enrichment_data JSONB DEFAULT '{}',

    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    mention_count INTEGER DEFAULT 1,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entities_normalized ON entities(normalized_name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_id) WHERE canonical_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_entities_name_trgm ON entities USING gin(name gin_trgm_ops);

-- ============================================
-- EVENTS: Discrete business events
-- ============================================

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    event_type VARCHAR(50) NOT NULL,
    subject_entity_id UUID REFERENCES entities(id),
    object_entity_id UUID REFERENCES entities(id),
    details JSONB DEFAULT '{}',

    event_date DATE,
    event_date_precision VARCHAR(10) DEFAULT 'day',

    source_article_id UUID REFERENCES articles(id),
    source_url TEXT,
    source_type VARCHAR(50),

    confidence FLOAT DEFAULT 0.8,
    is_verified BOOLEAN DEFAULT false,
    verified_by TEXT,

    event_hash VARCHAR(64),
    canonical_event_id UUID REFERENCES events(id),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_events_object ON events(object_entity_id);
CREATE INDEX IF NOT EXISTS idx_events_hash ON events(event_hash);

-- ============================================
-- RELATIONSHIPS: Entity connections
-- ============================================

CREATE TABLE IF NOT EXISTS relationships (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    subject_id UUID NOT NULL REFERENCES entities(id),
    predicate VARCHAR(50) NOT NULL,
    object_id UUID NOT NULL REFERENCES entities(id),

    context TEXT,
    event_id UUID REFERENCES events(id),
    source_article_id UUID REFERENCES articles(id),
    source_url TEXT,

    confidence FLOAT DEFAULT 0.8,
    extraction_method VARCHAR(50),

    start_date DATE,
    end_date DATE,
    is_current BOOLEAN DEFAULT true,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(subject_id, predicate, object_id, source_article_id)
);

CREATE INDEX IF NOT EXISTS idx_relationships_subject ON relationships(subject_id);
CREATE INDEX IF NOT EXISTS idx_relationships_object ON relationships(object_id);
CREATE INDEX IF NOT EXISTS idx_relationships_predicate ON relationships(predicate);

-- ============================================
-- PIPELINE: Job tracking
-- ============================================

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    run_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'running',

    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,

    articles_fetched INTEGER DEFAULT 0,
    articles_classified INTEGER DEFAULT 0,
    articles_extracted INTEGER DEFAULT 0,
    entities_created INTEGER DEFAULT 0,
    events_created INTEGER DEFAULT 0,
    relationships_created INTEGER DEFAULT 0,

    error_message TEXT,
    error_count INTEGER DEFAULT 0,

    llm_tokens_used INTEGER DEFAULT 0,
    llm_cost_usd DECIMAL(10, 4) DEFAULT 0,

    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_type ON pipeline_runs(run_type, started_at DESC);

-- ============================================
-- NEWSLETTERS: Generated content
-- ============================================

CREATE TABLE IF NOT EXISTS newsletters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    period_type VARCHAR(20) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,

    title VARCHAR(500),
    summary TEXT,
    html_content TEXT,
    markdown_content TEXT,

    funding_count INTEGER DEFAULT 0,
    acquisition_count INTEGER DEFAULT 0,
    hire_count INTEGER DEFAULT 0,
    departure_count INTEGER DEFAULT 0,
    layoff_count INTEGER DEFAULT 0,

    is_published BOOLEAN DEFAULT false,
    published_at TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_newsletters_period ON newsletters(period_start DESC);

-- ============================================
-- VIEWS
-- ============================================

CREATE OR REPLACE VIEW recent_events AS
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
  AND e.canonical_event_id IS NULL
ORDER BY e.event_date DESC NULLS LAST, e.created_at DESC;

CREATE OR REPLACE VIEW company_signals AS
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
-- TRIGGERS
-- ============================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_feeds_timestamp ON feeds;
CREATE TRIGGER update_feeds_timestamp BEFORE UPDATE ON feeds FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_articles_timestamp ON articles;
CREATE TRIGGER update_articles_timestamp BEFORE UPDATE ON articles FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_entities_timestamp ON entities;
CREATE TRIGGER update_entities_timestamp BEFORE UPDATE ON entities FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_events_timestamp ON events;
CREATE TRIGGER update_events_timestamp BEFORE UPDATE ON events FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_relationships_timestamp ON relationships;
CREATE TRIGGER update_relationships_timestamp BEFORE UPDATE ON relationships FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================
-- SEED DATA: Default feeds
-- ============================================

INSERT INTO feeds (name, url, feed_type, priority, event_types) VALUES
('Google News - Startup Funding', 'https://news.google.com/rss/search?q=startup+funding+raised+million&hl=en-US&gl=US&ceid=US:en', 'rss', 0, ARRAY['funding', 'startup']),
('Google News - Tech Acquisition', 'https://news.google.com/rss/search?q=tech+company+acquisition+deal&hl=en-US&gl=US&ceid=US:en', 'rss', 0, ARRAY['acquisition']),
('Google News - Tech Layoffs', 'https://news.google.com/rss/search?q=tech+layoffs+2026&hl=en-US&gl=US&ceid=US:en', 'rss', 0, ARRAY['layoff']),
('Google News - Executive Appointed', 'https://news.google.com/rss/search?q=appointed+CEO+OR+CFO+OR+CTO+tech&hl=en-US&gl=US&ceid=US:en', 'rss', 0, ARRAY['executive_move']),
('TechCrunch', 'https://techcrunch.com/feed/', 'rss', 0, ARRAY['funding', 'acquisition', 'startup']),
('Crunchbase News', 'https://news.crunchbase.com/feed/', 'rss', 0, ARRAY['funding', 'acquisition', 'ipo', 'layoff']),
('Axios', 'https://api.axios.com/feed/', 'rss', 0, ARRAY['funding', 'acquisition', 'executive_move']),
('GeekWire', 'https://www.geekwire.com/feed/', 'rss', 0, ARRAY['funding', 'startup', 'executive_move', 'acquisition'])
ON CONFLICT (name) DO NOTHING;
