"""Newsletter generator for recruiter intelligence digest."""

from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass, field

import structlog

from ..knowledge_graph.graph import KnowledgeGraph

logger = structlog.get_logger()


@dataclass
class NewsletterSection:
    """A section of the newsletter."""
    title: str
    items: List[dict]
    icon: str = ""


@dataclass
class Newsletter:
    """Generated newsletter content."""
    title: str
    date: datetime
    summary: str
    sections: List[NewsletterSection]
    stats: dict = field(default_factory=dict)


class NewsletterGenerator:
    """Generate weekly/daily recruiter intelligence newsletters."""

    def __init__(self, kg: KnowledgeGraph = None):
        self.kg = kg or KnowledgeGraph()

    def generate_weekly(self, weeks_back: int = 1) -> Newsletter:
        """Generate weekly newsletter digest."""
        end_date = datetime.now()
        start_date = end_date - timedelta(weeks=weeks_back)

        sections = []

        # Section 1: Funding Rounds (Hot Companies)
        funding = self._get_funding_events(start_date, end_date)
        if funding:
            sections.append(NewsletterSection(
                title="🚀 Companies That Raised Funding",
                icon="💰",
                items=funding
            ))

        # Section 2: Acquisitions (Talent Movement)
        acquisitions = self._get_acquisitions(start_date, end_date)
        if acquisitions:
            sections.append(NewsletterSection(
                title="🤝 Acquisitions & Mergers",
                icon="🏢",
                items=acquisitions
            ))

        # Section 3: Layoffs (Available Talent)
        layoffs = self._get_layoffs(start_date, end_date)
        if layoffs:
            sections.append(NewsletterSection(
                title="📉 Layoffs (Displaced Talent)",
                icon="👥",
                items=layoffs
            ))

        # Section 4: Executive Moves
        exec_moves = self._get_executive_moves(start_date, end_date)
        if exec_moves:
            sections.append(NewsletterSection(
                title="👔 Executive Movements",
                icon="📊",
                items=exec_moves
            ))

        # Section 5: Hot Candidates
        candidates = self._get_hot_candidates(start_date, end_date)
        if candidates:
            sections.append(NewsletterSection(
                title="⭐ Hot Candidates",
                icon="🎯",
                items=candidates
            ))

        # Get stats
        stats = self.kg.get_stats()

        return Newsletter(
            title=f"Recruiter Intelligence Weekly - {end_date.strftime('%B %d, %Y')}",
            date=end_date,
            summary=self._generate_summary(sections, stats),
            sections=sections,
            stats=stats,
        )

    def generate_daily(self) -> Newsletter:
        """Generate daily newsletter digest."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)  # Look back 7 days for daily digest

        sections = []

        # Today's Funding
        funding = self._get_funding_events(start_date, end_date)
        if funding:
            sections.append(NewsletterSection(
                title="Funding Rounds",
                icon="💰",
                items=funding[:15]  # Top 15
            ))

        # Acquisitions
        acquisitions = self._get_acquisitions(start_date, end_date)
        if acquisitions:
            sections.append(NewsletterSection(
                title="M&A Activity",
                icon="🤝",
                items=acquisitions[:10]
            ))

        # Layoffs - important for recruiters
        layoffs = self._get_layoffs(start_date, end_date)
        if layoffs:
            sections.append(NewsletterSection(
                title="Layoffs (Displaced Talent)",
                icon="📉",
                items=layoffs
            ))

        # Executive Moves
        exec_moves = self._get_executive_moves(start_date, end_date)
        if exec_moves:
            sections.append(NewsletterSection(
                title="Executive Moves",
                icon="👔",
                items=exec_moves[:10]
            ))

        # Hot Candidates
        candidates = self._get_hot_candidates(start_date, end_date)
        if candidates:
            sections.append(NewsletterSection(
                title="Available Talent",
                icon="⭐",
                items=candidates[:10]
            ))

        stats = self.kg.get_stats()

        return Newsletter(
            title=f"Recruiter Intelligence Daily - {end_date.strftime('%B %d, %Y')}",
            date=end_date,
            summary=self._generate_summary(sections, stats),
            sections=sections,
            stats=stats,
        )

    def _sanitize_name(self, name: str) -> str:
        """Sanitize entity name by removing HTML and invalid patterns."""
        import re
        if not name:
            return ""
        # Remove HTML tags and attributes
        name = re.sub(r'<[^>]+>', '', name)
        name = re.sub(r'target="_blank">', '', name)
        name = re.sub(r'href="[^"]*"', '', name)
        # Remove URL patterns
        name = re.sub(r'https?://[^\s]+', '', name)
        # Clean up whitespace
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _is_valid_company(self, name: str) -> bool:
        """Check if company name is valid for newsletter."""
        if not name or len(name) < 2:
            return False
        # Skip names that are clearly not companies
        invalid_patterns = [
            'target="_blank"',
            'href=',
            'http://',
            'https://',
            'Investing.com',
            'Reuters -',
            'Google News',
        ]
        name_lower = name.lower()
        for pattern in invalid_patterns:
            if pattern.lower() in name_lower:
                return False
        return True

    def _get_funding_events(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 20
    ) -> List[dict]:
        """Get recent funding events."""
        items = []

        # Get from news (FUNDED_BY)
        news_funding = self.kg.query(predicate="FUNDED_BY", limit=limit * 2)
        # Get from SEC (RAISED_FUNDING)
        sec_funding = self.kg.query(predicate="RAISED_FUNDING", limit=limit * 2)

        all_funding = news_funding + sec_funding

        seen_companies = set()
        for rel in all_funding:
            company_name = rel.subject.name if hasattr(rel.subject, 'name') else str(rel.subject)
            company_name = self._sanitize_name(company_name)

            if not self._is_valid_company(company_name):
                continue

            if company_name in seen_companies:
                continue

            # Filter by date if available
            if rel.event_date:
                event_dt = datetime.combine(rel.event_date, datetime.min.time())
                if not (start_date <= event_dt <= end_date):
                    continue

            seen_companies.add(company_name)
            context = getattr(rel, 'context', '') or ''

            # Try to extract amount from context
            amount = self._extract_amount(context)

            items.append({
                'company': company_name,
                'investor': rel.object.name if hasattr(rel.object, 'name') else str(rel.object),
                'amount': amount,
                'context': context,
                'date': str(rel.event_date) if rel.event_date else '',
                'source': 'SEC' if 'Form D' in context else 'News',
                'confidence': getattr(rel, 'confidence', 0.8),
            })

            if len(items) >= limit:
                break

        return sorted(items, key=lambda x: x.get('date', ''), reverse=True)

    def _normalize_company_name(self, name: str) -> str:
        """Normalize company name for deduplication."""
        import re
        name = name.lower().strip()
        # Remove common suffixes
        name = re.sub(r'\s*(inc\.?|llc|ltd\.?|corp\.?|co\.?|global|technologies|technology)$', '', name, flags=re.I)
        # Remove extra whitespace
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _get_acquisitions(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 15
    ) -> List[dict]:
        """Get recent acquisition events (deduplicated)."""
        items = []
        seen = set()  # Track acquirer-target pairs

        acquisitions = self.kg.query(predicate="ACQUIRED", limit=limit * 3)

        for rel in acquisitions:
            acquirer = rel.subject.name if hasattr(rel.subject, 'name') else str(rel.subject)
            target = rel.object.name if hasattr(rel.object, 'name') else str(rel.object)

            # Normalize for deduplication (handles variations like "Mobileye" vs "Mobileye Global")
            key = (self._normalize_company_name(acquirer), self._normalize_company_name(target))
            if key in seen:
                continue
            seen.add(key)

            items.append({
                'acquirer': acquirer,
                'target': target,
                'context': getattr(rel, 'context', ''),
                'date': str(rel.event_date) if rel.event_date else '',
                'confidence': getattr(rel, 'confidence', 0.8),
            })

            if len(items) >= limit:
                break

        return items

    def _get_layoffs(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 15
    ) -> List[dict]:
        """Get recent layoff events (deduplicated)."""
        items = []
        seen_companies = set()

        layoffs = self.kg.query(predicate="LAID_OFF", limit=limit * 3)

        for rel in layoffs:
            company = rel.subject.name if hasattr(rel.subject, 'name') else str(rel.subject)

            # Deduplicate by company
            key = company.lower().strip()
            if key in seen_companies:
                continue
            seen_companies.add(key)

            context = getattr(rel, 'context', '')
            count = self._extract_layoff_count(context)

            items.append({
                'company': company,
                'employees': count,
                'context': context,
                'date': str(rel.event_date) if rel.event_date else '',
            })

            if len(items) >= limit:
                break

        return items

    def _get_executive_moves(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 20
    ) -> List[dict]:
        """Get executive movements (hires, departures) - deduplicated."""
        items = []
        seen = set()  # Track person-action-company tuples

        # Departures (available talent)
        departures = self.kg.query(predicate="DEPARTED_FROM", limit=limit * 2)
        for rel in departures:
            person = rel.subject.name if hasattr(rel.subject, 'name') else str(rel.subject)
            company = rel.object.name if hasattr(rel.object, 'name') else str(rel.object)

            key = (person.lower().strip(), 'left', company.lower().strip())
            if key in seen:
                continue
            seen.add(key)

            items.append({
                'person': person,
                'action': 'left',
                'company': company,
                'signal': 'Available',
                'context': getattr(rel, 'context', ''),
            })

        # New hires
        hires = self.kg.query(predicate="HIRED_BY", limit=limit * 2)
        for rel in hires:
            person = rel.subject.name if hasattr(rel.subject, 'name') else str(rel.subject)
            company = rel.object.name if hasattr(rel.object, 'name') else str(rel.object)

            key = (person.lower().strip(), 'joined', company.lower().strip())
            if key in seen:
                continue
            seen.add(key)

            items.append({
                'person': person,
                'action': 'joined',
                'company': company,
                'signal': 'Hired',
                'context': getattr(rel, 'context', ''),
            })

        return items[:limit]

    def _get_hot_candidates(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 15
    ) -> List[dict]:
        """Get hot candidates based on signals (deduplicated)."""
        candidates = []
        seen_people = set()

        # Get people who left companies
        departures = self.kg.query(predicate="DEPARTED_FROM", limit=50)
        for rel in departures:
            person = rel.subject.name if hasattr(rel.subject, 'name') else str(rel.subject)
            company = rel.object.name if hasattr(rel.object, 'name') else str(rel.object)

            # Deduplicate by person name
            key = person.lower().strip()
            if key in seen_people:
                continue
            seen_people.add(key)

            # Get their roles
            roles = self.kg.query(subject=person, limit=10)
            title = None
            for role_rel in roles:
                if role_rel.predicate in ['CEO_OF', 'CTO_OF', 'CFO_OF', 'FOUNDED']:
                    title = role_rel.predicate.replace('_OF', '').replace('_', ' ').title()
                    break

            candidates.append({
                'name': person,
                'title': title or 'Executive',
                'previous_company': company,
                'signal': 'Recently departed',
                'score': 90 if title else 70,
            })

        # Sort by score
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates[:limit]

    def _extract_amount(self, context: str) -> Optional[str]:
        """Extract funding amount from context string."""
        import re

        patterns = [
            r'\$(\d+(?:,\d{3})*(?:\.\d+)?)\s*(million|billion|M|B|m|b)',
            r'\$(\d+(?:,\d{3})*(?:\.\d+)?)',
        ]

        for pattern in patterns:
            match = re.search(pattern, context, re.IGNORECASE)
            if match:
                return match.group(0)

        return None

    def _extract_layoff_count(self, context: str) -> Optional[int]:
        """Extract layoff count from context."""
        import re

        match = re.search(r'(\d+(?:,\d{3})*)\s*(?:employees|people|workers|staff)', context, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(',', ''))

        match = re.search(r'laid off (\d+(?:,\d{3})*)', context, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(',', ''))

        return None

    def _generate_summary(self, sections: List[NewsletterSection], stats: dict) -> str:
        """Generate newsletter summary."""
        summary_parts = []

        for section in sections:
            count = len(section.items)
            if count > 0:
                summary_parts.append(f"{count} {section.title.split(' ', 1)[-1].lower()}")

        if summary_parts:
            return f"This week: {', '.join(summary_parts)}."
        else:
            return "No significant events this period."

    def to_html(self, newsletter: Newsletter) -> str:
        """Convert newsletter to HTML format - minimal, professional design."""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 680px;
            margin: 0 auto;
            padding: 40px 20px;
            background: #fff;
            color: #1a1a1a;
            line-height: 1.6;
        }}
        .header {{ margin-bottom: 32px; }}
        h1 {{
            font-size: 1.5em;
            font-weight: 600;
            color: #1a1a1a;
            margin-bottom: 8px;
        }}
        .date {{ color: #666; font-size: 0.9em; }}
        .summary {{
            color: #444;
            font-size: 1em;
            padding: 16px 0;
            border-bottom: 1px solid #eee;
            margin-bottom: 32px;
        }}
        .section {{ margin-bottom: 32px; }}
        h2 {{
            font-size: 0.85em;
            font-weight: 600;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid #eee;
        }}
        .items {{ }}
        .item {{
            padding: 12px 0;
            border-bottom: 1px solid #f5f5f5;
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 12px;
        }}
        .item:last-child {{ border-bottom: none; }}
        .item-main {{ flex: 1; }}
        .company {{ font-weight: 500; color: #1a1a1a; }}
        .detail {{ color: #666; }}
        .tag {{
            font-size: 0.7em;
            font-weight: 500;
            padding: 3px 8px;
            border-radius: 3px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            white-space: nowrap;
        }}
        .tag-sec {{ background: #e8f5e9; color: #2e7d32; }}
        .tag-news {{ background: #e3f2fd; color: #1565c0; }}
        .tag-ma {{ background: #f3e5f5; color: #7b1fa2; }}
        .tag-layoff {{ background: #ffebee; color: #c62828; }}
        .tag-available {{ background: #e8f5e9; color: #2e7d32; }}
        .tag-hired {{ background: #fff3e0; color: #e65100; }}
        .amount {{ font-weight: 500; color: #1a1a1a; }}
        .meta {{ color: #999; font-size: 0.8em; margin-top: 4px; }}
        .stats {{
            display: flex;
            gap: 24px;
            padding: 24px 0;
            border-top: 1px solid #eee;
            margin-top: 32px;
        }}
        .stat {{ text-align: left; }}
        .stat-value {{ font-size: 1.5em; font-weight: 600; color: #1a1a1a; }}
        .stat-label {{ font-size: 0.8em; color: #666; }}
        .footer {{
            margin-top: 32px;
            padding-top: 24px;
            border-top: 1px solid #eee;
            color: #999;
            font-size: 0.8em;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Recruiter Intelligence</h1>
        <div class="date">{newsletter.date.strftime('%B %d, %Y')}</div>
    </div>
    <div class="summary">{newsletter.summary}</div>
"""

        # Clean section titles (remove emojis for minimal look)
        section_titles = {
            "Companies That Raised Funding": "Funding Rounds",
            "Acquisitions & Mergers": "M&A Activity",
            "Layoffs (Displaced Talent)": "Layoffs",
            "Executive Movements": "Executive Moves",
            "Hot Candidates": "Available Talent",
        }

        for section in newsletter.sections:
            # Clean title
            title = section.title
            for old, new in section_titles.items():
                if old in title:
                    title = new
                    break
            # Remove any remaining emojis
            title = ''.join(c for c in title if ord(c) < 128 or c.isalnum())

            html += f'<div class="section"><h2>{title}</h2><div class="items">'

            for item in section.items[:10]:  # Limit items per section
                html += '<div class="item"><div class="item-main">'

                if 'company' in item and 'amount' in item:
                    # Funding item
                    html += f'<span class="company">{item["company"]}</span>'
                    if item.get('amount'):
                        html += f' <span class="detail">raised</span> <span class="amount">{item["amount"]}</span>'
                    tag_class = "tag-sec" if item.get('source') == 'SEC' else "tag-news"
                    html += f'</div><span class="tag {tag_class}">{item.get("source", "News")}</span>'

                elif 'acquirer' in item:
                    # Acquisition item
                    html += f'<span class="company">{item["acquirer"]}</span> <span class="detail">acquired</span> <span class="company">{item["target"]}</span>'
                    html += '</div><span class="tag tag-ma">M&A</span>'

                elif 'employees' in item:
                    # Layoff item
                    html += f'<span class="company">{item["company"]}</span>'
                    if item.get('employees'):
                        html += f' <span class="detail">{item["employees"]:,} employees</span>'
                    html += '</div><span class="tag tag-layoff">Layoff</span>'

                elif 'person' in item and 'action' in item:
                    # Executive move
                    action_word = "joined" if item["action"] == "joined" else "left"
                    html += f'<span class="company">{item["person"]}</span> <span class="detail">{action_word}</span> <span class="company">{item["company"]}</span>'
                    tag_class = "tag-hired" if item.get("signal") == "Hired" else "tag-available"
                    html += f'</div><span class="tag {tag_class}">{item.get("signal", "Move")}</span>'

                elif 'name' in item and 'previous_company' in item:
                    # Candidate
                    html += f'<span class="company">{item["name"]}</span>'
                    if item.get('title'):
                        html += f' <span class="detail">({item["title"]})</span>'
                    html += f' <span class="detail">from {item["previous_company"]}</span>'
                    html += '</div><span class="tag tag-available">Available</span>'

                else:
                    html += '</div>'

                html += '</div>'

            html += '</div></div>'

        # Stats
        html += '''
    <div class="stats">
        <div class="stat">
            <div class="stat-value">{entities:,}</div>
            <div class="stat-label">Entities</div>
        </div>
        <div class="stat">
            <div class="stat-value">{relationships:,}</div>
            <div class="stat-label">Relationships</div>
        </div>
    </div>
'''.format(
            entities=newsletter.stats.get('total_entities', 0),
            relationships=newsletter.stats.get('total_relationships', 0),
        )

        html += '''
    <div class="footer">
        Data from SEC EDGAR, news feeds, Layoffs.fyi, and Y Combinator.
    </div>
</body>
</html>
'''

        return html

    def to_markdown(self, newsletter: Newsletter) -> str:
        """Convert newsletter to Markdown format."""
        md = f"# {newsletter.title}\n\n"
        md += f"**{newsletter.summary}**\n\n"
        md += "---\n\n"

        for section in newsletter.sections:
            md += f"## {section.title}\n\n"

            for item in section.items[:10]:
                if 'company' in item and 'amount' in item:
                    md += f"- **{item['company']}**"
                    if item.get('amount'):
                        md += f" raised {item['amount']}"
                    if item.get('investor') and item['investor'] != 'Undisclosed Investors':
                        md += f" from {item['investor']}"
                    md += f" [{item.get('source', 'News')}]\n"

                elif 'acquirer' in item:
                    md += f"- **{item['acquirer']}** acquired **{item['target']}**\n"

                elif 'employees' in item:
                    md += f"- **{item['company']}**"
                    if item.get('employees'):
                        md += f" laid off {item['employees']} employees"
                    md += " [Layoff]\n"

                elif 'person' in item and 'action' in item:
                    md += f"- **{item['person']}** {item['action']} {item['company']}\n"

                elif 'name' in item and 'previous_company' in item:
                    md += f"- **{item['name']}**"
                    if item.get('title'):
                        md += f" ({item['title']})"
                    md += f" - left {item['previous_company']} [Available]\n"

            md += "\n"

        md += "---\n\n"
        md += f"*Entities: {newsletter.stats.get('entities', 0)} | "
        md += f"Relationships: {newsletter.stats.get('relationships', 0)}*\n"

        return md


def generate_newsletter(format: str = "html", period: str = "weekly") -> str:
    """Generate newsletter in specified format."""
    generator = NewsletterGenerator()

    if period == "daily":
        newsletter = generator.generate_daily()
    else:
        newsletter = generator.generate_weekly()

    if format == "markdown":
        return generator.to_markdown(newsletter)
    else:
        return generator.to_html(newsletter)
