# UI Improvement Tasks - Recruiter Intelligence System

## TASK: Implement 16 UI Improvements

You are implementing UI improvements for a recruiter intelligence web application. The app is a FastAPI web UI (`scripts/kg_viewer.py`) that displays a knowledge graph of companies, people, funding events, acquisitions, and executive moves.

**CRITICAL REQUIREMENTS:**
1. After each change, test by running `curl` against the endpoint
2. Track success/failure in a checklist
3. If something fails, log it and move on - we need 80%+ working
4. The server is running at http://localhost:8000

**START THE SERVER FIRST:**
```bash
cd /Users/gauravsurtani/projects/fir_recruiting/new_approach/recruiter-intelligence
python scripts/kg_viewer.py &
```

**PRIMARY FILE:** `scripts/kg_viewer.py` (2125 lines)

---

## IMPLEMENTATION TASKS

### TASK 1: Entity Detail - Add Source/Amount/Context to Relationship Tables
**Location:** Lines 1980-2014
**Current:** Tables show Event, Related Entity, Date, Confidence only
**Change:** Add columns for Source (clickable link), Amount, Context

**Code to modify (As Subject table, ~line 1985):**
```python
# BEFORE:
<tr><th>Event</th><th>Related Entity</th><th>Date</th><th>Confidence</th></tr>

# AFTER:
<tr><th>Event</th><th>Related Entity</th><th>Amount</th><th>Date</th><th>Confidence</th><th>Context</th><th>Source</th></tr>
```

**Also update the row template (~line 1988-1995):**
```python
# Add these to each row:
metadata = getattr(rel, 'metadata', {}) or {}
amount = metadata.get('amount') or metadata.get('valuation') or '-'
context = (rel.context[:80] + '...') if rel.context and len(rel.context) > 80 else (rel.context or '-')
source_link = f'<a href="{rel.source_url}" target="_blank">View</a>' if rel.source_url else '-'
```

**TEST:**
```bash
curl -s http://localhost:8000/entity/1 | grep -c "Source</th>"
# Expected: 2 (one for each table)
```

---

### TASK 2: Feeds Page - Make RSS URLs Clickable
**Location:** Line 776
**Current:** `<span style="...">{feed['url'][:50]}...</span>`
**Change:** Wrap in anchor tag

**Code:**
```python
# BEFORE:
<br><span style="font-size: 0.8em; color: var(--gray-500);">{feed['url'][:50]}...</span>

# AFTER:
<br><a href="{feed['url']}" target="_blank" style="font-size: 0.8em; color: var(--gray-500);">{feed['url'][:60]}...</a>
```

**TEST:**
```bash
curl -s http://localhost:8000/feeds | grep -o '<a href="http[^"]*" target="_blank"' | head -3
# Expected: Multiple anchor tags with feed URLs
```

---

### TASK 3: Pipeline Run Button - Verify/Fix
**Location:** Lines ~1000-1100 (POST /pipeline/run)

**TEST:**
```bash
curl -X POST http://localhost:8000/pipeline/run \
  -d "days_back=1&use_form_d=true&use_spacy=true" \
  -H "Content-Type: application/x-www-form-urlencoded"
# Expected: HTML response with pipeline results OR error message
```

---

### TASK 4: Entity Detail - Add News Sources Card
**Location:** After line 1978 (before relationship tables)

**Code to add:**
```python
# Collect source URLs from relationships
source_articles = []
seen_urls = set()
for rel in subject_rels + object_rels:
    if rel.source_url and rel.source_url not in seen_urls:
        seen_urls.add(rel.source_url)
        from urllib.parse import urlparse
        domain = urlparse(rel.source_url).netloc.replace('www.', '')
        source_articles.append({
            'url': rel.source_url,
            'domain': domain,
            'date': rel.event_date,
            'event': rel.predicate
        })

if source_articles:
    content += """
    <div class="card">
        <div class="card-header">News Sources</div>
        <table>
            <tr><th>Source</th><th>Event</th><th>Date</th><th>Link</th></tr>
    """
    for article in source_articles[:10]:
        content += f"""
            <tr>
                <td>{article['domain']}</td>
                <td><span class="tag tag-{article['event']}">{article['event']}</span></td>
                <td>{article['date'] or '-'}</td>
                <td><a href="{article['url']}" target="_blank">View Article</a></td>
            </tr>
        """
    content += "</table></div>"
```

