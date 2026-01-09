# Recruiter Intelligence

A real-time intelligence platform for recruiters tracking funding, acquisitions, layoffs, and executive movements in tech.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA SOURCES                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  RSS Feeds (32)  │  SEC Form D  │  Layoffs.fyi  │  YC Directory             │
└────────┬─────────┴──────┬───────┴───────┬───────┴────┬──────────────────────┘
         │                │               │            │
         ▼                ▼               ▼            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                               PIPELINE                                       │
│  ┌──────────┐    ┌───────────┐    ┌───────────┐    ┌──────────┐             │
│  │ 1. FETCH │───▶│2. CLASSIFY│───▶│3. EXTRACT │───▶│4. ENRICH │             │
│  └──────────┘    └───────────┘    └───────────┘    └──────────┘             │
│       │               │                │                │                    │
│       ▼               ▼                ▼                ▼                    │
│  raw_articles    is_high_signal   kg_entities     kg_enrichment             │
│  (2000+ articles) event_type     kg_relationships                           │
│                  processed=1      extracted=1                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           KNOWLEDGE GRAPH                                    │
│  ENTITIES: company, person, investor, group                                  │
│  RELATIONSHIPS: ACQUIRED, FUNDED_BY, HIRED_BY, DEPARTED_FROM, LAID_OFF      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DASHBOARD                                       │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐│
│  │   Stats    │ │Acquisitions│ │  Funding   │ │   Hires    │ │ Departures ││
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └────────────┘│
│  ┌──────────────────────┐ ┌──────────────────────┐ ┌─────────────────────┐ │
│  │   Companies Page     │ │   Candidates Page    │ │     Newsletter      │ │
│  └──────────────────────┘ └──────────────────────┘ └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Activate environment
source venv/bin/activate

# Run the daily pipeline (fetches news, extracts entities)
python scripts/run_daily.py

# Start the dashboard
python scripts/kg_viewer.py
# Open http://localhost:8000
```

## Pipeline Flow (CRITICAL)

### Step 1: Fetch
- Fetches from 32 RSS feeds (`config/feeds.json`)
- Also fetches SEC Form D, Layoffs.fyi, YC Directory
- Stores in `raw_articles` table

### Step 2: Classify
- Keywords match → `event_type`: funding, acquisition, layoff, executive_move
- Sets `is_high_signal=1` for relevant articles
- Sets `processed=1` after classification

### Step 3: Extract (MOST CRITICAL)
- **Only processes WHERE `is_high_signal=1` AND `extracted=0`**
- Uses LLM (Claude) to extract entities and relationships
- Stores in `kg_entities` and `kg_relationships`
- **Sets `extracted=1` AFTER successful extraction**

### Step 4: Enrich
- Web search adds context to entities
- Stores in `kg_enrichment`

## Databases

### recruiter_intel.db
```sql
raw_articles (
    id, title, content, url, source,
    published_at, fetched_at,
    processed,        -- 1 after classification
    is_high_signal,   -- 1 if relevant to recruiting
    event_type,       -- funding, acquisition, layoff, executive_move
    extracted         -- 1 after LLM extraction (CRITICAL!)
)
```

### knowledge_graph.db
```sql
kg_entities (id, name, normalized_name, entity_type, attributes_json)
kg_relationships (subject_id, predicate, object_id, confidence, context, source_url)
kg_enrichment (entity_id, source, enrichment_json)
```

## Relationship Types

| Predicate | Subject | Object | Dashboard Section |
|-----------|---------|--------|-------------------|
| `ACQUIRED` | Acquirer | Target | Recent Acquisitions |
| `FUNDED_BY` | Company | Investor | Recent Funding |
| `HIRED_BY` | Person | Company | Recent Hires |
| `DEPARTED_FROM` | Person | Company | Recent Departures |
| `LAID_OFF` | Company | employees | Layoffs section |
| `FOUNDED` | Person | Company | Available Talent |
| `CEO_OF` | Person | Company | Executive info |

## Dashboard Pages

| Page | URL | Data Source |
|------|-----|-------------|
| Dashboard | `/` | All relationship types |
| Companies | `/companies` | kg_entities WHERE entity_type='company' |
| Candidates | `/candidates` | kg_entities WHERE entity_type='person' |
| Newsletter | `/newsletter` | Generated from kg_relationships |

## Manual Extraction (If Pipeline Stalls)

```bash
source venv/bin/activate && PYTHONPATH=. python3 << 'EOF'
import asyncio, sqlite3
from src.extraction.llm_extractor import LLMExtractor
from src.knowledge_graph.graph import KnowledgeGraph

extractor = LLMExtractor()
kg = KnowledgeGraph()
conn = sqlite3.connect('data/recruiter_intel.db')

cursor = conn.cursor()
cursor.execute("""
SELECT id, title, content, summary FROM raw_articles
WHERE is_high_signal = 1 AND extracted = 0
ORDER BY published_at DESC LIMIT 50
""")

async def extract():
    for id, title, content, summary in cursor.fetchall():
        result = await extractor.extract(title, content or summary or "")
        if result.relationships:
            kg.add_extraction_result(result)
        cursor.execute("UPDATE raw_articles SET extracted = 1 WHERE id = ?", (id,))
    conn.commit()

asyncio.run(extract())
print("Done!")
EOF
```

## Known Issues & Fixes

| Issue | Symptom | Fix |
|-------|---------|-----|
| Extraction not tracked | Dashboard shows old data | Added `extracted` column |
| Invalid entities | HTML artifacts in entity names | `_sanitize_name()` validation |
| Stale RSS news | Same news repeating | Removed `when:30d` from feeds |
| Layoffs.fyi 404 | No layoff data | Fallback data in scraper |
| YC API 403 | No YC companies | Fallback data in scraper |

## Troubleshooting

### Dashboard shows no/stale data
```bash
# Check unextracted articles
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"

# If > 0, run manual extraction above
```

### Check what's in knowledge graph
```bash
sqlite3 data/knowledge_graph.db "SELECT predicate, COUNT(*) FROM kg_relationships GROUP BY predicate"
```

### Check feed health
```bash
sqlite3 data/recruiter_intel.db "SELECT feed_name, total_articles, last_error FROM feed_stats ORDER BY total_articles DESC"
```

## File Structure

```
recruiter-intelligence/
├── config/feeds.json           # RSS feed URLs
├── data/
│   ├── recruiter_intel.db      # Articles, feed stats
│   ├── knowledge_graph.db      # Entities, relationships
│   └── newsletter.html         # Generated newsletter
├── scripts/
│   ├── run_daily.py            # Pipeline runner
│   └── kg_viewer.py            # Dashboard server
├── src/
│   ├── ingestion/              # RSS, SEC, layoffs, YC scrapers
│   ├── extraction/             # LLM extractor
│   ├── knowledge_graph/        # Graph operations
│   ├── newsletter/             # Newsletter generator
│   └── pipeline/daily.py       # Pipeline orchestration
└── .claude/                    # Claude context files
```

## Expected Pipeline Output

```
RECRUITER INTELLIGENCE PIPELINE
==================================================
Articles: 300+ fetched, 50+ new
High signal: 50+
Relationships extracted: 50+
KNOWLEDGE GRAPH: 1500+ entities, 300+ relationships
```

## License

MIT
