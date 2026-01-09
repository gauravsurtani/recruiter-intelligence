# Recruiter Intelligence - Improvement Plan & Agent Prompt

## Project Vision

**What This Is**: A daily intelligence digest for tech recruiters. NOT a CRM, NOT an outreach tool. It's a "morning read" that tells recruiters:
- Who raised funding (hiring signals)
- Who got acquired (talent displacement)
- Who's laying off (available talent pool)
- Who moved where (executive movements)

**Core User Story**: "As a tech recruiter, I want to spend 5 minutes each morning scanning what happened in tech yesterday so I know which companies are growing, shrinking, or changing leadership."

---

## Current State Assessment

### What's Working
- 32 RSS feeds collecting articles
- Classification correctly identifies event types
- LLM extraction produces relationships
- Dashboard displays data
- Newsletter generator exists

### What's Broken
| Problem | Impact | Evidence |
|---------|--------|----------|
| 401 articles pending extraction | Stale data | `SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0` |
| Pipeline runs 1x/day | 24hr delay | Daily cron only |
| Executive moves undercovered | Missing key intel | Only 43 hire/departure relationships |
| Some extractions are wrong | Bad data | "Nvidia ACQUIRED Groq" from unrelated article |
| Layoffs.fyi blocked | No layoff data | 403 errors, using stale fallback |
| YC scraper blocked | No startup data | 403 errors, using stale fallback |

### Database Stats
```
recruiter_intel.db:
- raw_articles: ~2,200 articles
- 609 classified as high-signal
- 401 NOT extracted (the problem!)

knowledge_graph.db:
- kg_entities: 1,855 (1,477 people, 343 companies)
- kg_relationships: 285 total
  - FUNDED_BY: 99
  - FOUNDED: 65
  - CEO_OF: 31
  - ACQUIRED: 14
  - HIRED_BY: 13
  - DEPARTED_FROM: 15
```

---

## Execution Plan

### Phase 1: Fix the Bleeding (Critical - Do First)
**Goal**: Clear extraction backlog, ensure pipeline keeps pace

#### Task 1.1: Process All Pending Extractions
```bash
# Run this to extract all pending high-signal articles
source venv/bin/activate && PYTHONPATH=. python3 << 'EOF'
import asyncio
import sqlite3
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
                print(f"[{i+1}/{len(rows)}] {title[:50]}... -> {len(result.relationships)} relationships")
            else:
                print(f"[{i+1}/{len(rows)}] {title[:50]}... -> no relationships")

            cursor.execute("UPDATE raw_articles SET extracted = 1 WHERE id = ?", (id,))

            if i % 10 == 0:
                conn.commit()

        except Exception as e:
            print(f"Error on {id}: {e}")

    conn.commit()
    print("Done!")

asyncio.run(extract_all())
EOF
```

**Success Criteria**:
```sql
-- This should return 0 or very low number
SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0;
```

#### Task 1.2: Add Pipeline Scheduling (2x Daily)
Create `scripts/schedule_pipeline.py`:
```python
"""Schedule pipeline to run twice daily."""
import schedule
import time
import subprocess
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_pipeline():
    logger.info("Starting pipeline run...")
    result = subprocess.run(
        ["python", "scripts/run_daily.py"],
        capture_output=True,
        text=True
    )
    logger.info(f"Pipeline completed: {result.returncode}")
    if result.stderr:
        logger.error(result.stderr)

# Run at 6am and 6pm
schedule.every().day.at("06:00").do(run_pipeline)
schedule.every().day.at("18:00").do(run_pipeline)

if __name__ == "__main__":
    logger.info("Pipeline scheduler started")
    run_pipeline()  # Run immediately on start
    while True:
        schedule.run_pending()
        time.sleep(60)
```

**Test Case**:
```bash
# Run and verify it executes
python scripts/schedule_pipeline.py &
# Check logs for "Pipeline completed"
```

#### Task 1.3: Increase Extraction Batch Size
Edit `src/pipeline/daily.py`:
```python
# Change from:
async def run(self, days_back: int = 1, max_articles: int = None) -> dict:
    max_articles = max_articles or settings.max_articles_per_run  # Usually 50

# Change to:
async def run(self, days_back: int = 1, max_articles: int = None) -> dict:
    max_articles = max_articles or 200  # Process more per run
```

**Test Case**:
```bash
# Run pipeline and check it processes more
python scripts/run_daily.py
# Should see "articles_to_extract" > 50 in logs
```

---

### Phase 2: Improve Data Quality
**Goal**: Better extraction accuracy, remove bad data