**TEST:**
```bash
curl -s http://localhost:8000/entity/1 | grep -c "News Sources"
# Expected: 1 (if entity has relationships with source URLs)
```

---

### TASK 5: Enrichment - Show Source Badge
**Location:** Lines 1831-1964 (enrichment display section)

**Code to add after line 1837:**
```python
enriched_at = source_data.get("enriched_at", "Unknown")
```

**Change card header (~line 1877 for company):**
```python
content += f'<div class="card"><div class="card-header">Company Details <span style="font-size: 0.75em; color: var(--gray-500); margin-left: 8px;">Source: {enrichment_source} | {enriched_at}</span></div><table>'
```

**TEST:**
```bash
curl -s http://localhost:8000/entity/1 | grep -c "Source:.*web_search"
# Expected: 1 (if entity is enriched)
```

---

### TASK 6: Timeline - Show Source Domain
**Location:** ~Lines 580-620 (timeline route)

**Find the source_link line and update:**
```python
# BEFORE:
source_link = f'<a href="{rel.source_url}" target="_blank">View</a>' if rel.source_url else '-'

# AFTER:
if rel.source_url:
    from urllib.parse import urlparse
    domain = urlparse(rel.source_url).netloc.replace('www.', '').split('.')[0].title()
    source_link = f'<a href="{rel.source_url}" target="_blank">{domain}</a>'
else:
    source_link = '-'
```

**TEST:**
```bash
curl -s http://localhost:8000/timeline | grep -oE '>[A-Z][a-z]+</a>' | head -5
# Expected: Domain names like >Techcrunch</a>, >Bloomberg</a>
```

---

### TASK 7: Companies Page - Add Quick Actions & Enrichment Indicator
**Location:** Lines 1583-1673

**Update table header (~line 1654):**
```python
# BEFORE:
<tr><th>Score</th><th>Company</th><th>Signals</th><th>Mentions</th></tr>

# AFTER:
<tr><th>Score</th><th>Company</th><th>Signals</th><th>Last Event</th><th>Actions</th></tr>
```

**Update row template (~line 1659-1666):**
```python
enriched = kg.get_enrichment(item['company'].id)
enr_dot = '<span style="color: var(--success);">●</span>' if enriched else '<span style="color: var(--gray-300);">○</span>'

content += f"""
    <tr>
        <td><strong style="color: var(--primary);">{item['score']}</strong></td>
        <td>{enr_dot} <a href="/entity/{item['company'].id}">{item['company'].name}</a></td>
        <td>{signals_html}</td>
        <td>-</td>
        <td>
            <a href="/search?query={item['company'].name}" class="btn btn-sm btn-secondary">Events</a>
            <a href="https://linkedin.com/company/{item['company'].name.lower().replace(' ', '-')}" target="_blank" class="btn btn-sm btn-secondary">LinkedIn</a>
        </td>
    </tr>
"""
```

**TEST:**
```bash
curl -s http://localhost:8000/companies | grep -c "Events</a>"
# Expected: >= 1
```

---

### TASK 8: Candidates Page - Add LinkedIn Links & Title
**Location:** Lines 1676-1764

**Update table header (~line 1744):**
```python
<tr><th>Score</th><th>Person</th><th>Title</th><th>Company</th><th>Signals</th><th>Actions</th></tr>
```

