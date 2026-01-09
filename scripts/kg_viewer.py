#!/usr/bin/env python3
"""Web UI to view the Knowledge Graph with timeline, search, and tagging."""

import sys
import json
from pathlib import Path
from datetime import date, timedelta, datetime
from collections import defaultdict
from typing import Optional, List

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi import FastAPI, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn

from src.storage.factory import get_article_storage, get_knowledge_graph
from src.config.feed_manager import FeedManager
from src.newsletter.generator import NewsletterGenerator

app = FastAPI(title="Recruiter Intelligence")

# Pipeline state file for tracking last run
PIPELINE_STATE_FILE = Path(__file__).parent / '.pipeline_state.json'


def save_pipeline_state(stats):
    """Save pipeline run state to file."""
    state = {'last_run': datetime.now().isoformat(), 'stats': stats}
    PIPELINE_STATE_FILE.write_text(json.dumps(state, default=str))


def get_pipeline_state():
    """Get last pipeline run state."""
    if PIPELINE_STATE_FILE.exists():
        try:
            return json.loads(PIPELINE_STATE_FILE.read_text())
        except Exception:
            return None
    return None


def get_kg():
    return get_knowledge_graph()


def get_storage():
    return get_article_storage()


# ===== HEALTH CHECK ENDPOINT =====
@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers."""
    try:
        storage = get_storage()
        stats = storage.get_stats()
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "database": "connected",
            "articles": stats.get("total_articles", 0),
        }
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )


# ===== DATA QUALITY FILTERS =====
# Filter out ONLY actual investment vehicles (SPVs, funds, family offices)
# Keep ALL real operating companies - startups of any industry

# These are investment vehicle patterns - NOT operating companies
INVESTMENT_VEHICLE_PATTERNS = [
    # SPV patterns (Special Purpose Vehicles)
    'a series of', 'series of ', 'spv,', ' spv ', ' spv',
    # Fund structures
    'fund lp', 'fund l.p.', 'fund, l.p.', 'fund, llc', 'fund llc',
    'master fund', 'offshore fund', 'opportunity fund', 'access fund',
    # GP/LP structures (fund management)
    'gp llc', 'gp, llc', 'gp ii', 'gp iii', 'gp iv', 'gp ltd',
    'capital lp', 'partners lp', 'partners l.p.', 'ventures lp',
    'partners i,', 'partners ii,', 'partners iii,', 'partners iv,',
    'partners i-', 'partners ii-', 'partners iii-',
    # Real estate investment
    'operating partnership', 'reit', 'municipal', 'real estate',
    'dst', 'apartment', 'senior investors', 'housing',
    # Known fund-only entities
    'witz ventures llc', 'anchor capital gp', 'springcoast',
    'growth partners', 'alternative investment',
    # Address-like names (real estate deals)
    'investors, llc', 'holdings, llc',
]

EXCLUDED_COMPANY_PATTERNS = [
    # Date patterns in SPV names (e.g., "SpaceX Dec 2025 a Series of...")
    r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{4}\s+a\s+series',
    # Numbered fund series (Fund I, Fund II, etc.)
    r'\bfund\s+(i|ii|iii|iv|v|vi|vii|viii|ix|x)\b',
    # Fund series patterns
    r'fund\s+[ivx]+\s+llc',
    # Series patterns
    r'-\s*series\s*\d',
    # Quarter patterns in SPV names
    r'\bq[1-4]\s+\d+\s+a\s+series',
    # Address patterns (123 Main St LLC)
    r'^\d+\s+[a-z]+\s+(st|ave|blvd|rd|way|dr)',
]

def is_investment_vehicle(name: str) -> bool:
    """Check if a name is an investment vehicle (fund/SPV) vs real operating company.

    Be CONSERVATIVE - only filter out obvious investment structures.
    When in doubt, KEEP the company (might be a real startup).
    """
    if not name:
        return False
    name_lower = name.lower()

    # Check for investment vehicle patterns
    for pattern in INVESTMENT_VEHICLE_PATTERNS:
        if pattern in name_lower:
            return True

    # Check regex patterns
    import re
    for pattern in EXCLUDED_COMPANY_PATTERNS:
        if re.search(pattern, name_lower, re.IGNORECASE):
            return True

    return False


def has_news_coverage(kg, entity_name: str) -> bool:
    """Check if an entity has relationships from news (not just SEC filings)."""
    rels = kg.query(subject=entity_name, limit=50)
    rels += kg.query(obj=entity_name, limit=50)

    for rel in rels:
        context = getattr(rel, 'context', '') or ''
        # News relationships don't have "Form D" in context
        if context and 'Form D' not in context:
            return True
    return False


def get_real_operating_companies(kg):
    """Get all real operating companies (any industry, excludes investment vehicles)."""
    all_companies = kg.search_entities('', entity_type='company')

    real_companies = []
    for company in all_companies:
        # Skip investment vehicles (SPVs, funds, etc.)
        if is_investment_vehicle(company.name):
            continue

        # Check if has news coverage (higher quality signal)
        news = has_news_coverage(kg, company.name)
        real_companies.append((company, news))

    return real_companies


# Alias for compatibility
get_real_tech_companies = get_real_operating_companies


def get_real_executives(kg):
    """Get people who are real tech executives (not fund managers)."""
    all_people = kg.search_entities('', entity_type='person')

    real_executives = []
    for person in all_people:
        # Skip obviously bad names
        if not person.name or len(person.name) < 3:
            continue

        # Skip AI model names that were incorrectly extracted
        if any(x in person.name.lower() for x in ['gpt-', 'claude ', 'gemini ', 'llama ']):
            continue

        # Check for executive relationships with real companies
        exec_rels = kg.query(subject=person.name, limit=20)
        has_real_exec_rel = False
        company_name = None
        is_from_news = False

        for rel in exec_rels:
            if rel.predicate in ['CEO_OF', 'CTO_OF', 'CFO_OF', 'FOUNDED', 'OFFICER_OF', 'EXECUTIVE_OF', 'DIRECTOR_OF']:
                obj_name = rel.object.name if hasattr(rel.object, 'name') else str(rel.object)

                # Skip if the company is an investment vehicle
                if is_investment_vehicle(obj_name):
                    continue

                has_real_exec_rel = True
                company_name = obj_name

                # Check if from news (higher quality)
                context = getattr(rel, 'context', '') or ''
                if 'Form D' not in context:
                    is_from_news = True
                break

        if has_real_exec_rel:
            real_executives.append((person, company_name, is_from_news))

    return real_executives


# Enhanced HTML Template with better styling
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Recruiter Intelligence{title_suffix}</title>
    <style>
        :root {{
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --success: #16a34a;
            --warning: #ea580c;
            --danger: #dc2626;
            --purple: #7c3aed;
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-300: #d1d5db;
            --gray-500: #6b7280;
            --gray-700: #374151;
            --gray-900: #111827;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 0;
            background: var(--gray-50);
            color: var(--gray-900);
        }}
        .header {{
            background: white;
            border-bottom: 1px solid var(--gray-200);
            padding: 16px 24px;
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        .header-content {{
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .logo {{
            font-size: 1.25em;
            font-weight: 700;
            color: var(--primary);
            text-decoration: none;
        }}
        .nav {{
            display: flex;
            gap: 4px;
            align-items: center;
        }}
        .nav a {{
            padding: 8px 16px;
            color: var(--gray-700);
            text-decoration: none;
            border-radius: 6px;
            font-size: 0.9em;
            transition: all 0.15s;
        }}
        .nav a:hover {{
            background: var(--gray-100);
            color: var(--gray-900);
        }}
        .nav a.active {{
            background: var(--primary);
            color: white;
        }}
        .nav-group {{
            position: relative;
        }}
        .nav-group-label {{
            padding: 8px 16px;
            color: var(--gray-500);
            font-size: 0.85em;
            cursor: pointer;
            border-radius: 6px;
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .nav-group-label:hover {{
            background: var(--gray-100);
            color: var(--gray-700);
        }}
        .nav-group-label::after {{
            content: "▾";
            font-size: 0.7em;
        }}
        .nav-group:hover .nav-dropdown {{
            display: block;
        }}
        .nav-dropdown {{
            display: none;
            position: absolute;
            top: 100%;
            left: 0;
            background: white;
            border: 1px solid var(--gray-200);
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            min-width: 180px;
            z-index: 200;
            padding: 8px 0;
        }}
        .nav-dropdown a {{
            display: block;
            padding: 10px 16px;
            border-radius: 0;
        }}
        .nav-dropdown a:hover {{
            background: var(--gray-50);
        }}
        .nav-divider {{
            width: 1px;
            height: 24px;
            background: var(--gray-200);
            margin: 0 8px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }}
        h1 {{
            color: var(--gray-900);
            margin: 0 0 24px 0;
            font-size: 1.75em;
        }}
        h2 {{
            color: var(--gray-700);
            margin: 32px 0 16px 0;
            font-size: 1.25em;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border: 1px solid var(--gray-200);
        }}
        .stat-value {{
            font-size: 2.5em;
            font-weight: 700;
            color: var(--primary);
            line-height: 1;
        }}
        .stat-label {{
            color: var(--gray-500);
            margin-top: 8px;
            font-size: 0.9em;
        }}
        .card {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border: 1px solid var(--gray-200);
            margin-bottom: 24px;
            overflow: hidden;
        }}
        .card-header {{
            padding: 16px 20px;
            border-bottom: 1px solid var(--gray-200);
            font-weight: 600;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--gray-100);
        }}
        th {{
            background: var(--gray-50);
            font-weight: 600;
            color: var(--gray-700);
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        tr:hover {{
            background: var(--gray-50);
        }}
        .tag {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.8em;
            font-weight: 500;
        }}
        .tag-company {{
            background: #dbeafe;
            color: #1e40af;
        }}
        .tag-person {{
            background: #f3e8ff;
            color: #6b21a8;
        }}
        .tag-investor {{
            background: #dcfce7;
            color: #166534;
        }}
        .tag-ACQUIRED {{
            background: #ffedd5;
            color: #c2410c;
        }}
        .tag-FUNDED_BY {{
            background: #dcfce7;
            color: #166534;
        }}
        .tag-HIRED_BY {{
            background: #dbeafe;
            color: #1e40af;
        }}
        .tag-DEPARTED_FROM {{
            background: #fee2e2;
            color: #b91c1c;
        }}
        .tag-LAID_OFF {{
            background: #fee2e2;
            color: #b91c1c;
        }}
        .tag-hot {{
            background: #fef3c7;
            color: #b45309;
        }}
        .tag-priority {{
            background: #fee2e2;
            color: #b91c1c;
        }}
        .tag-hiring {{
            background: #dcfce7;
            color: #166534;
        }}
        .entity-tag {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            margin: 2px;
            cursor: pointer;
            border: 1px solid transparent;
        }}
        .entity-tag:hover {{
            opacity: 0.8;
        }}
        a {{
            color: var(--primary);
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .btn {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 0.9em;
            font-weight: 500;
            cursor: pointer;
            border: none;
            transition: all 0.15s;
        }}
        .btn-primary {{
            background: var(--primary);
            color: white;
        }}
        .btn-primary:hover {{
            background: var(--primary-dark);
        }}
        .btn-secondary {{
            background: var(--gray-100);
            color: var(--gray-700);
            border: 1px solid var(--gray-300);
        }}
        .btn-secondary:hover {{
            background: var(--gray-200);
        }}
        .btn-sm {{
            padding: 4px 8px;
            font-size: 0.8em;
        }}
        .search-box {{
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }}
        .search-input {{
            flex: 1;
            min-width: 200px;
            padding: 10px 16px;
            border: 1px solid var(--gray-300);
            border-radius: 8px;
            font-size: 0.95em;
        }}
        .search-input:focus {{
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
        }}
        .filter-select {{
            padding: 10px 16px;
            border: 1px solid var(--gray-300);
            border-radius: 8px;
            font-size: 0.95em;
            background: white;
            min-width: 150px;
        }}
        .timeline {{
            position: relative;
            padding-left: 30px;
        }}
        .timeline::before {{
            content: '';
            position: absolute;
            left: 8px;
            top: 0;
            bottom: 0;
            width: 2px;
            background: var(--gray-200);
        }}
        .timeline-date {{
            font-weight: 600;
            color: var(--gray-700);
            margin: 24px 0 12px 0;
            font-size: 0.95em;
        }}
        .timeline-event {{
            position: relative;
            background: white;
            border: 1px solid var(--gray-200);
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 12px;
        }}
        .timeline-event::before {{
            content: '';
            position: absolute;
            left: -26px;
            top: 16px;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--primary);
            border: 2px solid white;
            box-shadow: 0 0 0 2px var(--primary);
        }}
        .timeline-event.acquisition::before {{
            background: var(--warning);
            box-shadow: 0 0 0 2px var(--warning);
        }}
        .timeline-event.funding::before {{
            background: var(--success);
            box-shadow: 0 0 0 2px var(--success);
        }}
        .timeline-event.hire::before {{
            background: var(--primary);
            box-shadow: 0 0 0 2px var(--primary);
        }}
        .timeline-event.departure::before {{
            background: var(--danger);
            box-shadow: 0 0 0 2px var(--danger);
        }}
        .timeline-content {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }}
        .timeline-text {{
            flex: 1;
        }}
        .timeline-source {{
            color: var(--gray-500);
            font-size: 0.85em;
        }}
        .empty-state {{
            text-align: center;
            padding: 48px;
            color: var(--gray-500);
        }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.75em;
            font-weight: 600;
            margin-left: 8px;
        }}
        .badge-new {{
            background: var(--success);
            color: white;
        }}
        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 24px;
        }}
        @media (max-width: 768px) {{
            .grid-2 {{
                grid-template-columns: 1fr;
            }}
        }}
        .confidence {{
            display: inline-block;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: 500;
        }}
        .confidence-high {{
            background: #dcfce7;
            color: #166534;
        }}
        .confidence-medium {{
            background: #fef3c7;
            color: #b45309;
        }}
        .confidence-low {{
            background: #fee2e2;
            color: #b91c1c;
        }}
        .enrichment-status {{
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 6px;
            vertical-align: middle;
        }}
        .enriched {{
            background: var(--success);
            box-shadow: 0 0 4px var(--success);
        }}
        .not-enriched {{
            background: var(--gray-300);
        }}
        .quality-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: 500;
            margin-left: 8px;
        }}
        .quality-high {{
            background: #dcfce7;
            color: #166534;
        }}
        .quality-medium {{
            background: #fef3c7;
            color: #b45309;
        }}
        .quality-low {{
            background: #fee2e2;
            color: #b91c1c;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <a href="/" class="logo">Recruiter Intelligence</a>
            <div class="nav">
                <a href="/" class="{nav_dashboard}">Dashboard</a>
                <div class="nav-divider"></div>
                <a href="/companies" class="{nav_companies}">Companies</a>
                <a href="/candidates" class="{nav_candidates}">Candidates</a>
                <a href="/newsletter" class="{nav_newsletter}">Newsletter</a>
                <div class="nav-divider"></div>
                <div class="nav-group">
                    <span class="nav-group-label">Data</span>
                    <div class="nav-dropdown">
                        <a href="/feeds" class="{nav_feeds}">RSS Feeds</a>
                        <a href="/pipeline" class="{nav_pipeline}">Pipeline</a>
                    </div>
                </div>
                <div class="nav-group">
                    <span class="nav-group-label">Explore</span>
                    <div class="nav-dropdown">
                        <a href="/timeline" class="{nav_timeline}">Timeline</a>
                        <a href="/search" class="{nav_search}">Search</a>
                        <a href="/entities" class="{nav_entities}">All Entities</a>
                        <a href="/relationships" class="{nav_relationships}">All Relationships</a>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <div class="container">
        {content}
    </div>
</body>
</html>
"""


