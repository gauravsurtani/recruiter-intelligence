# Recruiter Intelligence System - Implementation Plan

## STATUS: ALL PHASES COMPLETE

## Goal
Build a system that identifies **high-value candidates** and **high-value companies** for recruiters by tracking:
- Acquisitions (companies integrating = hiring)
- Funding rounds (growth = hiring)
- Executive moves (departures = available talent)
- Layoffs (displaced talent + companies that need help)

---

## Implementation Status

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Extraction Quality | DONE |
| Phase 2 | Entity Enrichment | DONE |
| Phase 3 | More RSS Feeds | DONE |
| Phase 4 | UI Enhancements | DONE |
| Phase 5 | Recruiter Views | DONE |

---

## What Was Implemented

**Phase 1 - Extraction Quality:**
- Enhanced extraction prompt with strict entity naming rules
- Entity validation to filter garbage (sentence fragments, common words)
- Confidence thresholds (0.70 minimum)
- Name normalization (remove Inc., Corp., etc.)

**Phase 2 - Entity Enrichment:**
- New `src/enrichment/` module with interfaces and service
- `kg_enrichment` table for storing external data
- `kg_tags` table for recruiter workflow tags
- Enrichment service generates LinkedIn/Crunchbase URLs
- Entity detail page shows enrichment data and allows tagging

**Phase 3 - More RSS Feeds:**
- Expanded from 10 to 24 RSS feeds
- Added: GeekWire, SiliconANGLE, Wired, The Verge, Axios, PR Newswire, Business Wire, Bloomberg, Forbes, Inc, Fast Company, WSJ, Ars Technica, Protocol

**Phase 4 - UI Enhancements:**
- Timeline view with date grouping and event filtering
- Full search with entity type/event type/date filters
- Entity tagging system with add/view tags
- Improved styling with better navigation

**Phase 5 - Recruiter-Focused Views:**
- High-Value Companies view (scored by signals)
- High-Value Candidates view (scored by availability)
- Entity detail pages with relationship graphs
- External research links (LinkedIn, Google, Crunchbase)

---

## How to Use

1. **Start the pipeline:**
   ```bash
   source venv/bin/activate
   python scripts/run_daily.py
   ```

2. **View the dashboard:**
   ```bash
   python scripts/kg_viewer.py
   # Open http://localhost:8000
   ```

3. **Key pages:**
   - `/` - Dashboard with overview stats
   - `/timeline` - Chronological event view
   - `/search` - Search with filters
   - `/companies` - High-value companies ranked by signals
   - `/candidates` - High-value candidates ranked by availability
   - `/entity/{id}` - Entity detail with tags and enrichment

---

## Current State (After Implementation)

**Working:**
- RSS ingestion from 24 feeds
- Event classification (acquisition, funding, executive_move, layoff, IPO)
- LLM extraction using Gemini 2.0 Flash with improved prompts
- Entity validation and normalization
- Knowledge graph with enrichment and tagging
- Full-featured web UI with timeline, search, and recruiter views

**Tests:** 54 passing

---

## Phase 1: Improve Extraction Quality

### 1.1 Enhanced Extraction Prompt

Update `src/extraction/llm_extractor.py` with stricter rules:

```python
EXTRACTION_PROMPT = """Extract entities and relationships from this article.

ENTITY EXTRACTION RULES:
- Company names: Use official name only (e.g., "Google" not "Google Inc." or "the tech giant")
- Person names: Full name with title if mentioned (e.g., "John Smith, CEO")
- Investor names: Fund name, not parent company (e.g., "Sequoia Capital" not "Sequoia")
- DO NOT include sentence fragments or descriptions
- DO NOT include generic terms like "the company" or "the startup"

ENTITIES:
- Companies (type: "company")
- People with business roles (type: "person")
- Investment firms, VCs, PE firms (type: "investor")

RELATIONSHIPS:
- ACQUIRED: Company acquired another company
- FUNDED_BY: Company received funding from investor
- HIRED_BY: Person joined a company (include role in context)
- DEPARTED_FROM: Person left a company
- FOUNDED: Person founded a company
- CEO_OF/CTO_OF/CFO_OF: Executive role assignment

VALIDATION:
- Only include entities explicitly named in the article
- Confidence > 0.7 for relationships
- Include event_date if mentioned (even partially like "last week" = calculate date)

Return JSON:
{
  "entities": [
    {"name": "Exact Official Name", "type": "company|person|investor", "role": "optional title"}
  ],
  "relationships": [
    {
      "subject": "Entity name (must match entities list)",
      "predicate": "RELATIONSHIP_TYPE",
      "object": "Entity name (must match entities list)",
      "context": "Exact quote from article",
      "confidence": 0.0-1.0,
      "role": "optional: CEO, CTO, VP Engineering"
    }
  ],
  "event_date": "YYYY-MM-DD or null",
  "amounts": {
    "funding": "$XM or null",
    "acquisition": "$XM or null",
    "valuation": "$XB or null"
  }
}

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content}"""
```

