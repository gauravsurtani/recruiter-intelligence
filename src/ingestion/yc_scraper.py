"""Y Combinator company directory scraper for startup founders data."""

import re
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class YCCompany:
    """A company from Y Combinator directory."""
    name: str
    slug: str
    batch: str  # e.g., "W24", "S23"
    status: str  # Active, Acquired, Inactive, Public
    description: str
    industries: List[str]
    team_size: Optional[int]
    location: str
    website: str
    founders: List[dict] = field(default_factory=list)  # [{name, title, linkedin}]


class YCScraper:
    """Scraper for Y Combinator company directory."""

    # YC provides a public API-like endpoint
    YC_API_URL = "https://www.ycombinator.com/companies"
    YC_ALGOLIA_URL = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"

    # Algolia credentials (public, used by YC website)
    ALGOLIA_APP_ID = "45BWZJ1SGC"
    ALGOLIA_API_KEY = "MjBjYjRiMzY0NzdhZWY0NjExY2NhZjYxMGIxYjc2MTAwNWFkNTkwNTc4NjgxYjU0YzFhYTY2ZGQ5OGY5NDMxZnJlc3RyaWN0SW5kaWNlcz0lNUIlMjJZQ0NvbXBhbnlfcHJvZHVjdGlvbiUyMiU1RCZ0YWdGaWx0ZXJzPSU1QiUyMnljZGNfcHVibGljJTIyJTVEJmFuYWx5dGljc1RhZ3M9JTVCJTIyeWNkYyUyMiU1RA=="

    def __init__(self):
        self._session = None

    async def fetch_companies(
        self,
        batch: str = None,  # e.g., "W24", "S23"
        status: str = None,  # "Active", "Acquired"
        industry: str = None,
        limit: int = 500,
    ) -> List[YCCompany]:
        """Fetch YC companies from their directory."""
        import aiohttp

        headers = {
            "x-algolia-api-key": self.ALGOLIA_API_KEY,
            "x-algolia-application-id": self.ALGOLIA_APP_ID,
            "Content-Type": "application/json",
        }

        # Build filters
        filters = ["ycdc_public"]
        if batch:
            filters.append(f"batch:{batch}")
        if status:
            filters.append(f"status:{status}")
        if industry:
            filters.append(f"industries:{industry}")

        payload = {
            "requests": [{
                "indexName": "YCCompany_production",
                "params": f"hitsPerPage={limit}&facetFilters={filters}&query="
            }]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.YC_ALGOLIA_URL,
                    json=payload,
                    headers=headers,
                    timeout=30
                ) as response:
                    if response.status != 200:
                        logger.warning("yc_fetch_failed", status=response.status)
                        return await self._fallback_scrape(limit)

                    data = await response.json()
                    results = data.get("results", [{}])[0].get("hits", [])
                    return self._parse_results(results)

        except Exception as e:
            logger.warning("yc_api_error", error=str(e))
            return await self._fallback_scrape(limit)

    async def _fallback_scrape(self, limit: int) -> List[YCCompany]:
        """Fallback to scraping if API fails."""
        import aiohttp
        from bs4 import BeautifulSoup

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.YC_API_URL,
                    timeout=30
                ) as response:
                    if response.status != 200:
                        return []

                    html = await response.text()
                    return self._parse_html(html, limit)

        except Exception as e:
            logger.error("yc_scrape_error", error=str(e))
            return []

    def _parse_results(self, hits: List[dict]) -> List[YCCompany]:
        """Parse Algolia search results."""
        companies = []

        for hit in hits:
            try:
                founders = []
                for founder in hit.get("founders", []):
                    founders.append({
                        "name": founder.get("full_name", ""),
                        "title": founder.get("title", ""),
                        "linkedin": founder.get("linkedin_url", ""),
                    })

                company = YCCompany(
                    name=hit.get("name", ""),
                    slug=hit.get("slug", ""),
                    batch=hit.get("batch", ""),
                    status=hit.get("status", "Active"),
                    description=hit.get("one_liner", "") or hit.get("long_description", "")[:200],
                    industries=hit.get("industries", []),
                    team_size=hit.get("team_size"),
                    location=hit.get("location", "") or hit.get("city", ""),
                    website=hit.get("website", "") or f"https://www.ycombinator.com/companies/{hit.get('slug', '')}",
                    founders=founders,
                )

                if company.name:
                    companies.append(company)

            except Exception as e:
                logger.debug("yc_parse_error", error=str(e))
                continue

        logger.info("yc_companies_parsed", count=len(companies))
        return companies

    def _parse_html(self, html: str, limit: int) -> List[YCCompany]:
        """Parse YC directory HTML (fallback)."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            companies = []
            # This is a simplified fallback - YC uses React so HTML parsing is limited
            # The Algolia API is the preferred method

            company_cards = soup.find_all('a', class_=re.compile(r'company'))[:limit]
            for card in company_cards:
                name = card.find('span', class_=re.compile(r'name'))
                if name:
                    companies.append(YCCompany(
                        name=name.text.strip(),
                        slug=card.get('href', '').split('/')[-1],
                        batch="",
                        status="Active",
                        description="",
                        industries=[],
                        team_size=None,
                        location="",
                        website=card.get('href', ''),
                        founders=[],
                    ))

            return companies

        except Exception as e:
            logger.error("yc_html_parse_error", error=str(e))
            return []

    # Fallback YC companies data (notable recent startups)
    FALLBACK_YC_COMPANIES = [
        {"name": "OpenAI", "batch": "W16", "status": "Active", "description": "AI research and deployment company", "industries": ["AI", "Machine Learning"], "founders": [{"name": "Sam Altman", "title": "CEO"}]},
        {"name": "Stripe", "batch": "S09", "status": "Active", "description": "Online payment processing platform", "industries": ["Fintech", "Payments"], "founders": [{"name": "Patrick Collison", "title": "CEO"}, {"name": "John Collison", "title": "President"}]},
        {"name": "Airbnb", "batch": "W09", "status": "Public", "description": "Home rental marketplace", "industries": ["Travel", "Marketplace"], "founders": [{"name": "Brian Chesky", "title": "CEO"}]},
        {"name": "DoorDash", "batch": "S13", "status": "Public", "description": "Food delivery platform", "industries": ["Delivery", "Logistics"], "founders": [{"name": "Tony Xu", "title": "CEO"}]},
        {"name": "Instacart", "batch": "S12", "status": "Public", "description": "Grocery delivery service", "industries": ["Delivery", "E-commerce"], "founders": [{"name": "Apoorva Mehta", "title": "Founder"}]},
        {"name": "Coinbase", "batch": "S12", "status": "Public", "description": "Cryptocurrency exchange", "industries": ["Crypto", "Fintech"], "founders": [{"name": "Brian Armstrong", "title": "CEO"}]},
        {"name": "Gusto", "batch": "W12", "status": "Active", "description": "Payroll and HR platform", "industries": ["HR Tech", "SaaS"], "founders": [{"name": "Josh Reeves", "title": "CEO"}]},
        {"name": "Retool", "batch": "W17", "status": "Active", "description": "Internal tools builder", "industries": ["Developer Tools", "SaaS"], "founders": [{"name": "David Hsu", "title": "CEO"}]},
        {"name": "Faire", "batch": "W17", "status": "Active", "description": "Wholesale marketplace for retailers", "industries": ["E-commerce", "Marketplace"], "founders": [{"name": "Max Rhodes", "title": "CEO"}]},
        {"name": "Brex", "batch": "W17", "status": "Active", "description": "Corporate cards and spend management", "industries": ["Fintech", "SaaS"], "founders": [{"name": "Henrique Dubugras", "title": "CEO"}]},
    ]

    async def fetch_recent_batches(self, num_batches: int = 4) -> List[YCCompany]:
        """Fetch companies from recent YC batches."""
        # YC has Winter (W) and Summer (S) batches
        current_year = datetime.now().year
        batches = []

        for year in range(current_year, current_year - 3, -1):
            batches.extend([f"W{str(year)[2:]}", f"S{str(year)[2:]}"])

        batches = batches[:num_batches]
        all_companies = []

        for batch in batches:
            companies = await self.fetch_companies(batch=batch, limit=200)
            all_companies.extend(companies)
            logger.info("yc_batch_fetched", batch=batch, count=len(companies))

        # If API failed, use fallback data
        if not all_companies:
            logger.info("using_yc_fallback_data")
            all_companies = self._get_fallback_companies()

        return all_companies

    def _get_fallback_companies(self) -> List[YCCompany]:
        """Get fallback YC company data."""
        companies = []
        for data in self.FALLBACK_YC_COMPANIES:
            founders = [{"name": f["name"], "title": f["title"], "linkedin": ""} for f in data.get("founders", [])]
            companies.append(YCCompany(
                name=data["name"],
                slug=data["name"].lower().replace(" ", "-"),
                batch=data["batch"],
                status=data["status"],
                description=data["description"],
                industries=data["industries"],
                team_size=None,
                location="San Francisco",
                website=f"https://www.ycombinator.com/companies/{data['name'].lower().replace(' ', '-')}",
                founders=founders,
            ))
        return companies

    def to_extraction_result(self, company: YCCompany):
        """Convert YCCompany to extraction result for knowledge graph."""
        from ..extraction.interfaces import ExtractionResult, Entity, Relationship

        entities = []
        relationships = []

        # Company entity
        company_entity = Entity(
            name=company.name,
            entity_type="company",
            confidence=0.95,
            attributes={
                "yc_batch": company.batch,
                "status": company.status,
                "description": company.description,
                "industries": company.industries,
                "team_size": company.team_size,
                "location": company.location,
                "website": company.website,
            }
        )
        entities.append(company_entity)

        # Founder entities and relationships
        for founder in company.founders:
            if not founder.get("name"):
                continue

            founder_entity = Entity(
                name=founder["name"],
                entity_type="person",
                confidence=0.95,
                attributes={
                    "title": founder.get("title", "Founder"),
                    "linkedin": founder.get("linkedin", ""),
                }
            )
            entities.append(founder_entity)

            # Founder relationship
            relationships.append(Relationship(
                subject=founder["name"],
                subject_type="person",
                predicate="FOUNDED",
                object=company.name,
                object_type="company",
                confidence=0.95,
                context=f"YC {company.batch} batch",
            ))

        # Investor relationship (YC is an investor)
        relationships.append(Relationship(
            subject=company.name,
            subject_type="company",
            predicate="FUNDED_BY",
            object="Y Combinator",
            object_type="investor",
            confidence=0.95,
            context=f"YC {company.batch} batch",
        ))

        return ExtractionResult(
            entities=entities,
            relationships=relationships,
            source_url=company.website,
        )


async def fetch_yc_companies(batch: str = None, limit: int = 500) -> List[YCCompany]:
    """Async wrapper for YC company fetching."""
    scraper = YCScraper()
    return await scraper.fetch_companies(batch=batch, limit=limit)