def render(content: str, active: str = "", title_suffix: str = "") -> str:
    """Render HTML with navigation highlighting."""
    nav_states = {
        'nav_dashboard': 'active' if active == 'dashboard' else '',
        'nav_feeds': 'active' if active == 'feeds' else '',
        'nav_pipeline': 'active' if active == 'pipeline' else '',
        'nav_timeline': 'active' if active == 'timeline' else '',
        'nav_search': 'active' if active == 'search' else '',
        'nav_entities': 'active' if active == 'entities' else '',
        'nav_relationships': 'active' if active == 'relationships' else '',
        'nav_companies': 'active' if active == 'companies' else '',
        'nav_candidates': 'active' if active == 'candidates' else '',
        'nav_newsletter': 'active' if active == 'newsletter' else '',
    }
    return HTML_TEMPLATE.format(
        content=content,
        title_suffix=f" - {title_suffix}" if title_suffix else "",
        **nav_states
    )


def confidence_badge(conf: float) -> str:
    """Render confidence as colored badge."""
    if conf >= 0.9:
        return f'<span class="confidence confidence-high">{conf:.0%}</span>'
    elif conf >= 0.75:
        return f'<span class="confidence confidence-medium">{conf:.0%}</span>'
    else:
        return f'<span class="confidence confidence-low">{conf:.0%}</span>'


def enrichment_indicator(entity_id: int, kg) -> str:
    """Render enrichment status indicator."""
    enrichment = kg.get_enrichment(entity_id)
    if enrichment:
        return '<span class="enrichment-status enriched" title="Enriched"></span>'
    else:
        return '<span class="enrichment-status not-enriched" title="Not enriched"></span>'


def get_enrichment_stats(kg) -> dict:
    """Get enrichment coverage stats."""
    with kg._connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
        enriched = conn.execute("SELECT COUNT(DISTINCT entity_id) FROM kg_enrichment").fetchone()[0]
        return {
            'total': total,
            'enriched': enriched,
            'coverage': round(enriched / total * 100, 1) if total > 0 else 0
        }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    kg = get_kg()
    storage = get_storage()

    stats = kg.get_stats()
    db_stats = storage.get_stats()
    enr_stats = get_enrichment_stats(kg)

    # Get recent data
    acquisitions = kg.query(predicate="ACQUIRED", limit=5)
    funding = kg.query(predicate="FUNDED_BY", limit=5)
    hires = kg.query(predicate="HIRED_BY", limit=5)
    departures = kg.query(predicate="DEPARTED_FROM", limit=5)

    # Calculate enrichment color
    enr_color = "var(--success)" if enr_stats['coverage'] > 50 else ("var(--warning)" if enr_stats['coverage'] > 20 else "var(--gray-500)")

    content = f"""
    <h1>Dashboard</h1>

    <div class="stats">
        <div class="stat-card">
            <div class="stat-value">{stats['total_entities']}</div>
            <div class="stat-label">Total Entities</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{stats['total_relationships']}</div>
            <div class="stat-label">Total Relationships</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{db_stats['total_articles']}</div>
            <div class="stat-label">Articles Processed</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{db_stats['high_signal_articles']}</div>
            <div class="stat-label">High Signal Articles</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" style="color: {enr_color};">{enr_stats['coverage']}%</div>
            <div class="stat-label">Enrichment Coverage ({enr_stats['enriched']}/{enr_stats['total']})</div>
        </div>
    </div>

    <div class="grid-2">
        <div class="card">
            <div class="card-header">
                Recent Acquisitions
                <a href="/search?event_type=ACQUIRED" class="btn btn-secondary btn-sm">View All</a>
            </div>
            <table>
                <tr><th>Acquirer</th><th>Target</th><th>Confidence</th><th></th></tr>
    """

    for rel in acquisitions:
        source_link = f' <a href="{rel.source_url}" target="_blank" style="font-size: 0.8em;">[source]</a>' if rel.source_url else ''
        content += f"""
                <tr>
                    <td><span class="tag tag-company">{rel.subject.name}</span></td>
                    <td><span class="tag tag-company">{rel.object.name}</span></td>
                    <td>{confidence_badge(rel.confidence)}</td>
                    <td>{source_link}</td>
                </tr>
        """

    if not acquisitions:
        content += '<tr><td colspan="4" class="empty-state">No acquisitions yet. <a href="/pipeline">Run the pipeline</a> to extract events.</td></tr>'

    content += """
            </table>
        </div>

        <div class="card">
            <div class="card-header">
                Recent Funding
                <a href="/search?event_type=FUNDED_BY" class="btn btn-secondary btn-sm">View All</a>
            </div>
            <table>
                <tr><th>Company</th><th>Investor</th><th>Confidence</th><th></th></tr>
    """

    for rel in funding:
        source_link = f' <a href="{rel.source_url}" target="_blank" style="font-size: 0.8em;">[source]</a>' if rel.source_url else ''
        content += f"""
                <tr>
                    <td><span class="tag tag-company">{rel.subject.name}</span></td>
                    <td><span class="tag tag-investor">{rel.object.name}</span></td>
                    <td>{confidence_badge(rel.confidence)}</td>
                    <td>{source_link}</td>
                </tr>
        """

    if not funding:
        content += '<tr><td colspan="4" class="empty-state">No funding events yet. <a href="/pipeline">Run the pipeline</a> to extract events.</td></tr>'

    content += """
            </table>
        </div>

        <div class="card">
            <div class="card-header">
                Recent Hires
                <a href="/search?event_type=HIRED_BY" class="btn btn-secondary btn-sm">View All</a>
            </div>
            <table>
                <tr><th>Person</th><th>Company</th><th>Confidence</th><th></th></tr>
    """

    for rel in hires:
        source_link = f' <a href="{rel.source_url}" target="_blank" style="font-size: 0.8em;">[source]</a>' if rel.source_url else ''
        content += f"""
                <tr>
                    <td><span class="tag tag-person">{rel.subject.name}</span></td>
                    <td><span class="tag tag-company">{rel.object.name}</span></td>
                    <td>{confidence_badge(rel.confidence)}</td>
                    <td>{source_link}</td>
                </tr>
        """

    if not hires:
        content += '<tr><td colspan="4" class="empty-state">No hires yet. <a href="/pipeline">Run the pipeline</a> to extract events.</td></tr>'

    content += """
            </table>
        </div>

        <div class="card">
            <div class="card-header">
                Recent Departures
                <a href="/search?event_type=DEPARTED_FROM" class="btn btn-secondary btn-sm">View All</a>
            </div>
            <table>
                <tr><th>Person</th><th>Company</th><th>Confidence</th><th></th></tr>
    """

    for rel in departures:
        source_link = f' <a href="{rel.source_url}" target="_blank" style="font-size: 0.8em;">[source]</a>' if rel.source_url else ''
        content += f"""
                <tr>
                    <td><span class="tag tag-person">{rel.subject.name}</span></td>
                    <td><span class="tag tag-company">{rel.object.name}</span></td>
                    <td>{confidence_badge(rel.confidence)}</td>
                    <td>{source_link}</td>
                </tr>
        """

    if not departures:
        content += '<tr><td colspan="4" class="empty-state">No departures yet. <a href="/pipeline">Run the pipeline</a> to extract events.</td></tr>'

    content += """
            </table>
        </div>
    </div>
    """

    return render(content, active='dashboard', title_suffix='Dashboard')