### 1.2 Entity Validation

Add post-extraction validation in `llm_extractor.py`:

```python
def _validate_entity(self, entity: Entity) -> bool:
    """Validate extracted entity quality."""
    name = entity.name.strip()

    # Reject if too short
    if len(name) < 2:
        return False

    # Reject if contains sentence fragments
    bad_patterns = [
        'says', 'said', 'announced', 'reported', 'according',
        'the company', 'the startup', 'the firm',
        'in a', 'for a', 'with a'
    ]
    if any(p in name.lower() for p in bad_patterns):
        return False

    # Reject if too long (likely a phrase, not entity name)
    if len(name) > 50:
        return False

    return True
```

### 1.3 Entity Deduplication

Update `src/knowledge_graph/resolver.py` with better normalization:

```python
class EntityResolver:
    """Resolve entity variations to canonical names."""

    COMPANY_SUFFIXES = [
        'Inc', 'Inc.', 'Corp', 'Corp.', 'LLC', 'Ltd', 'Ltd.',
        'Corporation', 'Company', 'Co', 'Co.'
    ]

    def normalize_company(self, name: str) -> str:
        """Normalize company name."""
        # Remove suffixes
        for suffix in self.COMPANY_SUFFIXES:
            if name.endswith(f' {suffix}'):
                name = name[:-len(suffix)-1]

        # Handle known aliases
        aliases = {
            'alphabet': 'google',
            'meta platforms': 'meta',
            'facebook': 'meta',
        }

        normalized = name.lower().strip()
        return aliases.get(normalized, normalized)
```

---

## Phase 2: Entity Enrichment

### 2.1 Schema Changes

Add to `src/knowledge_graph/graph.py`:

```sql
-- Entity enrichment data
CREATE TABLE IF NOT EXISTS kg_enrichment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER REFERENCES kg_entities(id),
    source TEXT NOT NULL,  -- 'crunchbase', 'linkedin', 'clearbit'
    data_json TEXT,
    enriched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, source)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_kg_enrichment_entity ON kg_enrichment(entity_id);
```

### 2.2 Enrichment Service Architecture

Create `src/enrichment/` module:

```
src/enrichment/
├── __init__.py
├── interfaces.py          # EnrichmentResult, EnricherInterface
├── crunchbase.py          # CrunchbaseEnricher
├── linkedin.py            # LinkedInEnricher
├── clearbit.py            # ClearbitEnricher (company data)
└── enrichment_service.py  # Orchestrates all enrichers
```

### 2.3 Company Enrichment Data Model

```python
@dataclass
class CompanyEnrichment:
    """Enriched company data."""
    domain: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    employee_range: Optional[str] = None  # "51-200"
    headquarters: Optional[str] = None
    founded_year: Optional[int] = None

    # Funding data
    total_funding: Optional[str] = None    # "$150M"
    last_funding_date: Optional[str] = None
    last_funding_amount: Optional[str] = None
    last_funding_type: Optional[str] = None  # "Series B"
    investors: List[str] = field(default_factory=list)

    # Signals for recruiters
    is_hiring: bool = False
    recent_headcount_change: Optional[str] = None  # "+20%", "-10%"
    linkedin_url: Optional[str] = None
```

### 2.4 Person Enrichment Data Model

