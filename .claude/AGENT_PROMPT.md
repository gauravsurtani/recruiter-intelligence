# Agent Prompt: Recruiter Intelligence System Improvement

## Your Mission

You are improving a **daily news intelligence system for tech recruiters**. The system aggregates tech news about funding, acquisitions, layoffs, and executive movements. It is NOT a CRM or outreach tool — it's a "morning read" digest.

## Context

**Working Directory**: `/Users/gauravsurtani/projects/fir_recruiting/new_approach/recruiter-intelligence`

**Tech Stack**:
- Python 3.11+ with asyncio
- SQLite databases (recruiter_intel.db, knowledge_graph.db)
- FastAPI dashboard (scripts/kg_viewer.py)
- Claude API for LLM extraction (ANTHROPIC_API_KEY required)
- RSS feeds for data collection

**Key Files**:
| File | Purpose |
|------|---------|
| `scripts/run_daily.py` | Main pipeline entry |
| `src/pipeline/daily.py` | Pipeline orchestration |
| `src/extraction/llm_extractor.py` | Claude-based entity extraction |
| `src/storage/database.py` | Article storage operations |
| `src/knowledge_graph/graph.py` | Knowledge graph operations |
| `config/feeds.json` | RSS feed configuration |
| `.claude/IMPROVEMENT_PLAN.md` | Detailed improvement plan |

## Current Critical Issue

**401 high-signal articles are NOT extracted**. The system collects news but doesn't process it fast enough. The dashboard shows stale data.

Run this to verify:
```bash
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"
```

## Your Tasks (In Order)

### Task 1: Clear Extraction Backlog (CRITICAL)
```bash
cd /Users/gauravsurtani/projects/fir_recruiting/new_approach/recruiter-intelligence
source venv/bin/activate
PYTHONPATH=. python3 << 'EOF'
import asyncio, sqlite3
from src.extraction.llm_extractor import LLMExtractor
from src.knowledge_graph.graph import KnowledgeGraph

extractor = LLMExtractor()
kg = KnowledgeGraph()
conn = sqlite3.connect('data/recruiter_intel.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT id, title, content, summary, url FROM raw_articles
    WHERE is_high_signal = 1 AND extracted = 0
    ORDER BY published_at DESC
""")

async def extract_all():
    rows = cursor.fetchall()
    print(f"Processing {len(rows)} articles...")
    for i, (id, title, content, summary, url) in enumerate(rows):
        try:
            text = content or summary or ""
            if len(text) < 50:
                cursor.execute("UPDATE raw_articles SET extracted = 1 WHERE id = ?", (id,))
                continue
            result = await extractor.extract(title, text)
            if result.relationships:
                kg.add_extraction_result(result, source_url=url)
                print(f"[{i+1}/{len(rows)}] {title[:50]}... -> {len(result.relationships)} rels")
            cursor.execute("UPDATE raw_articles SET extracted = 1 WHERE id = ?", (id,))
            if i % 10 == 0:
                conn.commit()
        except Exception as e:
            print(f"Error {id}: {e}")
    conn.commit()

asyncio.run(extract_all())
EOF
```

**Success**: `SELECT COUNT(*) ... WHERE extracted=0` returns < 10

### Task 2: Create Extraction Validator
Create `src/extraction/validator.py` to filter out bad entities (HTML artifacts, news source names, gibberish). See IMPROVEMENT_PLAN.md for full code.

**Test**:
```python
from src.extraction.validator import is_valid_entity_name
assert not is_valid_entity_name("target")  # HTML artifact
assert is_valid_entity_name("OpenAI")  # Valid company
```

### Task 3: Clean Existing Bad Data
```sql
DELETE FROM kg_entities WHERE name LIKE '%target="_blank%';
DELETE FROM kg_entities WHERE name LIKE '%href=%';
DELETE FROM kg_entities WHERE name IN ('Reuters', 'TechCrunch', 'Bloomberg');
DELETE FROM kg_relationships WHERE subject_id NOT IN (SELECT id FROM kg_entities);
DELETE FROM kg_relationships WHERE object_id NOT IN (SELECT id FROM kg_entities);
```

### Task 4: Add Executive-Focused Feeds
Add to `config/feeds.json`:
```json
{
  "name": "Google News - Executive Appointed",
  "url": "https://news.google.com/rss/search?q=appointed+CEO+OR+CFO+OR+CTO+tech&hl=en-US&gl=US&ceid=US:en",
  "priority": 0,
  "event_types": ["executive_move"]
}
```

### Task 5: Increase Pipeline Batch Size
In `src/pipeline/daily.py`, change `max_articles` default from 50 to 200.

### Task 6: Verify Dashboard
```bash
python scripts/kg_viewer.py &
curl http://localhost:8000/ | grep -c "ACQUIRED"
```
Should show recent acquisitions.

## Quality Checks

After each task, run:
```bash
# Check extraction status
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"

# Check relationship counts
sqlite3 data/knowledge_graph.db "SELECT predicate, COUNT(*) FROM kg_relationships GROUP BY predicate ORDER BY COUNT(*) DESC"

# Check for bad entities
sqlite3 data/knowledge_graph.db "SELECT name FROM kg_entities WHERE name LIKE '%<%' OR name LIKE '%>%' OR LENGTH(name) < 2"
```

## Important Rules

1. **Process in order** - Task 1 is critical, do it first
2. **Test after each change** - Don't batch up changes
3. **Mark extracted AFTER success** - Never before
4. **Preserve source URLs** - Every relationship needs attribution
5. **Log errors** - Use structlog, don't swallow exceptions

## Expected Final State

| Metric | Target |
|--------|--------|
| Pending extractions | < 10 |
| Total relationships | > 400 |
| Bad entities | 0 |
| Feed errors | < 3 |

## Reference

Read `.claude/IMPROVEMENT_PLAN.md` for:
- Full code for all changes
- Detailed test cases
- Architecture explanation
- Troubleshooting guide

## When Done

Run the health check:
```bash
echo "=== Health Check ==="
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) as pending FROM raw_articles WHERE is_high_signal=1 AND extracted=0"
sqlite3 data/knowledge_graph.db "SELECT predicate, COUNT(*) FROM kg_relationships GROUP BY predicate ORDER BY COUNT(*) DESC"
sqlite3 data/knowledge_graph.db "SELECT COUNT(*) as bad_entities FROM kg_entities WHERE name LIKE '%<%'"
```

Expected output:
```
pending: 0-10
FUNDED_BY: 100+
ACQUIRED: 20+
HIRED_BY: 20+
bad_entities: 0
```