def format_time_ago(dt) -> str:
    """Format datetime as human-readable 'time ago'."""
    if not dt:
        return "Never"
    from datetime import datetime, timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - dt
    if diff.days > 0:
        return f"{diff.days}d ago"
    hours = diff.seconds // 3600
    if hours > 0:
        return f"{hours}h ago"
    minutes = diff.seconds // 60
    return f"{minutes}m ago" if minutes > 0 else "Just now"


@app.get("/feeds", response_class=HTMLResponse)
async def feeds_list():
    """List all feeds with stats."""
    fm = FeedManager()
    all_feeds = fm.list_feeds()

    # Count stats
    active = sum(1 for f in all_feeds if f.get("enabled", True))
    disabled = len(all_feeds) - active

    content = f"""
    <h1>Feeds</h1>
    <p style="color: var(--gray-500); margin-bottom: 24px;">
        {len(all_feeds)} feeds configured &bull; {active} active &bull; {disabled} disabled
        <a href="/feeds/add" class="btn btn-primary" style="float: right;">+ Add Feed</a>
    </p>

    <div class="card">
        <div class="card-header">
            RSS Feeds
            <span style="font-size: 0.85em; color: var(--gray-500); font-weight: normal; margin-left: 12px;">
                <span style="color: var(--success);">●</span> Active
                <span style="color: var(--warning);">●</span> Issues
                <span style="color: var(--gray-300);">●</span> Disabled
            </span>
        </div>
        <table>
            <tr>
                <th style="width: 30px;"></th>
                <th>Name</th>
                <th>Priority</th>
                <th>Event Types</th>
                <th>Articles</th>
                <th>Success</th>
                <th>Last Fetch</th>
                <th>Actions</th>
            </tr>
    """

    for feed in all_feeds:
        enabled = feed.get("enabled", True)
        stats = feed.get("stats", {})

        # Status indicator
        if not enabled:
            status = '<span style="color: var(--gray-300);">●</span>'
        elif stats.get("consecutive_failures", 0) > 0:
            status = '<span style="color: var(--warning);">●</span>'
        else:
            status = '<span style="color: var(--success);">●</span>'

        # Priority label
        priority_map = {0: "High", 1: "Medium", 2: "Low"}
        priority = priority_map.get(feed.get("priority", 1), "Medium")

        # Event types
        event_types = ", ".join(feed.get("event_types", [])[:3])
        if len(feed.get("event_types", [])) > 3:
            event_types += "..."

        # Success rate
        rate = stats.get("success_rate", 1.0)
        rate_color = "var(--success)" if rate >= 0.9 else ("var(--warning)" if rate >= 0.7 else "var(--danger)")
        rate_str = f'{rate:.0%}'

        # Last fetch
        last_fetch = format_time_ago(stats.get("last_fetch_at"))

        # Encode name for URL
        import urllib.parse
        name_encoded = urllib.parse.quote(feed["name"], safe='')

        content += f"""
            <tr>
                <td>{status}</td>
                <td>
                    <strong>{feed['name']}</strong>
                    <br><a href="{feed['url']}" target="_blank" style="font-size: 0.8em; color: var(--gray-500);">{feed['url'][:60]}...</a>
                </td>
                <td>{priority}</td>
                <td style="font-size: 0.85em;">{event_types}</td>
                <td>{stats.get('total_articles', 0)}</td>
                <td style="color: {rate_color};">{rate_str}</td>
                <td>{last_fetch}</td>
                <td>
                    <form method="post" action="/feeds/{name_encoded}/toggle" style="display: inline;">
                        <button type="submit" class="btn btn-sm btn-secondary">
                            {'Disable' if enabled else 'Enable'}
                        </button>
                    </form>
                    <form method="post" action="/feeds/{name_encoded}/delete" style="display: inline; margin-left: 4px;"
                          onsubmit="return confirm('Delete this feed?');">
                        <button type="submit" class="btn btn-sm btn-secondary" style="color: var(--danger);">×</button>
                    </form>
                </td>
            </tr>
        """

    if not all_feeds:
        content += '<tr><td colspan="8" class="empty-state">No feeds configured</td></tr>'

    content += """
        </table>
    </div>
    """

    return render(content, active='feeds', title_suffix='Feeds')


@app.get("/feeds/add", response_class=HTMLResponse)
async def feeds_add_form():
    """Show form to add a new feed."""
    fm = FeedManager()
    suggested = fm.get_suggested_feeds()

    content = """
    <h1>Add Feed</h1>

    <div class="card">
        <div class="card-header">Add Custom Feed</div>
        <div style="padding: 20px;">
            <form method="post" action="/feeds/add">
                <div style="margin-bottom: 16px;">
                    <label style="display: block; margin-bottom: 4px; font-weight: 500;">Feed URL *</label>
                    <input type="url" name="url" class="search-input" style="width: 100%;"
                           placeholder="https://example.com/feed.xml" required>
                </div>

                <div style="margin-bottom: 16px;">
                    <label style="display: block; margin-bottom: 4px; font-weight: 500;">Name *</label>
                    <input type="text" name="name" class="search-input" style="width: 100%;"
                           placeholder="Feed name" required>
                </div>

                <div style="margin-bottom: 16px;">
                    <label style="display: block; margin-bottom: 4px; font-weight: 500;">Priority</label>
                    <select name="priority" class="filter-select">
                        <option value="0">High - Fetch first</option>
                        <option value="1" selected>Medium</option>
                        <option value="2">Low</option>
                    </select>
                </div>

                <div style="margin-bottom: 16px;">
                    <label style="display: block; margin-bottom: 4px; font-weight: 500;">Event Types</label>
                    <div style="display: flex; gap: 16px; flex-wrap: wrap;">
                        <label><input type="checkbox" name="event_types" value="funding" checked> Funding</label>
                        <label><input type="checkbox" name="event_types" value="acquisition" checked> Acquisition</label>
                        <label><input type="checkbox" name="event_types" value="executive_move"> Executive Move</label>
                        <label><input type="checkbox" name="event_types" value="layoff"> Layoff</label>
                        <label><input type="checkbox" name="event_types" value="ipo"> IPO</label>
                        <label><input type="checkbox" name="event_types" value="startup"> Startup</label>
                    </div>
                </div>

                <div style="margin-top: 24px;">
                    <a href="/feeds" class="btn btn-secondary">Cancel</a>
                    <button type="submit" class="btn btn-primary" style="margin-left: 8px;">Add Feed</button>
                </div>
            </form>
        </div>
    </div>
    """

    # Group suggested feeds by category
    if suggested:
        by_category = {}
        for feed in suggested:
            cat = feed.get("category", "Other")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(feed)

        content += """
        <div class="card" style="margin-top: 24px;">
            <div class="card-header">Suggested Feeds</div>
            <div style="padding: 20px;">
        """

        for category, feeds in by_category.items():
            content += f'<h3 style="margin-top: 0; color: var(--gray-700);">{category}</h3>'
            for feed in feeds:
                import urllib.parse
                url_encoded = urllib.parse.quote(feed["url"], safe='')
                content += f"""
                <div style="display: flex; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                    <form method="post" action="/feeds/add-suggested" style="margin-right: 12px;">
                        <input type="hidden" name="url" value="{feed['url']}">
                        <button type="submit" class="btn btn-sm btn-primary">+ Add</button>
                    </form>
                    <div>
                        <strong>{feed['name']}</strong>
                        <span style="color: var(--gray-500); margin-left: 8px;">{feed['description']}</span>
                    </div>
                </div>
                """

        content += """
            </div>
        </div>
        """

    return render(content, active='feeds', title_suffix='Add Feed')


@app.post("/feeds/add", response_class=HTMLResponse)
async def feeds_add(
    url: str = Form(...),
    name: str = Form(...),
    priority: int = Form(1),
    event_types: List[str] = Form(default=[])
):
    """Add a new feed."""
    fm = FeedManager()
    try:
        if not event_types:
            event_types = ["funding", "acquisition"]
        fm.add_feed(url=url, name=name, priority=priority, event_types=event_types)
        return RedirectResponse(url="/feeds", status_code=303)
    except ValueError as e:
        content = f"""
        <h1>Error Adding Feed</h1>
        <div class="card" style="border-color: var(--danger);">
            <div style="padding: 20px; color: var(--danger);">
                {str(e)}
            </div>
        </div>
        <a href="/feeds/add" class="btn btn-primary" style="margin-top: 16px;">Try Again</a>
        """
        return render(content, active='feeds', title_suffix='Error')


@app.post("/feeds/add-suggested", response_class=HTMLResponse)
async def feeds_add_suggested(url: str = Form(...)):
    """Add a suggested feed by URL."""
    fm = FeedManager()
    try:
        fm.add_suggested_feed(url)
        return RedirectResponse(url="/feeds", status_code=303)
    except ValueError as e:
        return RedirectResponse(url="/feeds/add", status_code=303)


@app.post("/feeds/{name}/toggle")
async def feeds_toggle(name: str):
    """Toggle a feed on/off."""
    import urllib.parse
    name = urllib.parse.unquote(name)
    fm = FeedManager()
    feed = fm.get_feed(name)
    if feed:
        new_state = not feed.get("enabled", True)
        fm.toggle_feed(name, new_state)
    return RedirectResponse(url="/feeds", status_code=303)