#### Task 2.1: Add Extraction Validation
Create `src/extraction/validator.py`:
```python
"""Validate extracted relationships before storing."""
import re
from typing import List, Tuple

# Known false positives to reject
INVALID_ENTITIES = {
    "target", "blank", "href", "http", "https", "www",
    "Reuters", "TechCrunch", "Bloomberg", "Fortune",
    "The Wall Street Journal", "CNBC", "Yahoo Finance"
}

INVALID_PATTERNS = [
    r'^[A-Z]{20,}$',  # All caps gibberish
    r'^CBM[iI]',  # Google News URL artifacts
    r'^\d+$',  # Just numbers
    r'^[a-f0-9]{32,}$',  # Hashes
]

def is_valid_entity_name(name: str) -> bool:
    """Check if entity name is valid."""
    if not name or len(name) < 2:
        return False

    name_lower = name.lower().strip()

    # Check against invalid names
    if name_lower in {n.lower() for n in INVALID_ENTITIES}:
        return False

    # Check against invalid patterns
    for pattern in INVALID_PATTERNS:
        if re.match(pattern, name):
            return False

    # Must have at least one letter
    if not re.search(r'[a-zA-Z]', name):
        return False

    return True

def validate_relationship(subject: str, predicate: str, obj: str) -> Tuple[bool, str]:
    """Validate a relationship before storing.

    Returns (is_valid, reason)
    """
    if not is_valid_entity_name(subject):
        return False, f"Invalid subject: {subject}"

    if not is_valid_entity_name(obj):
        return False, f"Invalid object: {obj}"

    # Predicate-specific validation
    if predicate == "ACQUIRED":
        # Subject and object shouldn't be the same
        if subject.lower() == obj.lower():
            return False, "Self-acquisition"
        # Neither should be a person name for acquisitions
        # (This is heuristic - could be improved)

    if predicate in ("HIRED_BY", "DEPARTED_FROM"):
        # Subject should be a person (has space = likely person name)
        if " " not in subject and not subject[0].isupper():
            return False, f"Subject doesn't look like person name: {subject}"

    return True, "OK"

def filter_extraction_results(relationships: List) -> List:
    """Filter out invalid relationships."""
    valid = []
    for rel in relationships:
        is_valid, reason = validate_relationship(
            rel.subject, rel.predicate, rel.object
        )
        if is_valid:
            valid.append(rel)
        else:
            print(f"Rejected: {rel.subject} {rel.predicate} {rel.object} - {reason}")
    return valid
```

**Integration**: Update `src/pipeline/daily.py` `_extract()` method:
```python
from ..extraction.validator import filter_extraction_results

async def _extract(self, articles: List) -> int:
    count = 0
    for article in articles:
        try:
            result = await self.extractor.extract(article.title, article.content or article.summary)
            if result.relationships:
                # Filter before adding
                valid_rels = filter_extraction_results(result.relationships)
                if valid_rels:
                    result.relationships = valid_rels
                    self.kg.add_extraction_result(result, source_url=article.url)
                    count += len(valid_rels)
            self.storage.mark_extracted(article.id)
        except Exception as e:
            logger.warning("extraction_error", article_id=article.id, error=str(e))
    return count
```

**Test Cases**:
```python
# tests/test_validator.py
from src.extraction.validator import is_valid_entity_name, validate_relationship

def test_rejects_html_artifacts():
    assert not is_valid_entity_name("target")
    assert not is_valid_entity_name("_blank")
    assert not is_valid_entity_name("href")

def test_rejects_news_sources():
    assert not is_valid_entity_name("Reuters")
    assert not is_valid_entity_name("TechCrunch")

def test_accepts_valid_companies():
    assert is_valid_entity_name("OpenAI")
    assert is_valid_entity_name("CrowdStrike")
    assert is_valid_entity_name("Stripe")

def test_accepts_valid_people():
    assert is_valid_entity_name("Sam Altman")
    assert is_valid_entity_name("Jensen Huang")

def test_rejects_self_acquisition():
    valid, reason = validate_relationship("Google", "ACQUIRED", "Google")
    assert not valid

def test_accepts_valid_acquisition():
    valid, reason = validate_relationship("Microsoft", "ACQUIRED", "Activision")
    assert valid
```