**Update row (~line 1747-1757):**
```python
enrichment = kg.get_enrichment(item['person'].id)
title = '-'
if enrichment:
    for src, data in enrichment.items():
        title = data.get('data', {}).get('current_title', '-')
        if title != '-':
            break

linkedin_url = f"https://www.linkedin.com/search/results/all/?keywords={item['person'].name.replace(' ', '%20')}"

content += f"""
    <tr>
        <td><strong style="color: var(--primary);">{item['score']}</strong></td>
        <td><a href="/entity/{item['person'].id}">{item['person'].name}</a></td>
        <td>{title}</td>
        <td>{company_html}</td>
        <td>{signals_html}</td>
        <td><a href="{linkedin_url}" target="_blank" class="btn btn-sm btn-secondary">LinkedIn</a></td>
    </tr>
"""
```

**TEST:**
```bash
curl -s http://localhost:8000/candidates | grep -c "linkedin.com"
# Expected: >= 1
```

---

### TASK 9: Entity Detail - Add Mini Event Timeline
**Location:** After enrichment section, before relationship tables (~line 1978)

**Code to add:**
```python
all_events = []
for rel in subject_rels:
    all_events.append({'type': rel.predicate, 'other': rel.object.name, 'date': rel.event_date, 'url': rel.source_url})
for rel in object_rels:
    all_events.append({'type': rel.predicate, 'other': rel.subject.name, 'date': rel.event_date, 'url': rel.source_url})

all_events.sort(key=lambda x: x['date'] or '', reverse=True)

if all_events:
    content += """
    <div class="card">
        <div class="card-header">Event Timeline</div>
        <div style="padding: 16px;">
    """
    for evt in all_events[:15]:
        color_map = {'ACQUIRED': 'var(--warning)', 'FUNDED_BY': 'var(--success)', 'HIRED_BY': 'var(--primary)', 'DEPARTED_FROM': 'var(--danger)'}
        color = color_map.get(evt['type'], 'var(--gray-500)')
        link = f'<a href="{evt["url"]}" target="_blank" style="font-size: 0.8em; margin-left: 8px;">source</a>' if evt['url'] else ''
        content += f"""
            <div style="display: flex; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                <span style="width: 12px; height: 12px; border-radius: 50%; background: {color}; margin-right: 12px;"></span>
                <span class="tag tag-{evt['type']}">{evt['type']}</span>
                <span style="margin-left: 8px;">{evt['other']}</span>
                <span style="margin-left: auto; color: var(--gray-500);">{evt['date'] or 'Unknown date'}</span>
                {link}
            </div>
        """
    content += "</div></div>"
```

**TEST:**
```bash
curl -s http://localhost:8000/entity/1 | grep -c "Event Timeline"
# Expected: 1
```

---

### TASK 10: Search Page - Add Source Column
**Location:** Search results relationships table (~lines 1100-1200)

**Add to header:**
```python
<th>Source</th>
```

**Add to each row:**
```python
source_link = f'<a href="{rel.source_url}" target="_blank">View</a>' if rel.source_url else '-'
```

**TEST:**
```bash
curl -s "http://localhost:8000/search?event_type=ACQUIRED" | grep -c "Source</th>"
# Expected: 1
```

---

### TASK 11: Dashboard - Add Source Links to Recent Events
**Location:** Dashboard route (~lines 400-550)

**For each event:**
```python
source_link = f' <a href="{rel.source_url}" target="_blank" style="font-size: 0.8em;">[source]</a>' if rel.source_url else ''
```

**TEST:**
```bash
curl -s http://localhost:8000/ | grep -c "\[source\]"
# Expected: >= 1
```

---

### TASK 12: Relationships Page - Make Entity Names Clickable
**Location:** Lines 1559-1573

**Update row template:**
```python
# BEFORE:
<td><span class="tag tag-{rel.subject.entity_type}">{rel.subject.name}</span></td>

# AFTER:
<td><a href="/entity/{rel.subject.id}"><span class="tag tag-{rel.subject.entity_type}">{rel.subject.name}</span></a></td>
```

**TEST:**
```bash
curl -s http://localhost:8000/relationships | grep -c 'href="/entity/'
# Expected: >= 2 per row
```

---

### TASK 13: Add Breadcrumbs to Entity Detail Page
**Location:** Start of entity_detail content (~line 1786)