@app.post("/feeds/{name}/delete")
async def feeds_delete(name: str):
    """Delete a feed."""
    import urllib.parse
    name = urllib.parse.unquote(name)
    fm = FeedManager()
    fm.delete_feed(name)
    return RedirectResponse(url="/feeds", status_code=303)


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_status():
    """Show pipeline features and run status."""
    kg = get_kg()
    storage = get_storage()

    # Get stats
    kg_stats = kg.get_stats()
    db_stats = storage.get_stats()

    # Get data source statistics using direct SQLite query
    import sqlite3
    conn = sqlite3.connect(kg.db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute("SELECT source_url, COUNT(*) as cnt FROM kg_relationships GROUP BY source_url")
    all_rels = cursor.fetchall()

    # Categorize relationships by source
    sec_rels = 0
    news_rels = 0
    for row in all_rels:
        url = row['source_url'] or ''
        if 'sec.gov' in url:
            sec_rels += int(row['cnt'])
        else:
            news_rels += int(row['cnt'])

    # Get enrichment stats
    cursor = conn.execute("SELECT source, COUNT(*) as cnt FROM kg_enrichment GROUP BY source")
    enrichment_stats = cursor.fetchall()
    enrichment_by_source = {row['source']: row['cnt'] for row in enrichment_stats}
    total_enrichments = sum(enrichment_by_source.values()) if enrichment_by_source else 0
    conn.close()

    # Check for last pipeline run
    pipeline_state = get_pipeline_state()

    content = """
    <h1>Pipeline Status</h1>
    <p style="color: var(--gray-500); margin-bottom: 24px;">
        Enhanced data sources and extraction features
    </p>
    """

    # Show last run info if available
    if pipeline_state:
        state_stats = pipeline_state.get('stats', {})
        content += f"""
        <div class="card" style="margin-bottom: 24px;">
            <div class="card-header">Last Pipeline Run</div>
            <div style="padding: 16px;">
                <strong>Run at:</strong> {pipeline_state['last_run']}<br>
                <strong>Articles fetched:</strong> {state_stats.get('fetched_articles', 'N/A')}<br>
                <strong>High signal:</strong> {state_stats.get('high_signal_articles', 'N/A')}<br>
                <strong>Relationships:</strong> {state_stats.get('extracted_relationships', 'N/A')}
            </div>
        </div>
        """

    content += """
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px;">
    """

    # Data source cards - user-friendly, no technical details
    data_sources = [
        {
            "name": "RSS News Feeds",
            "description": "Real-time tech news aggregation",
            "stats": f"{db_stats.get('total_articles', 0)} articles collected",
            "detail": "Crunchbase, Techmeme, VentureBeat, FastCompany",
            "icon": "📰",
            "color": "var(--primary)",
        },
        {
            "name": "SEC Filings",
            "description": "Official funding disclosures",
            "stats": f"{sec_rels} relationships extracted" if sec_rels > 0 else "Ready - run pipeline",
            "detail": "Form D regulatory filings",
            "icon": "📜",
            "color": "var(--success)",
        },
        {
            "name": "Entity Extraction",
            "description": "AI-powered relationship mapping",
            "stats": f"{kg_stats.get('total_relationships', 0)} relationships found",
            "detail": "Companies, people, investors, deals",
            "icon": "🧠",
            "color": "var(--purple)",
        },
        {
            "name": "Company Enrichment",
            "description": "Detailed company profiles",
            "stats": f"{total_enrichments} companies enriched",
            "detail": "Funding, employees, description",
            "icon": "🔍",
            "color": "var(--warning)",
        },
    ]

    for src in data_sources:
        content += f"""
        <div class="card" style="margin: 0; border-top: 3px solid {src['color']};">
            <div style="padding: 16px;">
                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                    <span style="font-size: 1.5em;">{src["icon"]}</span>
                    <h3 style="margin: 0;">{src["name"]}</h3>
                </div>
                <p style="color: var(--gray-500); font-size: 0.9em; margin: 0 0 8px 0;">{src["description"]}</p>
                <p style="font-size: 1.1em; font-weight: bold; color: {src['color']}; margin: 0 0 4px 0;">{src["stats"]}</p>
                <p style="font-size: 0.8em; color: var(--gray-400); margin: 0;">{src["detail"]}</p>
            </div>
        </div>
        """

    content += "</div>"

    # Knowledge Graph Stats
    content += f"""
    <div class="card">
        <div class="card-header">Knowledge Graph Statistics</div>
        <div style="padding: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px;">
            <div>
                <div style="font-size: 2em; font-weight: bold; color: var(--primary);">{kg_stats.get('total_entities', 0)}</div>
                <div style="color: var(--gray-500);">Total Entities</div>
            </div>
            <div>
                <div style="font-size: 2em; font-weight: bold; color: var(--success);">{kg_stats.get('total_relationships', 0)}</div>
                <div style="color: var(--gray-500);">Relationships</div>
            </div>
            <div>
                <div style="font-size: 2em; font-weight: bold; color: var(--purple);">{db_stats.get('total_articles', 0)}</div>
                <div style="color: var(--gray-500);">Articles Processed</div>
            </div>
            <div>
                <div style="font-size: 2em; font-weight: bold; color: var(--warning);">{db_stats.get('high_signal_articles', 0)}</div>
                <div style="color: var(--gray-500);">High Signal</div>
            </div>
        </div>
    </div>
    """

    # Entity breakdown
    companies = len(kg.search_entities('', entity_type='company'))
    people = len(kg.search_entities('', entity_type='person'))
    investors = len(kg.search_entities('', entity_type='investor'))

    content += f"""
    <div class="card" style="margin-top: 16px;">
        <div class="card-header">Entity Breakdown</div>
        <table>
            <tr>
                <th>Type</th>
                <th>Count</th>
                <th>Percentage</th>
            </tr>
            <tr>
                <td>Companies</td>
                <td>{companies}</td>
                <td>{companies * 100 // max(1, kg_stats.get('total_entities', 1))}%</td>
            </tr>
            <tr>
                <td>People</td>
                <td>{people}</td>
                <td>{people * 100 // max(1, kg_stats.get('total_entities', 1))}%</td>
            </tr>
            <tr>
                <td>Investors</td>
                <td>{investors}</td>
                <td>{investors * 100 // max(1, kg_stats.get('total_entities', 1))}%</td>
            </tr>
        </table>
    </div>
    """

    # Run pipeline button with loading state
    content += """
    <div class="card" style="margin-top: 16px;">
        <div class="card-header">Run Pipeline</div>
        <div style="padding: 20px;">
            <p style="color: var(--gray-500); margin-bottom: 16px;">
                Run the daily pipeline to fetch new articles, extract entities, and update the knowledge graph.
            </p>
            <form id="pipeline-form" method="post" action="/pipeline/run">
                <div style="display: flex; gap: 16px; align-items: center; margin-bottom: 16px;">
                    <label>Days back:
                        <select name="days_back" class="filter-select">
                            <option value="1">1 day</option>
                            <option value="3">3 days</option>
                            <option value="7" selected>7 days</option>
                            <option value="14">14 days</option>
                            <option value="30">30 days</option>
                        </select>
                    </label>
                </div>
                <input type="hidden" name="use_form_d" value="on">
                <input type="hidden" name="use_spacy" value="on">
                <input type="hidden" name="use_cross_ref" value="on">
                <button type="submit" id="pipeline-btn" class="btn btn-primary">
                    <span id="btn-text">Run Pipeline</span>
                    <span id="btn-spinner" style="display: none; margin-left: 8px;">
                        <svg width="16" height="16" viewBox="0 0 24 24" style="animation: spin 1s linear infinite; vertical-align: middle;">
                            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" fill="none" stroke-dasharray="31.4 31.4" stroke-linecap="round"/>
                        </svg>
                    </span>
                </button>
                <span id="pipeline-status" style="margin-left: 12px; color: var(--gray-500);"></span>
            </form>
            <style>
                @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
                #pipeline-btn:disabled { opacity: 0.7; cursor: not-allowed; }
            </style>
            <script>
                document.getElementById('pipeline-form').addEventListener('submit', function(e) {
                    var btn = document.getElementById('pipeline-btn');
                    var btnText = document.getElementById('btn-text');
                    var spinner = document.getElementById('btn-spinner');
                    var status = document.getElementById('pipeline-status');

                    btn.disabled = true;
                    btnText.textContent = 'Running...';
                    spinner.style.display = 'inline';
                    status.textContent = 'Pipeline is processing. This may take a minute...';
                    status.style.color = 'var(--primary)';
                });
            </script>
        </div>
    </div>
    """

    return render(content, active='pipeline', title_suffix='Pipeline')


@app.post("/pipeline/run")
async def run_pipeline_action(
    days_back: int = Form(7),
    use_form_d: bool = Form(False),
    use_spacy: bool = Form(False),
    use_cross_ref: bool = Form(False),
    use_gdelt: bool = Form(False),
):
    """Run the pipeline from the UI."""
    try:
        from src.pipeline.daily import run_daily_pipeline

        stats = await run_daily_pipeline(
            days_back=days_back,
            use_form_d=use_form_d,
            use_spacy=use_spacy,
            use_gdelt=use_gdelt,
            use_cross_ref=use_cross_ref,
        )

        # Save pipeline state for "Last Run" display
        save_pipeline_state(stats)

        # Format results
        content = f"""
        <h1>Pipeline Complete</h1>

        <div class="card">
            <div class="card-header" style="background: var(--success); color: white;">Results</div>
            <div style="padding: 20px;">
                <p><strong>Time:</strong> {stats.get('elapsed_seconds', 0):.1f} seconds</p>
                <p><strong>Articles fetched:</strong> {stats.get('fetched_articles', 0)}</p>
                <p><strong>Articles saved:</strong> {stats.get('saved_articles', 0)}</p>
                <p><strong>High signal:</strong> {stats.get('high_signal_articles', 0)}</p>
                <p><strong>Relationships extracted:</strong> {stats.get('extracted_relationships', 0)}</p>
            </div>
        </div>
        """

        # Form D results
        form_d = stats.get('form_d', {})
        if form_d.get('enabled'):
            content += f"""
            <div class="card" style="margin-top: 16px;">
                <div class="card-header">SEC Form D Results</div>
                <div style="padding: 20px;">
                    <p><strong>Filings fetched:</strong> {form_d.get('filings_fetched', 0)}</p>
                    <p><strong>Relationships added:</strong> {form_d.get('relationships_added', 0)}</p>
                </div>
            </div>
            """

        # Cross-reference results
        cross_ref = stats.get('cross_reference', {})
        if cross_ref.get('enabled'):
            content += f"""
            <div class="card" style="margin-top: 16px;">
                <div class="card-header">Cross-Reference Results</div>
                <div style="padding: 20px;">
                    <p><strong>News funding events:</strong> {cross_ref.get('news_events', 0)}</p>
                    <p><strong>Form D events:</strong> {cross_ref.get('form_d_events', 0)}</p>
                    <p><strong>Matches found:</strong> {cross_ref.get('matches', 0)}</p>
                    <p><strong>Confidence boosts:</strong> {cross_ref.get('confidence_boosts', 0)}</p>
                </div>
            </div>
            """

        # Knowledge graph stats
        kg = stats.get('knowledge_graph', {})
        content += f"""
        <div class="card" style="margin-top: 16px;">
            <div class="card-header">Knowledge Graph</div>
            <div style="padding: 20px;">
                <p><strong>Total entities:</strong> {kg.get('total_entities', 0)}</p>
                <p><strong>Total relationships:</strong> {kg.get('total_relationships', 0)}</p>
            </div>
        </div>

        <a href="/pipeline" class="btn btn-primary" style="margin-top: 16px;">Back to Pipeline</a>
        """

        return render(content, active='pipeline', title_suffix='Pipeline Results')

    except Exception as e:
        content = f"""
        <h1>Pipeline Error</h1>
        <div class="card" style="border-color: var(--danger);">
            <div style="padding: 20px; color: var(--danger);">
                {str(e)}
            </div>
        </div>
        <a href="/pipeline" class="btn btn-primary" style="margin-top: 16px;">Back to Pipeline</a>
        """
        return render(content, active='pipeline', title_suffix='Error')


@app.get("/timeline", response_class=HTMLResponse)
async def timeline(
    days: int = Query(30, description="Days to look back"),
    event_type: str = Query(None, description="Filter by event type")
):
    kg = get_kg()

    since = date.today() - timedelta(days=days)

    # Get all relationships
    if event_type:
        all_rels = kg.query(predicate=event_type, since_date=since, limit=200)
    else:
        all_rels = kg.query(since_date=since, limit=200)

    # Group by date
    by_date = defaultdict(list)
    for rel in all_rels:
        event_date = rel.event_date or date.today()
        by_date[event_date].append(rel)

    # Sort dates descending
    sorted_dates = sorted(by_date.keys(), reverse=True)

    content = f"""
    <h1>Timeline</h1>

    <form method="get" class="search-box">
        <select name="days" class="filter-select">
            <option value="7" {"selected" if days == 7 else ""}>Last 7 days</option>
            <option value="30" {"selected" if days == 30 else ""}>Last 30 days</option>
            <option value="90" {"selected" if days == 90 else ""}>Last 90 days</option>
            <option value="365" {"selected" if days == 365 else ""}>Last year</option>
        </select>
        <select name="event_type" class="filter-select">
            <option value="">All Events</option>
            <option value="ACQUIRED" {"selected" if event_type == "ACQUIRED" else ""}>Acquisitions</option>
            <option value="FUNDED_BY" {"selected" if event_type == "FUNDED_BY" else ""}>Funding</option>
            <option value="HIRED_BY" {"selected" if event_type == "HIRED_BY" else ""}>Hires</option>
            <option value="DEPARTED_FROM" {"selected" if event_type == "DEPARTED_FROM" else ""}>Departures</option>
            <option value="LAID_OFF" {"selected" if event_type == "LAID_OFF" else ""}>Layoffs</option>
        </select>
        <button type="submit" class="btn btn-primary">Filter</button>
    </form>

    <div class="timeline">
    """

    if not sorted_dates:
        content += '<div class="empty-state">No events found for this time period</div>'

    for d in sorted_dates:
        date_str = d.strftime('%B %d, %Y') if d else 'Unknown Date'
        content += f'<div class="timeline-date">{date_str}</div>'

        for rel in by_date[d]:
            event_class = 'acquisition' if 'ACQUIRED' in rel.predicate else \
                         'funding' if 'FUNDED' in rel.predicate else \
                         'departure' if 'DEPARTED' in rel.predicate or 'LAID' in rel.predicate else 'hire'

            if rel.source_url:
                from urllib.parse import urlparse
                domain = urlparse(rel.source_url).netloc.replace('www.', '').split('.')[0].title()
                source_link = f'<a href="{rel.source_url}" target="_blank" class="timeline-source">{domain}</a>'
            else:
                source_link = ''

            # Format the event text based on predicate with metadata
            metadata = getattr(rel, 'metadata', {}) or {}
            amount_str = ""
            if metadata.get('amount'):
                amount_str = f' <strong style="color: var(--success);">({metadata["amount"]})</strong>'
            elif metadata.get('valuation'):
                amount_str = f' <strong style="color: var(--success);">(at {metadata["valuation"]} valuation)</strong>'
            elif metadata.get('count'):
                amount_str = f' <strong style="color: var(--danger);">({metadata["count"]} employees)</strong>'

            if rel.predicate == 'ACQUIRED':
                text = f'<span class="tag tag-company">{rel.subject.name}</span> acquired <span class="tag tag-company">{rel.object.name}</span>{amount_str}'
            elif rel.predicate == 'FUNDED_BY':
                text = f'<span class="tag tag-company">{rel.subject.name}</span> received funding from <span class="tag tag-investor">{rel.object.name}</span>{amount_str}'
            elif rel.predicate == 'HIRED_BY':
                text = f'<span class="tag tag-person">{rel.subject.name}</span> joined <span class="tag tag-company">{rel.object.name}</span>'
            elif rel.predicate == 'DEPARTED_FROM':
                text = f'<span class="tag tag-person">{rel.subject.name}</span> left <span class="tag tag-company">{rel.object.name}</span>'
            elif rel.predicate == 'LAID_OFF':
                text = f'<span class="tag tag-company">{rel.subject.name}</span> laid off employees{amount_str}'
            else:
                text = f'{rel.subject.name} {rel.predicate} {rel.object.name}'

            content += f"""
            <div class="timeline-event {event_class}">
                <div class="timeline-content">
                    <div class="timeline-text">
                        <span class="tag tag-{rel.predicate}">{rel.predicate}</span>
                        {text}
                        {confidence_badge(rel.confidence)}
                    </div>
                    {source_link}
                </div>
            </div>
            """

    content += '</div>'

    return render(content, active='timeline', title_suffix='Timeline')


@app.get("/search", response_class=HTMLResponse)
async def search(
    q: str = Query("", description="Search query"),
    entity_type: str = Query(None, description="Entity type filter"),
    event_type: str = Query(None, description="Event type filter"),
    days: int = Query(90, description="Days to look back")
):
    kg = get_kg()

    content = f"""
    <h1>Search</h1>

    <form method="get" class="search-box">
        <input type="text" name="q" class="search-input" placeholder="Search companies, people, investors..." value="{q}">
        <select name="entity_type" class="filter-select">
            <option value="">All Entity Types</option>
            <option value="company" {"selected" if entity_type == "company" else ""}>Companies</option>
            <option value="person" {"selected" if entity_type == "person" else ""}>People</option>
            <option value="investor" {"selected" if entity_type == "investor" else ""}>Investors</option>
        </select>
        <select name="event_type" class="filter-select">
            <option value="">All Event Types</option>
            <option value="ACQUIRED" {"selected" if event_type == "ACQUIRED" else ""}>Acquisitions</option>
            <option value="FUNDED_BY" {"selected" if event_type == "FUNDED_BY" else ""}>Funding</option>
            <option value="HIRED_BY" {"selected" if event_type == "HIRED_BY" else ""}>Hires</option>
            <option value="DEPARTED_FROM" {"selected" if event_type == "DEPARTED_FROM" else ""}>Departures</option>
        </select>
        <select name="days" class="filter-select">
            <option value="7" {"selected" if days == 7 else ""}>Last 7 days</option>
            <option value="30" {"selected" if days == 30 else ""}>Last 30 days</option>
            <option value="90" {"selected" if days == 90 else ""}>Last 90 days</option>
            <option value="365" {"selected" if days == 365 else ""}>Last year</option>
        </select>
        <button type="submit" class="btn btn-primary">Search</button>
    </form>
    """

    # Search entities
    if q or entity_type:
        entities = kg.search_entities(q or '', entity_type=entity_type)

        content += f"""
        <div class="card">
            <div class="card-header">
                Entities ({len(entities)} results)
            </div>
            <table>
                <tr><th>Name</th><th>Type</th><th>Mentions</th><th>First Seen</th><th>Last Seen</th></tr>
        """

        for entity in entities[:50]:
            content += f"""
                <tr>
                    <td><a href="/entity/{entity.id}">{entity.name}</a></td>
                    <td><span class="tag tag-{entity.entity_type}">{entity.entity_type}</span></td>
                    <td>{entity.mention_count}</td>
                    <td>{entity.first_seen or '-'}</td>
                    <td>{entity.last_seen or '-'}</td>
                </tr>
            """

        if not entities:
            content += '<tr><td colspan="5" class="empty-state">No entities found</td></tr>'

        content += '</table></div>'

    # Search relationships
    since = date.today() - timedelta(days=days)
    rels = kg.query(subject=q if q else None, predicate=event_type, since_date=since, limit=100)

    # Also search by object
    if q:
        obj_rels = kg.query(obj=q, predicate=event_type, since_date=since, limit=100)
        # Merge and dedupe
        seen_ids = {r.id for r in rels}
        for r in obj_rels:
            if r.id not in seen_ids:
                rels.append(r)

    content += f"""
    <div class="card">
        <div class="card-header">
            Relationships ({len(rels)} results)
        </div>
        <table>
            <tr><th>Subject</th><th>Event</th><th>Object</th><th>Amount</th><th>Date</th><th>Confidence</th><th>Source</th></tr>
    """

    for rel in rels:
        source_link = f'<a href="{rel.source_url}" target="_blank">View</a>' if rel.source_url else '-'
        metadata = getattr(rel, 'metadata', {}) or {}
        amount_display = metadata.get('amount') or metadata.get('valuation') or (f"{metadata.get('count')} employees" if metadata.get('count') else '-')
        content += f"""
            <tr>
                <td><span class="tag tag-{rel.subject.entity_type}">{rel.subject.name}</span></td>
                <td><span class="tag tag-{rel.predicate}">{rel.predicate}</span></td>
                <td><span class="tag tag-{rel.object.entity_type}">{rel.object.name}</span></td>
                <td><strong style="color: var(--success);">{amount_display}</strong></td>
                <td>{rel.event_date or '-'}</td>
                <td>{confidence_badge(rel.confidence)}</td>
                <td>{source_link}</td>
            </tr>
        """

    if not rels:
        content += '<tr><td colspan="7" class="empty-state">No relationships found</td></tr>'

    content += '</table></div>'

    return render(content, active='search', title_suffix='Search')


@app.get("/entities", response_class=HTMLResponse)
async def entities(entity_type: str = Query(None), page: int = Query(1)):
    kg = get_kg()
    all_entities = kg.search_entities('', entity_type=entity_type)

    # Pagination
    PAGE_SIZE = 50
    total = len(all_entities)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    paginated_entities = all_entities[start:end]

    content = f"""
    <h1>All Entities</h1>

    <form method="get" class="search-box">
        <select name="entity_type" class="filter-select" onchange="this.form.submit()">
            <option value="">All Types</option>
            <option value="company" {"selected" if entity_type == "company" else ""}>Companies</option>
            <option value="person" {"selected" if entity_type == "person" else ""}>People</option>
            <option value="investor" {"selected" if entity_type == "investor" else ""}>Investors</option>
        </select>
    </form>

    <div class="card">
        <div class="card-header">
            Entities ({total} total, page {page} of {total_pages})
            <span style="font-size: 0.85em; color: var(--gray-500); font-weight: normal; margin-left: 12px;">
                <span class="enrichment-status enriched" style="display: inline-block; vertical-align: middle;"></span> = enriched
            </span>
        </div>
        <table>
            <tr><th>Name</th><th>Type</th><th>Mentions</th><th>First Seen</th><th>Last Seen</th></tr>
    """

    for entity in paginated_entities:
        enr_ind = enrichment_indicator(entity.id, kg)
        content += f"""
            <tr>
                <td>{enr_ind}<a href="/entity/{entity.id}">{entity.name}</a></td>
                <td><span class="tag tag-{entity.entity_type}">{entity.entity_type}</span></td>
                <td>{entity.mention_count}</td>
                <td>{entity.first_seen or '-'}</td>
                <td>{entity.last_seen or '-'}</td>
            </tr>
        """

    if not all_entities:
        content += '<tr><td colspan="5" class="empty-state">No entities yet. <a href="/pipeline">Run the pipeline</a> to extract entities from news.</td></tr>'

    content += '</table>'

    # Pagination controls
    if total_pages > 1:
        content += '<div style="margin-top: 16px; text-align: center;">'
        type_param = f'&entity_type={entity_type}' if entity_type else ''
        for p in range(1, total_pages + 1):
            active = 'background: var(--primary); color: white;' if p == page else ''
            content += f'<a href="/entities?page={p}{type_param}" class="btn btn-sm btn-secondary" style="margin: 0 4px; {active}">{p}</a>'
        content += '</div>'

    content += '</div>'

    return render(content, active='entities', title_suffix='Entities')


@app.get("/relationships", response_class=HTMLResponse)
async def relationships(predicate: str = Query(None)):
    kg = get_kg()
    all_rels = kg.query(predicate=predicate, limit=200)

    content = f"""
    <h1>All Relationships</h1>

    <form method="get" class="search-box">
        <select name="predicate" class="filter-select" onchange="this.form.submit()">
            <option value="">All Types</option>
            <option value="ACQUIRED" {"selected" if predicate == "ACQUIRED" else ""}>Acquisitions</option>
            <option value="FUNDED_BY" {"selected" if predicate == "FUNDED_BY" else ""}>Funding</option>
            <option value="HIRED_BY" {"selected" if predicate == "HIRED_BY" else ""}>Hires</option>
            <option value="DEPARTED_FROM" {"selected" if predicate == "DEPARTED_FROM" else ""}>Departures</option>
            <option value="CEO_OF" {"selected" if predicate == "CEO_OF" else ""}>CEO</option>
            <option value="CTO_OF" {"selected" if predicate == "CTO_OF" else ""}>CTO</option>
            <option value="FOUNDED" {"selected" if predicate == "FOUNDED" else ""}>Founded</option>
        </select>
    </form>

    <div class="card">
        <div class="card-header">
            Relationships ({len(all_rels)} total)
        </div>
        <table>
            <tr><th>Subject</th><th>Event</th><th>Object</th><th>Amount</th><th>Date</th><th>Confidence</th><th>Source</th></tr>
    """

    for rel in all_rels:
        source_link = f'<a href="{rel.source_url}" target="_blank">View</a>' if rel.source_url else '-'
        metadata = getattr(rel, 'metadata', {}) or {}
        amount_display = metadata.get('amount') or metadata.get('valuation') or (f"{metadata.get('count')} employees" if metadata.get('count') else '-')
        content += f"""
            <tr>
                <td><a href="/entity/{rel.subject.id}"><span class="tag tag-{rel.subject.entity_type}">{rel.subject.name}</span></a></td>
                <td><span class="tag tag-{rel.predicate}">{rel.predicate}</span></td>
                <td><a href="/entity/{rel.object.id}"><span class="tag tag-{rel.object.entity_type}">{rel.object.name}</span></a></td>
                <td><strong style="color: var(--success);">{amount_display}</strong></td>
                <td>{rel.event_date or '-'}</td>
                <td>{confidence_badge(rel.confidence)}</td>
                <td>{source_link}</td>
            </tr>
        """

    if not all_rels:
        content += '<tr><td colspan="7" class="empty-state">No relationships yet. <a href="/pipeline">Run the pipeline</a> to extract entities from news.</td></tr>'

    content += '</table></div>'

    return render(content, active='relationships', title_suffix='Relationships')


@app.get("/companies", response_class=HTMLResponse)
async def companies():
    """High-value companies view for recruiters - filtered for real tech companies."""
    kg = get_kg()
    import urllib.parse

    # Get filtered companies (excludes investment vehicles/SPVs)
    real_companies = get_real_tech_companies(kg)

    # Score companies based on recruiting signals
    scored_companies = []
    for company, has_news in real_companies:
        score = 0
        signals = []
        source_type = 'news' if has_news else 'sec'

        # BOOST: Companies with news coverage are more valuable
        if has_news:
            score += 20
            signals.append("In News")

        # Check for recent funding (from news: FUNDED_BY, from SEC: RAISED_FUNDING)
        funding_rels = kg.query(subject=company.name, predicate="FUNDED_BY", limit=10)
        sec_funding_rels = kg.query(subject=company.name, predicate="RAISED_FUNDING", limit=10)

        # News funding is more valuable than SEC-only
        if funding_rels:
            score += 35
            signals.append(f"Funded (News)")
        elif sec_funding_rels:
            score += 25
            signals.append(f"Funded (SEC)")

        # Check for acquisitions (company is acquiring)
        acq_rels = kg.query(subject=company.name, predicate="ACQUIRED", limit=10)
        if acq_rels:
            score += 30
            signals.append(f"Acquiring ({len(acq_rels)}x)")

        # Check for being acquired (uncertainty)
        acquired_rels = kg.query(obj=company.name, predicate="ACQUIRED", limit=10)
        if acquired_rels:
            score += 20
            signals.append("Was acquired")

        # Check for hires (active hiring)
        hire_rels = kg.query(obj=company.name, predicate="HIRED_BY", limit=20)
        if hire_rels:
            score += len(hire_rels) * 3
            signals.append(f"Hiring ({len(hire_rels)} recent)")

        # Check for departures (backfill opportunities)
        depart_rels = kg.query(obj=company.name, predicate="DEPARTED_FROM", limit=10)
        if depart_rels:
            score += len(depart_rels) * 4
            signals.append(f"Departures ({len(depart_rels)})")

        # Check for layoffs (restructuring)
        layoff_rels = kg.query(subject=company.name, predicate="LAID_OFF", limit=5)
        if layoff_rels:
            score += 15
            signals.append("Layoffs")

        # Only include companies with some signal
        if score > 0:
            scored_companies.append({
                'company': company,
                'score': score,
                'signals': signals,
                'source_type': source_type
            })

    # Sort by score
    scored_companies.sort(key=lambda x: x['score'], reverse=True)

    # Stats for header
    total_companies = len(kg.search_entities('', entity_type='company'))
    filtered_count = len(real_companies)
    with_signals = len(scored_companies)

    content = f"""
    <h1>High-Value Companies</h1>
    <p style="color: var(--gray-500); margin-bottom: 12px;">
        Companies ranked by recruiting opportunity signals: funding, acquisitions, hiring activity, departures.
    </p>
    <p style="color: var(--gray-400); font-size: 0.9em; margin-bottom: 24px;">
        Showing {with_signals} companies with signals (filtered from {filtered_count} tech companies, {total_companies - filtered_count} investment vehicles excluded)
    </p>

    <div class="card">
        <div class="card-header">
            Companies by Recruiting Value
        </div>
        <table>
            <tr><th>Score</th><th>Company</th><th>Source</th><th>Signals</th><th>Actions</th></tr>
    """

    for item in scored_companies[:50]:
        signals_html = ' '.join([f'<span class="tag tag-hiring">{s}</span>' for s in item['signals']])
        enriched = kg.get_enrichment(item['company'].id)
        enr_dot = '<span style="color: var(--success);">●</span>' if enriched else '<span style="color: var(--gray-300);">○</span>'
        company_name_encoded = urllib.parse.quote(item['company'].name, safe='')
        linkedin_slug = item['company'].name.lower().replace(' ', '-')
        source_badge = '<span class="tag tag-funded">News</span>' if item['source_type'] == 'news' else '<span class="tag" style="background: var(--gray-200);">SEC</span>'
        content += f"""
            <tr>
                <td><strong style="color: var(--primary);">{item['score']}</strong></td>
                <td>{enr_dot} <a href="/entity/{item['company'].id}">{item['company'].name}</a></td>
                <td>{source_badge}</td>
                <td>{signals_html}</td>
                <td>
                    <a href="/search?q={company_name_encoded}" class="btn btn-sm btn-secondary">Events</a>
                    <a href="https://linkedin.com/company/{linkedin_slug}" target="_blank" class="btn btn-sm btn-secondary">LinkedIn</a>
                </td>
            </tr>
        """

    if not scored_companies:
        content += '<tr><td colspan="5" class="empty-state">No companies with signals yet. <a href="/pipeline">Run the pipeline</a> to extract entities from news.</td></tr>'

    content += '</table></div>'

    return render(content, active='companies', title_suffix='High-Value Companies')


@app.get("/candidates", response_class=HTMLResponse)
async def candidates():
    """High-value candidates view - founders, executives, and underdogs at startups."""
    kg = get_kg()
    import urllib.parse

    # Get all people
    all_people = kg.search_entities('', entity_type='person')

    # Filter and score candidates
    scored_candidates = []
    for person in all_people:
        # Skip obviously bad names
        if not person.name or len(person.name) < 3:
            continue

        # Skip AI model names and obviously wrong extractions
        bad_patterns = ['gpt-', 'claude ', 'gemini ', 'llama ', 'mistral ',
                       'donald trump', 'joe biden', 'barack obama', 'elon musk',  # Celebrities often misextracted
                       'venezuela', 'colombia', 'ministry']  # Country/govt misextractions
        if any(x in person.name.lower() for x in bad_patterns):
            continue

        score = 0
        signals = []
        current_company = None
        role = None
        is_from_news = False

        # Check all relationships for this person
        all_rels = kg.query(subject=person.name, limit=30)

        for rel in all_rels:
            obj_name = rel.object.name if hasattr(rel.object, 'name') else str(rel.object)
            context = getattr(rel, 'context', '') or ''

            # Skip if company is an investment vehicle
            if is_investment_vehicle(obj_name):
                continue

            if rel.predicate == 'FOUNDED':
                score += 50  # Founders are highest value
                signals.append("Founder")
                current_company = obj_name
                role = "Founder"
                if 'Form D' not in context:
                    is_from_news = True

            elif rel.predicate == 'CEO_OF':
                score += 45
                signals.append("CEO")
                current_company = obj_name
                role = "CEO"
                if 'Form D' not in context:
                    is_from_news = True

            elif rel.predicate in ['CTO_OF', 'CFO_OF']:
                score += 40
                signals.append(rel.predicate.replace('_OF', ''))
                current_company = obj_name
                role = rel.predicate.replace('_OF', '')
                if 'Form D' not in context:
                    is_from_news = True

            elif rel.predicate == 'OFFICER_OF':
                if score < 25:  # Don't overwrite better roles
                    score += 25
                    signals.append("Officer/Director")
                    current_company = obj_name
                    role = "Officer"

            elif rel.predicate == 'EXECUTIVE_OF':
                if score < 35:
                    score += 35
                    signals.append("Executive")
                    current_company = obj_name
                    role = "Executive"

            elif rel.predicate == 'DIRECTOR_OF':
                if score < 20:
                    score += 20
                    signals.append("Director")
                    current_company = obj_name
                    role = "Director"

            elif rel.predicate == 'DEPARTED_FROM':
                score += 30  # Availability signal
                signals.append(f"Left {obj_name}")

            elif rel.predicate == 'HIRED_BY':
                if not current_company:
                    current_company = obj_name
                score += 10
                signals.append("Recently hired")

        # Boost for news coverage (more visible/notable people)
        if is_from_news:
            score += 15
            if "In News" not in signals:
                signals.append("In News")

        # Only include if they have some signal
        if score > 0 and current_company:
            scored_candidates.append({
                'person': person,
                'score': score,
                'signals': list(set(signals)),  # Dedupe
                'current_company': current_company,
                'role': role,
                'is_from_news': is_from_news
            })

    # Sort by score
    scored_candidates.sort(key=lambda x: x['score'], reverse=True)

    # Stats
    total_people = len(all_people)
    with_signals = len(scored_candidates)

    content = f"""
    <h1>Founders & Executives</h1>
    <p style="color: var(--gray-500); margin-bottom: 12px;">
        People at startups ranked by role and availability signals. Includes founders, executives, and officers.
    </p>
    <p style="color: var(--gray-400); font-size: 0.9em; margin-bottom: 24px;">
        Showing {with_signals} people with company relationships (from {total_people} total)
    </p>

    <div class="card">
        <div class="card-header">
            Startup Leaders by Value
        </div>
        <table>
            <tr><th>Score</th><th>Name</th><th>Role</th><th>Company</th><th>Source</th><th>Signals</th><th>Actions</th></tr>
    """

    for item in scored_candidates[:100]:  # Show more candidates
        signals_html = ' '.join([f'<span class="tag tag-hot">{s}</span>' for s in item['signals'][:3]])
        company_html = item['current_company'] or '-'
        role_html = item['role'] or '-'
        source_badge = '<span class="tag tag-funded">News</span>' if item['is_from_news'] else '<span class="tag" style="background: var(--gray-200);">SEC</span>'

        linkedin_url = f"https://www.linkedin.com/search/results/all/?keywords={urllib.parse.quote(item['person'].name)}"

        content += f"""
            <tr>
                <td><strong style="color: var(--primary);">{item['score']}</strong></td>
                <td><a href="/entity/{item['person'].id}">{item['person'].name}</a></td>
                <td>{role_html}</td>
                <td>{company_html}</td>
                <td>{source_badge}</td>
                <td>{signals_html}</td>
                <td><a href="{linkedin_url}" target="_blank" class="btn btn-sm btn-secondary">LinkedIn</a></td>
            </tr>
        """

    if not scored_candidates:
        content += '<tr><td colspan="7" class="empty-state">No candidates yet. <a href="/pipeline">Run the pipeline</a> to extract entities.</td></tr>'

    content += '</table></div>'

    return render(content, active='candidates', title_suffix='Founders & Executives')


@app.get("/entity/{entity_id}", response_class=HTMLResponse)
async def entity_detail(entity_id: int):
    """Entity detail page with all relationships, tags, and enrichment."""
    kg = get_kg()

    # Use new get_entity_by_id method
    entity = kg.get_entity_by_id(entity_id)

    if not entity:
        return render('<h1>Entity Not Found</h1>', title_suffix='Not Found')

    # Get relationships where entity is subject or object
    subject_rels = kg.query(subject=entity.name, limit=100)
    object_rels = kg.query(obj=entity.name, limit=100)

    # Get tags and enrichment
    tags = kg.get_entity_tags(entity_id)
    enrichment = kg.get_enrichment(entity_id)

    # Build breadcrumb
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
    <h1>
        <span class="tag tag-{entity.entity_type}">{entity.entity_type}</span>
        {entity.name}
    </h1>

    <!-- Tags section -->
    <div style="margin-bottom: 20px;">
        <strong>Tags:</strong>
    """

    if tags:
        for tag in tags:
            content += f'<span class="tag tag-hot" style="margin-left: 8px;">{tag}</span>'
    else:
        content += '<span style="color: var(--gray-500); margin-left: 8px;">No tags</span>'

    # Add tag form
    content += f"""
        <form method="post" action="/entity/{entity_id}/tag" style="display: inline; margin-left: 16px;">
            <input type="text" name="tag" placeholder="Add tag..." style="padding: 4px 8px; border: 1px solid var(--gray-300); border-radius: 4px; font-size: 0.85em;">
            <button type="submit" class="btn btn-sm btn-secondary">Add</button>
        </form>
    </div>

    <div class="stats">
        <div class="stat-card">
            <div class="stat-value">{entity.mention_count}</div>
            <div class="stat-label">Mentions</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{len(subject_rels) + len(object_rels)}</div>
            <div class="stat-label">Relationships</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{entity.first_seen or '-'}</div>
            <div class="stat-label">First Seen</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{entity.last_seen or '-'}</div>
            <div class="stat-label">Last Seen</div>
        </div>
    </div>
    """

    # Show enrichment data if available - organized into sections
    if enrichment:
        # Combine all enrichment data
        all_data = {}
        enrichment_source = "unknown"
        enriched_at = "Unknown"
        for source, source_data in enrichment.items():
            enrichment_source = source
            enriched_at = source_data.get("enriched_at", "Unknown")
            all_data.update(source_data.get("data", {}))

        # Company-specific enrichment display
        if entity.entity_type == "company" and all_data:
            # Key metrics cards
            content += '<div class="stats">'
            if all_data.get("employee_count"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value">{all_data["employee_count"]:,}</div>
                    <div class="stat-label">Employees</div>
                </div>'''
            elif all_data.get("employee_range"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value">{all_data["employee_range"]}</div>
                    <div class="stat-label">Employee Range</div>
                </div>'''
            if all_data.get("total_funding"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value" style="color: var(--success);">{all_data["total_funding"]}</div>
                    <div class="stat-label">Total Funding</div>
                </div>'''
            if all_data.get("founded_year"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value">{all_data["founded_year"]}</div>
                    <div class="stat-label">Founded</div>
                </div>'''
            if all_data.get("funding_rounds"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value">{all_data["funding_rounds"]}</div>
                    <div class="stat-label">Funding Rounds</div>
                </div>'''
            content += '</div>'

            # Company details card
            content += f'<div class="card"><div class="card-header">Company Details <span style="font-size: 0.75em; color: var(--gray-500); margin-left: 8px;">Source: {enrichment_source} | {enriched_at}</span></div><table>'
            detail_fields = [
                ("description", "Description"),
                ("industry", "Industry"),
                ("company_type", "Type"),
                ("headquarters", "Headquarters"),
                ("last_funding_type", "Latest Round"),
                ("investors", "Investors"),
            ]
            for key, label in detail_fields:
                value = all_data.get(key)
                if value and value != "None" and value != 0:
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    content += f'<tr><td style="width: 150px; color: var(--gray-500); font-weight: 500;">{label}</td><td>{value}</td></tr>'
            content += '</table></div>'

            # Links card
            if any(all_data.get(k) for k in ["website_url", "linkedin_url", "crunchbase_url"]):
                content += '<div class="card"><div class="card-header">Links</div><div style="padding: 16px;">'
                if all_data.get("website_url"):
                    content += f'<a href="{all_data["website_url"]}" target="_blank" class="btn btn-secondary" style="margin-right: 8px;">Website</a>'
                if all_data.get("linkedin_url"):
                    content += f'<a href="{all_data["linkedin_url"]}" target="_blank" class="btn btn-secondary" style="margin-right: 8px;">LinkedIn</a>'
                if all_data.get("crunchbase_url"):
                    content += f'<a href="{all_data["crunchbase_url"]}" target="_blank" class="btn btn-secondary" style="margin-right: 8px;">Crunchbase</a>'
                content += '</div></div>'

        # Person-specific enrichment display
        elif entity.entity_type == "person" and all_data:
            # Key info cards
            content += '<div class="stats">'
            if all_data.get("current_title"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value" style="font-size: 1.5em;">{all_data["current_title"]}</div>
                    <div class="stat-label">Current Title</div>
                </div>'''
            if all_data.get("current_company"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value" style="font-size: 1.5em;">{all_data["current_company"]}</div>
                    <div class="stat-label">Current Company</div>
                </div>'''
            if all_data.get("executive_level"):
                content += f'''
                <div class="stat-card">
                    <div class="stat-value" style="color: var(--purple);">{all_data["executive_level"]}</div>
                    <div class="stat-label">Level</div>
                </div>'''
            content += '</div>'

            # Person details card
            content += f'<div class="card"><div class="card-header">Professional Details <span style="font-size: 0.75em; color: var(--gray-500); margin-left: 8px;">Source: {enrichment_source} | {enriched_at}</span></div><table>'
            detail_fields = [
                ("location", "Location"),
                ("previous_companies", "Previous Companies"),
                ("education", "Education"),
                ("skills", "Skills/Expertise"),
            ]
            for key, label in detail_fields:
                value = all_data.get(key)
                if value and value != "None":
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    content += f'<tr><td style="width: 180px; color: var(--gray-500); font-weight: 500;">{label}</td><td>{value}</td></tr>'
            content += '</table></div>'

            # LinkedIn link
            if all_data.get("linkedin_url"):
                content += f'''<div class="card"><div class="card-header">Links</div>
                <div style="padding: 16px;">
                    <a href="{all_data["linkedin_url"]}" target="_blank" class="btn btn-secondary">LinkedIn Profile</a>
                </div></div>'''

        # Fallback: show raw data for other entity types
        elif all_data:
            content += f'''
            <div class="card">
                <div class="card-header">Enrichment Data <span style="color: var(--gray-500); font-weight: normal; font-size: 0.85em;">(source: {enrichment_source})</span></div>
                <table>
            '''
            for key, value in all_data.items():
                if value and value != "None" and value != 0:
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    content += f'<tr><td style="width: 200px; color: var(--gray-500);">{key}</td><td>{value}</td></tr>'
            content += '</table></div>'

    # Quick links for external research
    clean_name = entity.name.replace(" ", "+")
    content += f"""
    <div class="card">
        <div class="card-header">External Research</div>
        <div style="padding: 16px;">
            <a href="https://www.linkedin.com/search/results/all/?keywords={clean_name}" target="_blank" class="btn btn-secondary" style="margin-right: 8px;">LinkedIn Search</a>
            <a href="https://www.google.com/search?q={clean_name}" target="_blank" class="btn btn-secondary" style="margin-right: 8px;">Google Search</a>
            <a href="https://www.crunchbase.com/textsearch?q={clean_name}" target="_blank" class="btn btn-secondary" style="margin-right: 8px;">Crunchbase</a>
            <a href="/entity/{entity_id}/enrich" class="btn btn-primary">Enrich Data</a>
        </div>
    </div>
    """

    # Collect source URLs from relationships for News Sources card
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

    # Mini Event Timeline
    all_events = []
    for rel in subject_rels:
        all_events.append({'type': rel.predicate, 'other': rel.object.name, 'date': rel.event_date, 'url': rel.source_url})
    for rel in object_rels:
        all_events.append({'type': rel.predicate, 'other': rel.subject.name, 'date': rel.event_date, 'url': rel.source_url})

    # Sort events by date, handling both datetime.date and string types
    def sort_key(x):
        d = x['date']
        if d is None:
            return ''
        if hasattr(d, 'isoformat'):
            return d.isoformat()
        return str(d)
    all_events.sort(key=sort_key, reverse=True)

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

    if subject_rels:
        content += """
        <div class="card">
            <div class="card-header">As Subject</div>
            <table>
                <tr><th>Event</th><th>Related Entity</th><th>Amount</th><th>Date</th><th>Confidence</th><th>Context</th><th>Source</th></tr>
        """
        for rel in subject_rels:
            metadata = getattr(rel, 'metadata', {}) or {}
            amount = metadata.get('amount') or metadata.get('valuation') or '-'
            context = (rel.context[:80] + '...') if rel.context and len(rel.context) > 80 else (rel.context or '-')
            source_link = f'<a href="{rel.source_url}" target="_blank">View</a>' if rel.source_url else '-'
            content += f"""
                <tr>
                    <td><span class="tag tag-{rel.predicate}">{rel.predicate}</span></td>
                    <td><a href="/entity/{rel.object.id}"><span class="tag tag-{rel.object.entity_type}">{rel.object.name}</span></a></td>
                    <td><strong style="color: var(--success);">{amount}</strong></td>
                    <td>{rel.event_date or '-'}</td>
                    <td>{confidence_badge(rel.confidence)}</td>
                    <td style="font-size: 0.85em; color: var(--gray-500);">{context}</td>
                    <td>{source_link}</td>
                </tr>
            """
        content += '</table></div>'

    if object_rels:
        content += """
        <div class="card">
            <div class="card-header">As Object</div>
            <table>
                <tr><th>Related Entity</th><th>Event</th><th>Amount</th><th>Date</th><th>Confidence</th><th>Context</th><th>Source</th></tr>
        """
        for rel in object_rels:
            metadata = getattr(rel, 'metadata', {}) or {}
            amount = metadata.get('amount') or metadata.get('valuation') or '-'
            context = (rel.context[:80] + '...') if rel.context and len(rel.context) > 80 else (rel.context or '-')
            source_link = f'<a href="{rel.source_url}" target="_blank">View</a>' if rel.source_url else '-'
            content += f"""
                <tr>
                    <td><a href="/entity/{rel.subject.id}"><span class="tag tag-{rel.subject.entity_type}">{rel.subject.name}</span></a></td>
                    <td><span class="tag tag-{rel.predicate}">{rel.predicate}</span></td>
                    <td><strong style="color: var(--success);">{amount}</strong></td>
                    <td>{rel.event_date or '-'}</td>
                    <td>{confidence_badge(rel.confidence)}</td>
                    <td style="font-size: 0.85em; color: var(--gray-500);">{context}</td>
                    <td>{source_link}</td>
                </tr>
            """
        content += '</table></div>'

    return render(content, title_suffix=entity.name)


@app.post("/entity/{entity_id}/tag")
async def add_entity_tag(entity_id: int, tag: str = Form(...)):
    """Add a tag to an entity."""
    kg = get_kg()
    if tag and tag.strip():
        kg.add_tag(entity_id, tag.strip())
    return RedirectResponse(url=f"/entity/{entity_id}", status_code=303)


@app.get("/entity/{entity_id}/enrich", response_class=HTMLResponse)
async def enrich_entity(entity_id: int):
    """Trigger enrichment for an entity."""
    from src.enrichment.enrichment_service import EnrichmentService

    kg = get_kg()
    entity = kg.get_entity_by_id(entity_id)

    if not entity:
        return render('<h1>Entity Not Found</h1>', title_suffix='Not Found')

    service = EnrichmentService(kg)

    if entity.entity_type == "company":
        result = await service.enrich_company(entity_id)
    elif entity.entity_type == "person":
        result = await service.enrich_person(entity_id)
    else:
        result = None

    return RedirectResponse(url=f"/entity/{entity_id}", status_code=303)


@app.get("/tags", response_class=HTMLResponse)
async def all_tags():
    """View all tags."""
    kg = get_kg()
    tags = kg.get_all_tags()

    content = """
    <h1>All Tags</h1>
    <div class="card">
        <div class="card-header">Tags by Usage</div>
        <table>
            <tr><th>Tag</th><th>Count</th><th>Entities</th></tr>
    """

    for tag_info in tags:
        tag = tag_info["tag"]
        count = tag_info["count"]
        content += f"""
            <tr>
                <td><span class="tag tag-hot">{tag}</span></td>
                <td>{count}</td>
                <td><a href="/tags/{tag}">View entities</a></td>
            </tr>
        """

    if not tags:
        content += '<tr><td colspan="3" class="empty-state">No tags yet</td></tr>'

    content += '</table></div>'

    return render(content, title_suffix='Tags')


@app.get("/tags/{tag}", response_class=HTMLResponse)
async def entities_by_tag(tag: str):
    """View entities with a specific tag."""
    kg = get_kg()
    entities = kg.get_entities_by_tag(tag)

    content = f"""
    <h1>Entities tagged: <span class="tag tag-hot">{tag}</span></h1>
    <div class="card">
        <div class="card-header">Entities ({len(entities)} total)</div>
        <table>
            <tr><th>Name</th><th>Type</th><th>Mentions</th></tr>
    """

    for entity in entities:
        content += f"""
            <tr>
                <td><a href="/entity/{entity.id}">{entity.name}</a></td>
                <td><span class="tag tag-{entity.entity_type}">{entity.entity_type}</span></td>
                <td>{entity.mention_count}</td>
            </tr>
        """

    if not entities:
        content += '<tr><td colspan="3" class="empty-state">No entities with this tag</td></tr>'

    content += '</table></div>'

    return render(content, title_suffix=f'Tag: {tag}')


@app.get("/newsletter", response_class=HTMLResponse)
async def newsletter(
    period: str = Query("weekly", description="weekly or daily"),
    format: str = Query("html", description="html or embed")
):
    """Generate recruiter intelligence newsletter."""
    kg = get_kg()
    generator = NewsletterGenerator(kg)

    if period == "daily":
        nl = generator.generate_daily()
    else:
        nl = generator.generate_weekly()

    # Format options dropdown
    format_options = f"""
        <option value="html" {"selected" if format == "html" else ""}>Embedded</option>
        <option value="standalone" {"selected" if format == "standalone" else ""}>Standalone HTML</option>
        <option value="markdown" {"selected" if format == "markdown" else ""}>Markdown</option>
    """

    period_options = f"""
        <option value="weekly" {"selected" if period == "weekly" else ""}>Weekly Digest</option>
        <option value="daily" {"selected" if period == "daily" else ""}>Daily Update</option>
    """

    # If standalone HTML requested, return raw HTML
    if format == "standalone":
        return HTMLResponse(generator.to_html(nl))

    # If markdown requested, show in code block
    if format == "markdown":
        md_content = generator.to_markdown(nl)
        content = f"""
        <h1>Newsletter - {period.title()} Digest</h1>

        <div class="card" style="margin-bottom: 24px;">
            <form method="get" action="/newsletter" style="display: flex; gap: 16px; align-items: center;">
                <select name="period" class="filter-select" onchange="this.form.submit()">
                    {period_options}
                </select>
                <select name="format" class="filter-select" onchange="this.form.submit()">
                    {format_options}
                </select>
                <a href="/newsletter?period={period}&format=standalone" target="_blank" class="btn btn-primary">
                    Open in New Tab
                </a>
            </form>
        </div>

        <div class="card">
            <div class="card-header">Markdown Output</div>
            <pre style="background: #1f2937; color: #f3f4f6; padding: 20px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap;">{md_content}</pre>
        </div>
        """
        return render(content, active='newsletter', title_suffix='Newsletter')

    # Embedded view - minimal design
    # Clean section titles
    section_titles = {
        "Companies That Raised Funding": "Funding Rounds",
        "Acquisitions & Mergers": "M&A Activity",
        "Layoffs (Displaced Talent)": "Layoffs",
        "Executive Movements": "Executive Moves",
        "Hot Candidates": "Available Talent",
    }

    sections_html = ""
    for section in nl.sections:
        # Clean title (remove emojis)
        title = section.title
        for old, new in section_titles.items():
            if old in title:
                title = new
                break
        title = ''.join(c for c in title if ord(c) < 128 or c.isalnum())

        sections_html += f"""
        <div style="margin-bottom: 32px;">
            <h3 style="font-size: 0.8em; font-weight: 600; color: var(--gray-500); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--gray-200);">{title}</h3>
        """

        for item in section.items[:12]:
            sections_html += '<div style="display: flex; justify-content: space-between; align-items: baseline; padding: 10px 0; border-bottom: 1px solid var(--gray-100);">'
            sections_html += '<div style="flex: 1;">'

            if 'company' in item and 'amount' in item:
                sections_html += f'<a href="/search?q={item["company"]}" style="font-weight: 500; color: var(--gray-900); text-decoration: none;">{item["company"]}</a>'
                if item.get('amount'):
                    sections_html += f' <span style="color: var(--gray-500);">raised</span> <span style="font-weight: 500;">{item["amount"]}</span>'
                tag_style = "background: #e8f5e9; color: #2e7d32;" if item.get('source') == 'SEC' else "background: #e3f2fd; color: #1565c0;"
                sections_html += f'</div><span style="font-size: 0.7em; font-weight: 500; padding: 3px 8px; border-radius: 3px; text-transform: uppercase; {tag_style}">{item.get("source", "News")}</span>'

            elif 'acquirer' in item:
                sections_html += f'<a href="/search?q={item["acquirer"]}" style="font-weight: 500; color: var(--gray-900); text-decoration: none;">{item["acquirer"]}</a>'
                sections_html += f' <span style="color: var(--gray-500);">acquired</span> '
                sections_html += f'<a href="/search?q={item["target"]}" style="font-weight: 500; color: var(--gray-900); text-decoration: none;">{item["target"]}</a>'
                sections_html += '</div><span style="font-size: 0.7em; font-weight: 500; padding: 3px 8px; border-radius: 3px; text-transform: uppercase; background: #f3e5f5; color: #7b1fa2;">M&A</span>'

            elif 'employees' in item:
                sections_html += f'<a href="/search?q={item["company"]}" style="font-weight: 500; color: var(--gray-900); text-decoration: none;">{item["company"]}</a>'
                if item.get('employees'):
                    sections_html += f' <span style="color: var(--gray-500);">{item["employees"]:,} employees</span>'
                sections_html += '</div><span style="font-size: 0.7em; font-weight: 500; padding: 3px 8px; border-radius: 3px; text-transform: uppercase; background: #ffebee; color: #c62828;">Layoff</span>'

            elif 'person' in item and 'action' in item:
                sections_html += f'<a href="/search?q={item["person"]}" style="font-weight: 500; color: var(--gray-900); text-decoration: none;">{item["person"]}</a>'
                action = "joined" if item["action"] == "joined" else "left"
                sections_html += f' <span style="color: var(--gray-500);">{action}</span> '
                sections_html += f'<a href="/search?q={item["company"]}" style="font-weight: 500; color: var(--gray-900); text-decoration: none;">{item["company"]}</a>'
                tag_style = "background: #fff3e0; color: #e65100;" if item.get("signal") == "Hired" else "background: #e8f5e9; color: #2e7d32;"
                sections_html += f'</div><span style="font-size: 0.7em; font-weight: 500; padding: 3px 8px; border-radius: 3px; text-transform: uppercase; {tag_style}">{item.get("signal", "Move")}</span>'

            elif 'name' in item and 'previous_company' in item:
                sections_html += f'<a href="/search?q={item["name"]}" style="font-weight: 500; color: var(--gray-900); text-decoration: none;">{item["name"]}</a>'
                if item.get('title'):
                    sections_html += f' <span style="color: var(--gray-500);">({item["title"]})</span>'
                sections_html += f' <span style="color: var(--gray-500);">from</span> '
                sections_html += f'<a href="/search?q={item["previous_company"]}" style="color: var(--gray-700); text-decoration: none;">{item["previous_company"]}</a>'
                sections_html += '</div><span style="font-size: 0.7em; font-weight: 500; padding: 3px 8px; border-radius: 3px; text-transform: uppercase; background: #e8f5e9; color: #2e7d32;">Available</span>'

            else:
                sections_html += '</div>'

            sections_html += '</div>'

        if not section.items:
            sections_html += '<p style="color: var(--gray-500); padding: 20px; text-align: center;">No events</p>'

        sections_html += '</div>'

    # Minimal stats
    stats = nl.stats
    stats_html = f"""
    <div style="display: flex; gap: 32px; padding: 20px 0; border-top: 1px solid var(--gray-200); margin-top: 24px;">
        <div>
            <div style="font-size: 1.5em; font-weight: 600; color: var(--gray-900);">{stats.get('total_entities', 0):,}</div>
            <div style="font-size: 0.8em; color: var(--gray-500);">Entities</div>
        </div>
        <div>
            <div style="font-size: 1.5em; font-weight: 600; color: var(--gray-900);">{stats.get('total_relationships', 0):,}</div>
            <div style="font-size: 0.8em; color: var(--gray-500);">Relationships</div>
        </div>
    </div>
    """

    content = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
        <h1 style="margin: 0;">Newsletter</h1>
        <a href="/newsletter?period={period}&format=standalone" target="_blank" style="font-size: 0.85em; color: var(--primary); text-decoration: none;">Open standalone &rarr;</a>
    </div>
    <p style="color: var(--gray-500); margin-bottom: 24px; font-size: 0.95em;">{nl.summary}</p>

    <div style="display: flex; gap: 12px; margin-bottom: 32px;">
        <form method="get" action="/newsletter" style="display: flex; gap: 12px;">
            <select name="period" class="filter-select" onchange="this.form.submit()" style="padding: 6px 12px; border: 1px solid var(--gray-200); border-radius: 4px; font-size: 0.85em;">
                {period_options}
            </select>
            <select name="format" class="filter-select" onchange="this.form.submit()" style="padding: 6px 12px; border: 1px solid var(--gray-200); border-radius: 4px; font-size: 0.85em;">
                {format_options}
            </select>
        </form>
    </div>

    {sections_html}
    {stats_html}

    <div style="padding-top: 16px; color: var(--gray-400); font-size: 0.8em;">
        Data from SEC EDGAR, news feeds, Layoffs.fyi, Y Combinator. Generated {nl.date.strftime('%b %d, %Y')}.
    </div>
    """

    return render(content, active='newsletter', title_suffix='Newsletter')


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    port = int(os.environ.get("PORT", 8000))

    print("\n" + "="*60)
    print("RECRUITER INTELLIGENCE")
    print("="*60)
    print("Starting web server...")
    print(f"Open http://localhost:{port} in your browser")
    print("Press Ctrl+C to stop")
    print("="*60 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=port)