#### Task 2.2: Clean Existing Bad Data
```sql
-- Run these to clean existing bad entities
DELETE FROM kg_entities WHERE name LIKE '%target="_blank%';
DELETE FROM kg_entities WHERE name LIKE '%href=%';
DELETE FROM kg_entities WHERE name LIKE 'CBM%' AND LENGTH(name) > 20;
DELETE FROM kg_entities WHERE name IN ('Reuters', 'TechCrunch', 'Bloomberg', 'Fortune', 'CNBC');
DELETE FROM kg_entities WHERE LENGTH(name) < 2;

-- Clean orphaned relationships
DELETE FROM kg_relationships WHERE subject_id NOT IN (SELECT id FROM kg_entities);
DELETE FROM kg_relationships WHERE object_id NOT IN (SELECT id FROM kg_entities);
```

**Verification**:
```sql
-- Should return 0
SELECT COUNT(*) FROM kg_entities WHERE name LIKE '%target%' OR name LIKE '%href%';
```

---

### Phase 3: Improve Data Sources
**Goal**: Better coverage of executive moves, fix blocked scrapers

#### Task 3.1: Add Executive-Focused RSS Feeds
Update `config/feeds.json` - add these feeds:
```json
{
  "name": "Google News - Executive Appointed",
  "url": "https://news.google.com/rss/search?q=appointed+CEO+OR+CFO+OR+CTO+tech&hl=en-US&gl=US&ceid=US:en",
  "priority": 0,
  "event_types": ["executive_move"]
},
{
  "name": "Google News - Executive Joins",
  "url": "https://news.google.com/rss/search?q=%22joins+as%22+OR+%22hired+as%22+tech+executive&hl=en-US&gl=US&ceid=US:en",
  "priority": 0,
  "event_types": ["executive_move"]
},
{
  "name": "Google News - Executive Leaves",
  "url": "https://news.google.com/rss/search?q=executive+%22steps+down%22+OR+%22leaving%22+tech&hl=en-US&gl=US&ceid=US:en",
  "priority": 0,
  "event_types": ["executive_move"]
},
{
  "name": "Google News - VP Hired",
  "url": "https://news.google.com/rss/search?q=%22VP%22+OR+%22Vice+President%22+hired+appointed+tech&hl=en-US&gl=US&ceid=US:en",
  "priority": 0,
  "event_types": ["executive_move"]
}
```

**Test Case**:
```bash
# After adding feeds, run pipeline and check
python scripts/run_daily.py
sqlite3 data/recruiter_intel.db "SELECT source, COUNT(*) FROM raw_articles WHERE source LIKE '%Executive%' GROUP BY source"
```

#### Task 3.2: Fix Layoffs.fyi Scraper (Use Playwright)
Replace `src/ingestion/layoffs_scraper.py` with Playwright-based scraper:
```python
"""Layoffs.fyi scraper using Playwright for JavaScript rendering."""
import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional
import structlog

logger = structlog.get_logger()

@dataclass
class LayoffEvent:
    company: str
    num_laid_off: Optional[int]
    date: datetime
    industry: str
    source_url: str
    percentage: Optional[float] = None

class LayoffsScraper:
    """Scrape layoffs.fyi using Playwright."""

    FALLBACK_DATA = [
        # Keep recent fallback data updated manually if scraping fails
        LayoffEvent("Meta", 100, datetime(2026, 1, 8), "Tech", "https://layoffs.fyi"),
        LayoffEvent("Amazon", 200, datetime(2026, 1, 7), "Tech", "https://layoffs.fyi"),
    ]

    async def fetch_layoffs(self, days_back: int = 7) -> List[LayoffEvent]:
        """Fetch recent layoffs."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright_not_installed", message="Using fallback data")
            return self._filter_by_date(self.FALLBACK_DATA, days_back)

        events = []
        cutoff = datetime.now() - timedelta(days=days_back)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                # Set realistic user agent
                await page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                })

                await page.goto("https://layoffs.fyi", wait_until="networkidle", timeout=30000)

                # Wait for table to load
                await page.wait_for_selector("table", timeout=10000)

                # Extract table rows
                rows = await page.query_selector_all("table tbody tr")

                for row in rows[:100]:  # Limit to recent
                    try:
                        cells = await row.query_selector_all("td")
                        if len(cells) >= 4:
                            company = await cells[0].inner_text()
                            date_str = await cells[1].inner_text()
                            num_str = await cells[2].inner_text()
                            industry = await cells[3].inner_text() if len(cells) > 3 else "Tech"

                            # Parse date
                            try:
                                event_date = datetime.strptime(date_str.strip(), "%Y-%m-%d")
                            except:
                                event_date = datetime.now()

                            if event_date < cutoff:
                                continue

                            # Parse number
                            num_laid_off = None
                            try:
                                num_laid_off = int(num_str.replace(",", "").strip())
                            except:
                                pass

                            events.append(LayoffEvent(
                                company=company.strip(),
                                num_laid_off=num_laid_off,
                                date=event_date,
                                industry=industry.strip(),
                                source_url="https://layoffs.fyi"
                            ))
                    except Exception as e:
                        continue

                await browser.close()

            logger.info("layoffs_fetched", count=len(events))
            return events

        except Exception as e:
            logger.error("layoffs_scrape_error", error=str(e))
            return self._filter_by_date(self.FALLBACK_DATA, days_back)

    def _filter_by_date(self, events: List[LayoffEvent], days_back: int) -> List[LayoffEvent]:
        cutoff = datetime.now() - timedelta(days=days_back)
        return [e for e in events if e.date >= cutoff]

    def to_extraction_result(self, event: LayoffEvent):
        """Convert to extraction result for knowledge graph."""
        from ..extraction.llm_extractor import ExtractionResult, Entity, Relationship

        company = Entity(name=event.company, entity_type="company")
        employees = Entity(name="employees", entity_type="group")

        context = f"{event.company} laid off"
        if event.num_laid_off:
            context += f" {event.num_laid_off} employees"
        context += f" on {event.date.strftime('%Y-%m-%d')}"

        rel = Relationship(
            subject=event.company,
            predicate="LAID_OFF",
            object="employees",
            confidence=0.95,
            context=context
        )

        return ExtractionResult(
            entities=[company, employees],
            relationships=[rel]
        )
```

