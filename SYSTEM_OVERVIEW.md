# Recruiter Intelligence System - Complete Overview

## What This System Does

This is an **always-on startup intelligence system** that automatically monitors news sources, SEC filings, and other data streams to identify recruiting opportunities. It tracks:

- **Funding rounds** (seed through Series C+)
- **Acquisitions and exits**
- **Executive moves** (hires, departures, promotions)
- **Layoffs** (potential talent availability)

The system is designed for recruiters to identify:
1. **Companies actively hiring** (recently funded, acquiring)
2. **Available candidates** (departing execs, layoff victims)
3. **Market movements** (who's growing, who's shrinking)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     DATA SOURCES                                 │
├─────────────────────────────────────────────────────────────────┤
│  RSS Feeds (24)     SEC EDGAR Form D      GDELT News API        │
│  - TechCrunch       - Legal filings       - Historical depth    │
│  - Crunchbase       - 60-80% of raises    - Back to 2017        │
│  - Bloomberg        - Directors/officers  - 100+ languages      │
│  - VentureBeat      - Investor counts     - Sentiment data      │
│  - GeekWire         - Industry codes      - Entity extraction   │
│  - Axios            └─────────────────────────────────────────┐ │
│  - Reuters                                                    │ │
│  - PR Newswire                                               │ │
│  - Business Wire                                             │ │
│  - Hacker News                                               │ │
└──────────────────────────────────────────────────────────────┘ │
                              │                                   │
                              ▼                                   │
┌─────────────────────────────────────────────────────────────────┐
│                     PROCESSING PIPELINE                          │
├─────────────────────────────────────────────────────────────────┤
│  1. Fetch           2. Classify        3. Extract               │
│  - Async RSS        - Keyword regex    - spaCy NER (fast)       │
│  - Rate limiting    - 5 event types    - LLM fallback           │
│  - Deduplication    - High-signal      - Entity extraction      │
│                       filtering        - Relationship mapping    │
│                                                                  │
│  4. Cross-Reference    5. Resolve         6. Enrich             │
│  - News ↔ Form D      - Deduplicate       - Gemini web search   │
│  - Confidence boost   - Merge aliases     - LinkedIn profiles   │
│  - Validation         - Fix types         - Company data        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     KNOWLEDGE GRAPH                              │
├─────────────────────────────────────────────────────────────────┤
│  Entities:           Relationships:        Enrichment:          │
│  - Companies         - ACQUIRED            - Employee count     │
│  - People            - FUNDED_BY           - Total funding      │
│  - Investors         - HIRED_BY            - Industry/sector    │
│                      - DEPARTED_FROM       - LinkedIn URLs      │
│                      - CEO_OF/CTO_OF       - Recent rounds      │
│                      - FOUNDED             - Hiring signals     │
│                      - LAID_OFF                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     WEB UI (kg_viewer.py)                        │
├─────────────────────────────────────────────────────────────────┤
│  Dashboard │ Feeds │ Timeline │ Search │ Entities │ Companies   │
│                                                                  │
│  - Real-time stats      - Company scores (recruiting signals)   │
│  - Event timeline       - Candidate scores (availability)       │
│  - Entity details       - Tag management for workflow           │
│  - Feed management      - Enrichment display                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implemented Features

### 1. Multi-Source Data Ingestion

#### RSS Feeds (24 configured)
| Priority | Sources |
|----------|---------|
| High (0) | Crunchbase News, TechCrunch, VentureBeat, TechMeme, Bloomberg Tech, Axios, WSJ Tech, GeekWire |
| Medium (1) | SEC EDGAR 8-K, Fortune, Reuters, PR Newswire, Business Wire, Forbes, The Verge, Wired |
| Low (2) | Hacker News, Ars Technica, Yahoo Finance |

#### SEC EDGAR Form D API
- **What it captures**: Legal funding disclosures required within 15 days of securities sales
- **Data extracted**:
  - Company name, state of incorporation
  - Total offering amount and amount sold
  - Number of investors (accredited vs non-accredited)
  - Directors and executive officers
  - Industry classification
- **Coverage**: 60-80% of Reg D raises
- **Confidence**: 95% (legal source)

#### GDELT News API (Optional)
- **What it provides**: Historical news archives back to 2017
- **Features**: Entity extraction, sentiment analysis, 100+ languages
- **Use case**: Supplementary source for broader coverage

### 2. Hybrid Extraction (spaCy + LLM)

**Two-pass extraction to reduce costs by 60-80%:**

```
Article → spaCy NER (fast, local)
              │
              ├─► Clear case (single company + amount + round type)
              │   → Return spaCy result directly
              │
              └─► Ambiguous case (multiple orgs, unclear parties)
                  → Route to LLM for relationship extraction
```

**spaCy extracts:**
- MONEY entities (funding amounts)
- ORG entities (companies, investors)
- PERSON entities (executives)
- DATE entities (event dates)

**LLM handles:**
- Acquisition direction (who acquired whom)
- Executive moves (person → company → role)
- Complex multi-party relationships

### 3. Cross-Referencing (News ↔ Form D)

**Purpose**: Validate news-extracted funding with legal SEC filings

**Matching criteria:**
- Company name similarity > 85%
- Date proximity within 30 days
- Amount compatibility (20% tolerance)

**Benefits:**
- Catches news exaggeration vs actual raise
- Fills gaps (investor counts, industry codes)
- Boosts confidence: News + Form D agreement → 95%+ confidence

### 4. Entity Resolution & Deduplication

