# Recruiter Intelligence - Claude Context

## Project Purpose
A real-time intelligence platform for recruiters. Tracks funding, acquisitions, layoffs, and executive movements to help recruiters find candidates and target companies.

## CRITICAL: Pipeline Flow

```
RSS Feeds → raw_articles → Classification → Extraction → kg_entities/kg_relationships → Dashboard
```

### The Extraction Bug (FIXED)
**Problem**: Articles were marked `processed=1` after classification, but if extraction failed, they were never retried.

**Fix**: Added `extracted` column. Pipeline must check `WHERE is_high_signal=1 AND extracted=0`.

### How Data Flows to Dashboard

| Dashboard Section | Predicate | Query |
|-------------------|-----------|-------|
| Recent Acquisitions | `ACQUIRED` | `WHERE predicate='ACQUIRED'` |
| Recent Funding | `FUNDED_BY` | `WHERE predicate='FUNDED_BY'` |
| Recent Hires | `HIRED_BY` | `WHERE predicate='HIRED_BY'` |
| Recent Departures | `DEPARTED_FROM` | `WHERE predicate='DEPARTED_FROM'` |
| Companies Page | All | `kg_entities WHERE entity_type='company'` |
| Candidates Page | All | `kg_entities WHERE entity_type='person'` |

## Key Files

| File | Purpose |
|------|---------|
| `scripts/run_daily.py` | Main pipeline entry point |
| `scripts/kg_viewer.py` | Dashboard server (FastAPI) |
| `src/pipeline/daily.py` | Pipeline orchestration |
| `src/extraction/llm_extractor.py` | LLM-based entity/relationship extraction |
| `src/knowledge_graph/graph.py` | Knowledge graph operations |
| `config/feeds.json` | RSS feed URLs |

## Databases

### recruiter_intel.db
- `raw_articles`: Fetched articles with `processed`, `is_high_signal`, `extracted` flags
- `feed_stats`: Feed health tracking

### knowledge_graph.db
- `kg_entities`: Companies, people, investors (entity_type field)
- `kg_relationships`: Links between entities (predicate field)
- `kg_enrichment`: Web search enrichment data

## Common Tasks

### Check if dashboard data is stale
```bash
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"
```
If > 0, extraction is behind.

### Manual extraction of missed articles
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
AND event_type IN ('acquisition', 'funding', 'layoff', 'executive_move')
ORDER BY published_at DESC LIMIT 50
""")

async def extract():
    for id, title, content, summary in cursor.fetchall():
        try:
            result = await extractor.extract(title, content or summary or "")
            if result.relationships:
                kg.add_extraction_result(result)
            cursor.execute("UPDATE raw_articles SET extracted = 1 WHERE id = ?", (id,))
        except Exception as e:
            print(f"Error {id}: {e}")
    conn.commit()

asyncio.run(extract())
EOF
```

### Check knowledge graph stats
```bash
sqlite3 data/knowledge_graph.db "SELECT predicate, COUNT(*) FROM kg_relationships GROUP BY predicate ORDER BY COUNT(*) DESC"
```

### Check feed health
```bash
sqlite3 data/recruiter_intel.db "SELECT feed_name, total_articles, last_error FROM feed_stats WHERE last_error IS NOT NULL"
```

### Generate newsletter
```bash
source venv/bin/activate && PYTHONPATH=. python3 -c "
from src.newsletter.generator import NewsletterGenerator
gen = NewsletterGenerator()
newsletter = gen.generate_daily()
html = gen.to_html(newsletter)
with open('data/newsletter.html', 'w') as f:
    f.write(html)
print('Saved to data/newsletter.html')
"
```

## Entity Types
- `company`: Tech companies (Stripe, OpenAI, etc.)
- `person`: Executives, founders (Sam Altman, etc.)
- `investor`: VCs, funds (Sequoia, Y Combinator)
- `group`: Generic groups like "employees" for layoff relationships

## Relationship Types (Predicates)

| Predicate | Meaning | Example |
|-----------|---------|---------|
| `ACQUIRED` | Acquirer bought target | CrowdStrike → SGNL |
| `FUNDED_BY` | Company received funding from | xAI → Nvidia |
| `HIRED_BY` | Person joined company | C.J. Mahoney → Meta |
| `DEPARTED_FROM` | Person left company | C.J. Mahoney → Microsoft |
| `LAID_OFF` | Company laid off employees | Amazon → employees |
| `FOUNDED` | Person founded company | Sam Altman → OpenAI |
| `CEO_OF` | Person is CEO of | Elon Musk → xAI |
| `INVESTED_IN` | Investor invested in | Sequoia → Stripe |

## Known Issues

### Issue 1: Extraction not running
**Symptom**: Dashboard shows old acquisitions/funding
**Check**: `SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0`
**Fix**: Run manual extraction script above

### Issue 2: Invalid entities (HTML artifacts)
**Symptom**: Entity names like `target="_blank">...`
**Fix**: `_sanitize_name()` in `src/newsletter/generator.py`

### Issue 3: Layoffs.fyi blocked
**Symptom**: No layoff data
**Fix**: Fallback data in `src/ingestion/layoffs_scraper.py`

### Issue 4: YC API blocked
**Symptom**: No YC companies
**Fix**: Fallback data in `src/ingestion/yc_scraper.py`

## Dashboard URLs

| URL | Page |
|-----|------|
| `http://localhost:8000/` | Main dashboard |
| `http://localhost:8000/companies` | Companies ranked by signals |
| `http://localhost:8000/candidates` | People available for recruiting |
| `http://localhost:8000/newsletter` | Generated newsletter |
| `http://localhost:8000/search?q=...` | Search entities |

## Expected Data After Pipeline Run

```
Entities: 1500+
Relationships: 300+
- ACQUIRED: 20+
- FUNDED_BY: 80+
- HIRED_BY: 15+
- DEPARTED_FROM: 15+
- LAID_OFF: 5+
- FOUNDED: 50+
```

## Running the System

```bash
# 1. Activate venv
source venv/bin/activate

# 2. Run pipeline
python scripts/run_daily.py

# 3. Check extraction happened
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"
# Should be 0 or low number

# 4. Start dashboard
python scripts/kg_viewer.py

# 5. Open http://localhost:8000
```

## API Keys Required
- `ANTHROPIC_API_KEY`: For LLM extraction
- `SEC_EDGAR_EMAIL`: For SEC API (optional)