**Test Case**:
```bash
# Test scraper directly
source venv/bin/activate && PYTHONPATH=. python3 -c "
import asyncio
from src.ingestion.layoffs_scraper import LayoffsScraper

async def test():
    scraper = LayoffsScraper()
    events = await scraper.fetch_layoffs(days_back=7)
    print(f'Found {len(events)} layoff events')
    for e in events[:5]:
        print(f'  {e.company}: {e.num_laid_off} on {e.date}')

asyncio.run(test())
"
```

#### Task 3.3: Fix YC Scraper (Use Playwright)
Similar approach - update `src/ingestion/yc_scraper.py` to use Playwright.

---

### Phase 4: Improve User Experience
**Goal**: Better newsletter, fresher dashboard

#### Task 4.1: Auto-Generate Daily Newsletter
Create `scripts/generate_newsletter.py`:
```python
"""Generate and optionally email daily newsletter."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.newsletter.generator import NewsletterGenerator

def main():
    gen = NewsletterGenerator()

    # Generate newsletter
    newsletter = gen.generate_daily()

    # Save HTML
    html = gen.to_html(newsletter)
    output_path = Path("data/newsletter.html")
    output_path.write_text(html)

    # Also save dated version
    dated_path = Path(f"data/newsletters/newsletter_{datetime.now().strftime('%Y%m%d')}.html")
    dated_path.parent.mkdir(exist_ok=True)
    dated_path.write_text(html)

    print(f"Newsletter generated: {output_path}")
    print(f"Archived: {dated_path}")

    # Print summary
    print(f"\nSummary:")
    print(f"  Acquisitions: {len(newsletter.acquisitions)}")
    print(f"  Funding: {len(newsletter.funding_rounds)}")
    print(f"  Hires: {len(newsletter.executive_moves)}")
    print(f"  Layoffs: {len(newsletter.layoffs)}")

if __name__ == "__main__":
    main()
```

**Test Case**:
```bash
python scripts/generate_newsletter.py
# Should output newsletter.html with today's data
open data/newsletter.html  # View in browser
```

#### Task 4.2: Add Dashboard Freshness Indicator
Update `scripts/kg_viewer.py` to show data freshness:
```python
# Add to the dashboard template
<div class="freshness-indicator">
    <span>Data as of: {{ last_update }}</span>
    <span>Pending extraction: {{ pending_count }} articles</span>
</div>
```

#### Task 4.3: Add "Today's Highlights" Section
Add to dashboard homepage - top 5 most important events from today.

---

## Test Suite

### Unit Tests
```bash
# Create tests/test_pipeline.py
pytest tests/ -v
```

### Integration Tests
```bash
# Test full pipeline
python scripts/run_daily.py --dry-run

# Verify extraction
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"
# Expected: 0 or < 10

# Verify relationships
sqlite3 data/knowledge_graph.db "SELECT predicate, COUNT(*) FROM kg_relationships GROUP BY predicate"
# Expected: Multiple predicates with counts
```