**Automatic handling of:**
- Company suffixes: "Nvidia Corp." → "Nvidia"
- Known aliases: "Facebook" ↔ "Meta Platforms"
- Invalid entities: Removes "investor", "company", "startup"
- Type inference: Uses relationships to fix "unknown" types

### 5. Source Quality Validation

**Three-tier confidence scoring:**

| Tier | Sources | Base Confidence |
|------|---------|-----------------|
| 1 | Bloomberg, WSJ, Reuters, SEC, Crunchbase | 95% |
| 2 | TechCrunch, GeekWire, VentureBeat, Axios | 85% |
| 3 | PR Newswire, Business Wire, aggregators | 70% |

### 6. Enrichment via Web Search

**Company enrichment extracts:**
- Employee count and growth trends
- Total funding and recent rounds
- Headquarters location
- Industry and sub-industry
- Recruiting signals (is_hiring, job_openings)

**Person enrichment extracts:**
- Current title and company
- Executive level (C-level, VP, Director)
- Previous companies
- Education background
- LinkedIn/GitHub URLs

---

## Database Schema

### Core Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `raw_articles` | Fetched articles | url, title, content, published_at, is_high_signal |
| `kg_entities` | Companies/People/Investors | name, entity_type, mention_count |
| `kg_relationships` | Connections between entities | subject_id, predicate, object_id, confidence |
| `kg_enrichment` | External data per entity | entity_id, source, data_json |
| `kg_aliases` | Name variations | entity_id, alias |
| `feed_stats` | Per-feed performance | feed_name, total_articles, success_rate |

### Relationship Types

| Predicate | Subject | Object | Example |
|-----------|---------|--------|---------|
| ACQUIRED | Company | Company | Microsoft ACQUIRED Activision |
| FUNDED_BY | Company | Investor | Stripe FUNDED_BY Sequoia |
| HIRED_BY | Person | Company | John Smith HIRED_BY Google |
| DEPARTED_FROM | Person | Company | Jane Doe DEPARTED_FROM Meta |
| CEO_OF | Person | Company | Satya Nadella CEO_OF Microsoft |
| FOUNDED | Person | Company | Elon Musk FOUNDED SpaceX |
| LAID_OFF | Company | Person | Twitter LAID_OFF employees |

---

## Web UI Pages

### Dashboard (`/`)
- Total entities, relationships, articles
- High-signal article count
- Enrichment coverage percentage
- Recent events by type (acquisitions, funding, hires, departures)

### Feeds (`/feeds`)
- All configured feeds with health status
- Toggle enable/disable
- Add new feeds with URL validation
- Suggested feeds for one-click add

### Timeline (`/timeline`)
- Chronological view of all events
- Filter by days back and event type
- Color-coded by event category

### Search (`/search`)
- Free-text entity search
- Filter by type and date range
- Shows both entities and relationships

### Entities (`/entities`)
- All extracted entities
- Filter by type (company/person/investor)
- Enrichment status indicator

### Entity Detail (`/entity/{id}`)
- Full entity profile
- Enrichment data display
- Related relationships
- Tag management
- External research links

### Companies (`/companies`)
- Recruiter-focused company ranking
- Scored by: funding, acquisitions, hiring, departures, layoffs
- Identifies companies actively building teams

### Candidates (`/candidates`)
- Recruiter-focused candidate ranking
- Scored by: departures, acquired company exposure, executive level
- Identifies potentially available talent

---

## Running the System

### Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt

# Download spaCy model (for hybrid extraction)
python -m spacy download en_core_web_lg

# Set environment variables
export RI_GEMINI_API_KEY=your_key_here
# OR
export RI_ANTHROPIC_API_KEY=your_key_here
```

### Run the Pipeline

```bash
# Basic run (1 day lookback)
python scripts/run_daily.py

# Extended run (7 days)
python scripts/run_daily.py --days 7

# With all features enabled
python -c "
import asyncio
from src.pipeline.daily import run_daily_pipeline

stats = asyncio.run(run_daily_pipeline(
    days_back=7,
    use_form_d=True,    # SEC Form D filings
    use_spacy=True,     # Hybrid extraction
    use_gdelt=False,    # GDELT (optional)
    use_cross_ref=True, # Cross-referencing
))
print(stats)
"
```

### View the Dashboard

```bash
python scripts/kg_viewer.py
# Open http://localhost:8000
```

---

## Expected Outcomes

| Metric | Without Enhancements | With All Features |
|--------|---------------------|-------------------|
| Funding coverage | ~50% | 80-90% |
| LLM API costs | $X | $0.2-0.4X (60-80% reduction) |
| Data validation | News only | News + Legal verification |
| Historical depth | Current feeds only | Back to 2017 |
| Entity accuracy | Basic dedup | Cross-referenced + validated |

---

## Cost Analysis

| Component | Cost | Value |
|-----------|------|-------|
| RSS Feeds | Free | 40-50% of announced events |
| SEC EDGAR | Free | 60-80% of Reg D raises |
| GDELT | Free | Historical depth to 2017 |
| spaCy | Free | 60-80% LLM cost reduction |
| Gemini API | ~$0.30-0.60/1000 articles | Extraction + enrichment |

**Target: 80-90% coverage for under $50/month**

---

## Future Enhancements

1. **Newsletter-to-RSS conversion** - Axios Pro Rata, StrictlyVC
2. **WARN Act integration** - State-level layoff filings
3. **LinkedIn integration** - Executive move tracking
4. **Scheduled automation** - Cron-based daily runs
5. **Slack/Email alerts** - Notify on high-value events