**Code to add before the h1:**
```python
if entity.entity_type == 'company':
    breadcrumb = '<a href="/">Dashboard</a> &gt; <a href="/companies">Companies</a> &gt; '
elif entity.entity_type == 'person':
    breadcrumb = '<a href="/">Dashboard</a> &gt; <a href="/candidates">Candidates</a> &gt; '
else:
    breadcrumb = '<a href="/">Dashboard</a> &gt; <a href="/entities">Entities</a> &gt; '

content = f"""
<div style="margin-bottom: 16px; font-size: 0.9em; color: var(--gray-500);">
    {breadcrumb} {entity.name}
</div>
"""
```

**TEST:**
```bash
curl -s http://localhost:8000/entity/1 | grep -c "Dashboard</a>"
# Expected: 1
```

---

### TASK 14: Add Pagination to Entities Page
**Location:** Entities route (~lines 1460-1527)

**Update route signature:**
```python
@app.get("/entities", response_class=HTMLResponse)
async def entities(entity_type: str = Query(None), page: int = Query(1)):
```

**Add pagination logic:**
```python
PAGE_SIZE = 50
all_entities = kg.search_entities('', entity_type=entity_type)
total = len(all_entities)
total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
start = (page - 1) * PAGE_SIZE
end = start + PAGE_SIZE
paginated_entities = all_entities[start:end]
```

**Add controls after table:**
```python
if total_pages > 1:
    content += '<div style="margin-top: 16px; text-align: center;">'
    for p in range(1, total_pages + 1):
        active = 'background: var(--primary); color: white;' if p == page else ''
        type_param = f'&entity_type={entity_type}' if entity_type else ''
        content += f'<a href="/entities?page={p}{type_param}" class="btn btn-sm btn-secondary" style="margin: 0 4px; {active}">{p}</a>'
    content += '</div>'
```

**TEST:**
```bash
curl -s "http://localhost:8000/entities?page=1" | grep -c 'page='
# Expected: >= 1 (if multiple pages exist)
```

---

### TASK 15: Better Empty States with CTAs
**Location:** Multiple empty state messages (search for `class="empty-state"`)

**Update each:**
```python
# BEFORE:
content += '<tr><td colspan="5" class="empty-state">No entities yet</td></tr>'

# AFTER:
content += '<tr><td colspan="5" class="empty-state">No entities yet. <a href="/pipeline">Run the pipeline</a> to extract entities from news.</td></tr>'
```

**TEST:**
```bash
grep -c "Run the pipeline" scripts/kg_viewer.py
# Expected: >= 1
```

---

### TASK 16: Pipeline Page - Show Last Run Info
**Location:** Pipeline route

**Add helper functions at top of file:**
```python
import json
from pathlib import Path

PIPELINE_STATE_FILE = Path(__file__).parent / '.pipeline_state.json'

def save_pipeline_state(stats):
    state = {'last_run': datetime.now().isoformat(), 'stats': stats}
    PIPELINE_STATE_FILE.write_text(json.dumps(state, default=str))

def get_pipeline_state():
    if PIPELINE_STATE_FILE.exists():
        return json.loads(PIPELINE_STATE_FILE.read_text())
    return None
```

**Add to pipeline_status route:**
```python
state = get_pipeline_state()
if state:
    content += f"""
    <div class="card" style="margin-bottom: 24px;">
        <div class="card-header">Last Pipeline Run</div>
        <div style="padding: 16px;">
            <strong>Run at:</strong> {state['last_run']}<br>
            <strong>Articles fetched:</strong> {state['stats'].get('fetched_articles', 'N/A')}<br>
            <strong>High signal:</strong> {state['stats'].get('high_signal_articles', 'N/A')}<br>
            <strong>Relationships:</strong> {state['stats'].get('extracted_relationships', 'N/A')}
        </div>
    </div>
    """
```

**Call at end of run_pipeline_action:**
```python
save_pipeline_state(stats)
```