```python
@dataclass
class PersonEnrichment:
    """Enriched person data."""
    linkedin_url: Optional[str] = None
    current_company: Optional[str] = None
    current_title: Optional[str] = None
    location: Optional[str] = None

    # Career signals
    tenure_months: Optional[int] = None
    is_executive: bool = False
    previous_companies: List[str] = field(default_factory=list)
```

### 2.5 Enrichment Sources

| Source | Data Type | API/Method | Cost |
|--------|-----------|------------|------|
| Crunchbase Basic | Company funding | API (requires subscription) | $$ |
| Clearbit | Company domain → data | API | $ |
| LinkedIn Company | Employee count | Unofficial scrape | Free |
| LinkedIn Person | Profile URL | Google CSE search | $ |
| Apollo.io | Contact data | API | $$ |

**Recommendation**: Start with free/cheap options:
1. **Clearbit Reveal** (free tier) for company domain lookup
2. **Google Custom Search** for LinkedIn profile URLs
3. **Web scrape** company career pages for hiring signals

### 2.6 Implementation Priority

```
1. Company domain enrichment (Clearbit free tier)
2. LinkedIn company page URL generation
3. Funding data from article extraction (already have this!)
4. Person LinkedIn URL via Google search
```

---

## Phase 3: Add More RSS Feeds

### 3.1 High-Value Feeds to Add

**Executive Movement Feeds:**
```json
{
  "name": "Hunt Scanlon Executive Search",
  "url": "https://huntscanlon.com/feed/",
  "priority": 0,
  "event_types": ["executive_move"]
},
{
  "name": "Executive Grapevine",
  "url": "https://www.executivegrapevine.com/rss/",
  "priority": 0,
  "event_types": ["executive_move", "layoff"]
}
```

**VC/PE News:**
```json
{
  "name": "Axios Pro Rata",
  "url": "https://www.axios.com/newsletters/axios-pro-rata",
  "priority": 0,
  "event_types": ["funding", "acquisition"]
},
{
  "name": "Term Sheet (Fortune)",
  "url": "https://fortune.com/tag/term-sheet/feed/",
  "priority": 0,
  "event_types": ["funding", "acquisition"]
}
```

**Tech-Specific:**
```json
{
  "name": "SiliconANGLE",
  "url": "https://siliconangle.com/feed/",
  "priority": 1,
  "event_types": ["funding", "acquisition", "ai"]
},
{
  "name": "GeekWire",
  "url": "https://www.geekwire.com/feed/",
  "priority": 1,
  "event_types": ["funding", "startup", "executive_move"]
},
{
  "name": "Wired Business",
  "url": "https://www.wired.com/feed/category/business/latest/rss",
  "priority": 1,
  "event_types": ["acquisition", "funding"]
}
```

**Layoff/Restructuring:**
```json
{
  "name": "Layoffs.fyi RSS",
  "url": "https://layoffs.fyi/feed/",
  "priority": 0,
  "event_types": ["layoff"]
}
```

### 3.2 Feed Quality Monitoring

Add to `src/ingestion/fetcher.py`:

```python
async def test_feed(self, feed: FeedConfig) -> dict:
    """Test feed availability and quality."""
    return {
        "status": "ok" | "error" | "empty",
        "article_count": int,
        "latest_article_date": date,
        "avg_content_length": int,
        "error_message": str | None
    }
```

---

## Phase 4: UI Enhancements

### 4.1 Timeline View

Add `/timeline` endpoint to `scripts/kg_viewer.py`:

```python
@app.get("/timeline", response_class=HTMLResponse)
async def timeline(
    days: int = 30,
    event_type: str = None,
    company: str = None
):
    """Chronological event timeline."""
    kg = get_kg()

    # Query relationships ordered by date
    since = date.today() - timedelta(days=days)
    events = kg.query(since_date=since)

    # Group by date
    by_date = defaultdict(list)
    for event in events:
        event_date = event.event_date or date.today()
        by_date[event_date].append(event)
```

**Timeline UI:**
```html
<div class="timeline">
  <div class="timeline-date">January 6, 2026</div>
  <div class="timeline-events">
    <div class="event acquisition">
      <span class="event-type">ACQUISITION</span>
      <span class="subject">Microsoft</span> acquired <span class="object">Startup X</span>
      <a href="..." class="source">TechCrunch →</a>
    </div>
    <div class="event hire">
      <span class="event-type">HIRED</span>
      <span class="subject">Jane Doe</span> joined <span class="object">Stripe</span> as CTO
    </div>
  </div>
</div>
```