### Dashboard Tests
```bash
# Start dashboard
python scripts/kg_viewer.py &

# Test endpoints
curl http://localhost:8000/ | grep "Recent Acquisitions"
curl http://localhost:8000/companies | grep "company"
curl http://localhost:8000/candidates | grep "person"
curl http://localhost:8000/newsletter | grep "Intelligence"
```

### Data Quality Checks (Run Daily)
```sql
-- Check for bad entities
SELECT name FROM kg_entities
WHERE name LIKE '%<%'
   OR name LIKE '%>%'
   OR name LIKE '%http%'
   OR LENGTH(name) < 2;
-- Expected: 0 rows

-- Check relationship balance
SELECT predicate, COUNT(*) as cnt FROM kg_relationships
GROUP BY predicate
ORDER BY cnt DESC;
-- Expected: FUNDED_BY > ACQUIRED > HIRED_BY (typical distribution)

-- Check for orphans
SELECT COUNT(*) FROM kg_relationships
WHERE subject_id NOT IN (SELECT id FROM kg_entities)
   OR object_id NOT IN (SELECT id FROM kg_entities);
-- Expected: 0

-- Check extraction freshness
SELECT
    DATE(published_at) as day,
    SUM(CASE WHEN extracted=1 THEN 1 ELSE 0 END) as extracted,
    SUM(CASE WHEN extracted=0 THEN 1 ELSE 0 END) as pending
FROM raw_articles
WHERE is_high_signal=1
GROUP BY DATE(published_at)
ORDER BY day DESC
LIMIT 7;
-- Expected: Recent days should have low pending counts
```

---

## Best Practices

### 1. Always Check Extraction Backlog First
```bash
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"
```
If > 50, run extraction before anything else.

### 2. Validate Before Storing
Never add entities/relationships without validation. Use the validator module.

### 3. Preserve Source URLs
Every relationship should have a `source_url` for verification.

### 4. Log Everything
Use structlog for all operations. Key events to log:
- Articles fetched
- Articles classified
- Relationships extracted
- Errors encountered

### 5. Incremental Processing
Mark items as processed AFTER successful processing, not before. This ensures retries on failure.

### 6. Daily Health Check
Run this every morning:
```bash
echo "=== Recruiter Intelligence Health Check ==="
echo "Pending extractions:"
sqlite3 data/recruiter_intel.db "SELECT COUNT(*) FROM raw_articles WHERE is_high_signal=1 AND extracted=0"
echo "Relationships by type:"
sqlite3 data/knowledge_graph.db "SELECT predicate, COUNT(*) FROM kg_relationships GROUP BY predicate ORDER BY COUNT(*) DESC"
echo "Feed errors:"
sqlite3 data/recruiter_intel.db "SELECT feed_name, last_error FROM feed_stats WHERE last_error IS NOT NULL"
```

---

## Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Extraction backlog | < 20 articles | `SELECT COUNT(*) WHERE extracted=0` |
| Daily new relationships | > 30 | Compare kg_relationships count daily |
| Data freshness | < 12 hours | Check max(published_at) of extracted articles |
| Entity quality | 0 invalid names | Run validation query |
| Feed health | > 90% success | Check feed_stats.success_rate |

---

## Execution Checklist

### Phase 1: Fix the Bleeding
- [ ] Process all 401 pending extractions
- [ ] Verify extraction backlog is 0
- [ ] Set up 2x daily pipeline schedule
- [ ] Increase batch size to 200

### Phase 2: Improve Data Quality
- [ ] Create validator.py
- [ ] Integrate validator into pipeline
- [ ] Clean existing bad data
- [ ] Verify no invalid entities remain

### Phase 3: Improve Data Sources
- [ ] Add 4 executive-focused RSS feeds
- [ ] Fix Layoffs.fyi scraper with Playwright
- [ ] Fix YC scraper with Playwright
- [ ] Verify new feeds are producing articles

### Phase 4: Improve UX
- [ ] Create auto-newsletter script
- [ ] Add freshness indicator to dashboard
- [ ] Add "Today's Highlights" section
- [ ] Test full user flow

### Final Verification
- [ ] All metrics meet targets
- [ ] Dashboard shows fresh data
- [ ] Newsletter generates correctly
- [ ] No errors in logs

---

## Handoff Notes

When handing this to another agent or human:

1. **Start with Phase 1** - The 401 pending extractions is the most critical issue
2. **Run health check first** - See what state the system is in
3. **Test after each change** - Don't batch up multiple changes
4. **Keep the docs updated** - Update CLAUDE.md with any new learnings
5. **Monitor costs** - LLM extraction has API costs, track usage

The system works, it just needs to keep pace with data flow and improve data quality. The architecture is sound.