**TEST:**
```bash
curl -s http://localhost:8000/pipeline | grep -c "Last Pipeline Run"
# Expected: 1 (after pipeline has run)
```

---

## TESTING CHECKLIST

Run this script after all changes:

```bash
#!/bin/bash
echo "=== UI IMPROVEMENT TESTS ==="

echo "1. Entity Source Links:"
curl -s http://localhost:8000/entity/1 | grep -q "Source</th>" && echo "PASS" || echo "FAIL"

echo "2. Feeds Clickable URLs:"
curl -s http://localhost:8000/feeds | grep -q 'href="http.*target="_blank"' && echo "PASS" || echo "FAIL"

echo "3. Pipeline Run Button:"
curl -s -X POST http://localhost:8000/pipeline/run -d "days_back=1" | grep -q "fetched\|Pipeline" && echo "PASS" || echo "FAIL"

echo "4. News Sources Card:"
curl -s http://localhost:8000/entity/1 | grep -q "News Sources" && echo "PASS" || echo "FAIL - needs data"

echo "5. Enrichment Source Badge:"
curl -s http://localhost:8000/entity/1 | grep -q "Source:" && echo "PASS" || echo "FAIL - needs enriched entity"

echo "6. Timeline Domain Names:"
curl -s http://localhost:8000/timeline | grep -qE '>[A-Z][a-z]+</a>' && echo "PASS" || echo "FAIL"

echo "7. Companies Quick Actions:"
curl -s http://localhost:8000/companies | grep -q "Events</a>" && echo "PASS" || echo "FAIL"

echo "8. Candidates LinkedIn:"
curl -s http://localhost:8000/candidates | grep -q "linkedin.com" && echo "PASS" || echo "FAIL"

echo "9. Entity Event Timeline:"
curl -s http://localhost:8000/entity/1 | grep -q "Event Timeline" && echo "PASS" || echo "FAIL"

echo "10. Search Source Links:"
curl -s "http://localhost:8000/search?event_type=ACQUIRED" | grep -q "Source</th>" && echo "PASS" || echo "FAIL"

echo "11. Dashboard Source Links:"
curl -s http://localhost:8000/ | grep -q "\[source\]" && echo "PASS" || echo "FAIL - needs data"

echo "12. Relationships Clickable:"
curl -s http://localhost:8000/relationships | grep -q 'href="/entity/' && echo "PASS" || echo "FAIL"

echo "13. Breadcrumbs:"
curl -s http://localhost:8000/entity/1 | grep -q "Dashboard</a>" && echo "PASS" || echo "FAIL"

echo "14. Pagination:"
curl -s http://localhost:8000/entities | grep -q 'page=' && echo "PASS" || echo "FAIL - needs 50+ entities"

echo "15. Empty State CTAs:"
grep -q "Run the pipeline" scripts/kg_viewer.py && echo "PASS" || echo "FAIL"

echo "16. Pipeline Last Run:"
curl -s http://localhost:8000/pipeline | grep -q "Last Pipeline Run" && echo "PASS" || echo "FAIL - run pipeline first"

echo "=== DONE ==="
```

---

## SUCCESS CRITERIA

**Target: 80%+ tests passing (13+ out of 16)**

**Known dependencies:**
- Tests 4, 5, 9, 11 require entities with data
- Tests 7, 8 require companies/people with signals
- Test 14 requires 50+ entities
- Test 16 requires pipeline to have run once

**If a test fails:**
1. Check if it's a data dependency
2. Check for Python syntax errors
3. Check server logs
4. Document and move on

---

## KEY LINE NUMBERS

| Feature | Lines |
|---------|-------|
| Entity "As Subject" table | 1980-1996 |
| Entity "As Object" table | 1998-2014 |
| Feeds URL display | 776 |
| Timeline source link | ~590-610 |
| Companies page | 1583-1673 |
| Candidates page | 1676-1764 |
| Relationships table | 1559-1573 |
| Search results | ~1100-1200 |
| Dashboard | ~400-550 |
| Entity detail start | 1767 |
| Enrichment display | 1831-1964 |