### 4.2 Search & Filters

Add search endpoint and UI:

```python
@app.get("/search", response_class=HTMLResponse)
async def search(
    q: str = "",
    entity_type: str = None,    # company, person, investor
    event_type: str = None,     # ACQUIRED, FUNDED_BY, HIRED_BY
    min_funding: str = None,    # 10M, 50M, 100M
    days: int = 90
):
    """Search with filters."""
```

**Filter UI:**
```html
<div class="filters">
  <input type="text" placeholder="Search companies, people...">
  <select name="entity_type">
    <option value="">All Types</option>
    <option value="company">Companies</option>
    <option value="person">People</option>
    <option value="investor">Investors</option>
  </select>
  <select name="event_type">
    <option value="">All Events</option>
    <option value="ACQUIRED">Acquisitions</option>
    <option value="FUNDED_BY">Funding</option>
    <option value="HIRED_BY">Hires</option>
    <option value="DEPARTED_FROM">Departures</option>
  </select>
  <select name="timeframe">
    <option value="7">Last 7 days</option>
    <option value="30">Last 30 days</option>
    <option value="90">Last 90 days</option>
  </select>
</div>
```

### 4.3 Tagging System

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS kg_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER REFERENCES kg_entities(id),
    tag TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, tag)
);
```

**Pre-defined Tags:**
- `hot_target` - Priority company for outreach
- `active_hiring` - Confirmed hiring activity
- `recent_funding` - Funded in last 90 days
- `acquisition_target` - May be acquired soon
- `talent_pool` - Source of candidates (layoffs, acquisitions)
- `watch_list` - Monitor for changes

**Tag UI:**
```html
<div class="entity-tags">
  <span class="tag active">active_hiring</span>
  <span class="tag">recent_funding</span>
  <button class="add-tag">+ Add Tag</button>
</div>
```

---

## Phase 5: Recruiter-Focused Views

### 5.1 High-Value Companies View

Create `/companies/high-value` endpoint:

**Scoring Algorithm:**
```python
def score_company(company: GraphEntity, relationships: List[GraphRelationship]) -> float:
    """Score company for recruiting value."""
    score = 0.0

    # Recent funding = growth = hiring
    funding_rels = [r for r in relationships
                    if r.predicate == "FUNDED_BY"
                    and r.subject.name == company.name]
    if funding_rels:
        score += 30
        # Bonus for larger rounds
        for rel in funding_rels:
            if rel.amounts and rel.amounts.get("funding"):
                amount = parse_amount(rel.amounts["funding"])
                if amount > 100_000_000:
                    score += 20
                elif amount > 50_000_000:
                    score += 10

    # Made acquisitions = integrating = hiring
    acquisitions = [r for r in relationships
                    if r.predicate == "ACQUIRED"
                    and r.subject.name == company.name]
    score += len(acquisitions) * 20

    # Exec departures = backfill needed
    departures = [r for r in relationships
                  if r.predicate == "DEPARTED_FROM"
                  and r.object.name == company.name]
    score += len(departures) * 15

    # Layoffs = needs help rebuilding
    layoffs = [r for r in relationships
               if r.predicate == "LAYOFF"
               and r.subject.name == company.name]
    score += len(layoffs) * 10

    return score
```

**View:**
```
+--------------------------------------------------+
| HIGH-VALUE COMPANIES (Last 30 Days)              |
+--------------------------------------------------+
| Score | Company      | Signals                   |
|-------|--------------|---------------------------|
|  95   | Stripe       | $150M funding, 3 hires    |
|  85   | Databricks   | Acquired 2 companies      |
|  70   | OpenAI       | Exec departure (CTO)      |
|  60   | Meta         | Layoffs, restructuring    |
+--------------------------------------------------+
```

### 5.2 High-Value Candidates View

Create `/candidates/high-value` endpoint:

**Candidate Signals:**
```python
def get_candidate_signals(person: GraphEntity, relationships: List[GraphRelationship]) -> dict:
    """Identify recruiting signals for a person."""
    signals = []

    # Departed = available
    departures = [r for r in relationships
                  if r.predicate == "DEPARTED_FROM"
                  and r.subject.name == person.name]
    if departures:
        signals.append(f"Left {departures[0].object.name}")

    # At acquired company = uncertain future
    company = get_current_company(person)
    if company and was_acquired_recently(company):
        signals.append(f"At acquired company ({company})")

    # At company with layoffs = receptive
    if company and had_layoffs_recently(company):
        signals.append(f"At company with layoffs ({company})")

    # Executive = high value
    if is_executive(person):
        signals.append("Executive level")

    return {
        "person": person,
        "signals": signals,
        "score": len(signals) * 25
    }
```

### 5.3 Dashboard Widgets

Update `/` dashboard with widgets:

```html
<!-- Hot Companies This Week -->
<div class="widget">
  <h3>Hot Companies This Week</h3>
  <ul>
    <li><span class="score">95</span> Stripe - $150M Series D</li>
    <li><span class="score">85</span> Databricks - Acquired MosaicML</li>
    <li><span class="score">70</span> Anthropic - Hiring surge</li>
  </ul>
</div>

<!-- Executive Movement Radar -->
<div class="widget">
  <h3>Executive Movement Radar</h3>
  <ul>
    <li>John Smith left Google (CTO)</li>
    <li>Jane Doe joined Stripe (VP Engineering)</li>
  </ul>
</div>

<!-- Funding Roundup -->
<div class="widget">
  <h3>Funding Roundup (Last 7 Days)</h3>
  <ul>
    <li>Stripe - $150M Series D</li>
    <li>Notion - $75M Series C</li>
  </ul>
</div>
```

---

## Implementation Checklist

### Week 1: Foundation
- [ ] Update extraction prompt (Phase 1.1)
- [ ] Add entity validation (Phase 1.2)
- [ ] Add 5 new RSS feeds (Phase 3)
- [ ] Test pipeline with new feeds

### Week 2: Timeline & Search
- [ ] Implement `/timeline` endpoint (Phase 4.1)
- [ ] Add search/filter UI (Phase 4.2)
- [ ] Style improvements for UI

### Week 3: Company Enrichment
- [ ] Add enrichment schema (Phase 2.1)
- [ ] Implement Clearbit integration (Phase 2.3)
- [ ] Add LinkedIn URL generation
- [ ] Display enrichment data in UI

### Week 4: Recruiter Views
- [ ] Implement tagging system (Phase 4.3)
- [ ] Create high-value companies view (Phase 5.1)
- [ ] Create high-value candidates view (Phase 5.2)
- [ ] Dashboard widgets (Phase 5.3)

### Week 5: Person Enrichment
- [ ] LinkedIn profile search (Phase 2.4)
- [ ] Career trajectory view
- [ ] Export to CSV/Excel

---

## API Keys & Services Needed

| Service | Purpose | Cost | Required? |
|---------|---------|------|-----------|
| Gemini API | Entity extraction | Free tier | Yes (have) |
| Clearbit Reveal | Company enrichment | Free tier | No |
| Google Custom Search | LinkedIn URL lookup | $5/1000 queries | No |
| Apollo.io | Contact enrichment | $$ | No |
| Crunchbase API | Funding data | $$ | No |

---

## Questions to Decide

1. **What entity enrichment sources do you have access to?**
   - Crunchbase API?
   - LinkedIn Sales Navigator?
   - Apollo.io?

2. **What tags do you want pre-defined?**
   - `hot_target`, `active_hiring`, `recent_funding`?
   - Free-form tags allowed?

3. **Export formats needed?**
   - CSV for spreadsheets?
   - JSON for integrations?

4. **Notification/alerts wanted?**
   - Email when company gets funding?
   - Slack integration?

---

## Next Steps

Pick a starting point:
1. **Phase 1** - Extraction quality (quick win, no new dependencies)
2. **Phase 3** - More feeds (quick win, more data)
3. **Phase 4** - UI improvements (timeline, search, tags)
4. **Phase 2** - Entity enrichment (needs API access decisions)
