"""Enrichment service for fetching external data about entities using web search."""

import asyncio
import json
import re
from typing import Optional, List
from urllib.parse import quote_plus

import aiohttp
import structlog

from .interfaces import CompanyEnrichment, PersonEnrichment, EnrichmentResult
from ..knowledge_graph.graph import KnowledgeGraph
from ..config.settings import settings

logger = structlog.get_logger()

# Gemini API endpoint for grounded search (using latest model)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


class EnrichmentService:
    """Service for enriching entities with external data via web search."""

    def __init__(self, kg: KnowledgeGraph = None):
        self.kg = kg or KnowledgeGraph()
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
                headers={
                    "Content-Type": "application/json"
                }
            )
        return self._session

    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _search_with_gemini(self, query: str) -> Optional[str]:
        """Use Gemini with Google Search grounding to get real-time data."""
        if not settings.gemini_api_key:
            logger.warning("no_gemini_api_key", msg="Cannot perform web search without API key")
            return None

        session = await self._get_session()

        payload = {
            "contents": [{
                "parts": [{
                    "text": query
                }]
            }],
            "tools": [{
                "google_search": {}
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 2000
            }
        }

        try:
            url = f"{GEMINI_API_URL}?key={settings.gemini_api_key}"
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("gemini_search_failed", status=resp.status, error=error_text[:200])
                    return None

                data = await resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
        except Exception as e:
            logger.error("gemini_search_error", error=str(e))
            return None

        return None

    def _parse_company_data(self, text: str, company_name: str) -> dict:
        """Parse company data from Gemini response."""
        data = {}

        # Extract description (first sentence or two about the company)
        desc_match = re.search(rf'{re.escape(company_name)}[^.]*\s+is\s+([^.]+\.)', text, re.IGNORECASE)
        if desc_match:
            data["description"] = f"{company_name} is {desc_match.group(1)}"
        elif company_name.lower() in text.lower():
            # Get text around company name
            idx = text.lower().find(company_name.lower())
            snippet = text[idx:idx+300].split('.')[0] + '.'
            if len(snippet) > 30:
                data["description"] = snippet

        # Extract employee count
        emp_patterns = [
            r'(\d{1,3}(?:,\d{3})*)\s*(?:employees|staff|workers)',
            r'employs?\s*(?:about|approximately|around|over|more than)?\s*(\d{1,3}(?:,\d{3})*)',
            r'workforce\s*of\s*(?:about|approximately|around|over)?\s*(\d{1,3}(?:,\d{3})*)',
            r'(\d{1,3}(?:,\d{3})*)\s*people\s*(?:work|employed)',
        ]
        for pattern in emp_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                emp_str = match.group(1).replace(',', '')
                try:
                    data["employee_count"] = int(emp_str)
                    if data["employee_count"] <= 10:
                        data["employee_range"] = "1-10"
                    elif data["employee_count"] <= 50:
                        data["employee_range"] = "11-50"
                    elif data["employee_count"] <= 200:
                        data["employee_range"] = "51-200"
                    elif data["employee_count"] <= 500:
                        data["employee_range"] = "201-500"
                    elif data["employee_count"] <= 1000:
                        data["employee_range"] = "501-1000"
                    elif data["employee_count"] <= 5000:
                        data["employee_range"] = "1001-5000"
                    else:
                        data["employee_range"] = "5000+"
                    break
                except ValueError:
                    pass

        # Extract headquarters/location
        location_patterns = [
            r'headquartered\s+in\s+([^,.]+(?:,\s*[A-Z][a-z]+)?)',
            r'based\s+in\s+([^,.]+(?:,\s*[A-Z][a-z]+)?)',
            r'headquarters?\s+(?:is|are|located)?\s*(?:in|at)?\s*([^,.]+(?:,\s*[A-Z][a-z]+)?)',
            r'(?:San Francisco|New York|Palo Alto|Mountain View|Austin|Boston|Seattle|Los Angeles|London|Berlin)',
        ]
        for pattern in location_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if match.groups():
                    data["headquarters"] = match.group(1).strip()
                else:
                    data["headquarters"] = match.group(0).strip()
                break

        # Extract founding year
        year_patterns = [
            r'founded\s+(?:in\s+)?(\d{4})',
            r'established\s+(?:in\s+)?(\d{4})',
            r'started\s+(?:in\s+)?(\d{4})',
            r'since\s+(\d{4})',
        ]
        for pattern in year_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                year = int(match.group(1))
                if 1900 <= year <= 2026:
                    data["founded_year"] = year
                    break

        # Extract funding information
        funding_patterns = [
            r'raised\s+\$?([\d.]+)\s*(billion|million|B|M)',
            r'\$?([\d.]+)\s*(billion|million|B|M)\s+(?:in\s+)?(?:funding|investment|raised)',
            r'funding\s+(?:of|totaling)\s+\$?([\d.]+)\s*(billion|million|B|M)',
            r'series\s+[A-Z]\s+(?:of|at|worth)?\s*\$?([\d.]+)\s*(billion|million|B|M)',
        ]
        for pattern in funding_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount = float(match.group(1))
                unit = match.group(2).lower()
                if unit in ['billion', 'b']:
                    amount *= 1000000000
                    data["total_funding"] = f"${match.group(1)}B"
                else:
                    amount *= 1000000
                    data["total_funding"] = f"${match.group(1)}M"
                data["total_funding_amount"] = int(amount)
                break

        # Extract funding round type
        round_patterns = [
            r'(series\s+[A-Z](?:\d)?)',
            r'(seed\s+(?:round|funding))',
            r'(pre-seed)',
            r'(IPO)',
        ]
        for pattern in round_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data["last_funding_type"] = match.group(1).title()
                break

        # Extract industry
        industry_patterns = [
            r'(?:in the|specializes in|focuses on)\s+([a-z]+(?:\s+[a-z]+)?)\s+(?:industry|sector|space|market)',
            r'(artificial intelligence|machine learning|AI|ML|fintech|healthtech|biotech|edtech|cybersecurity|cloud computing|SaaS|e-commerce|robotics)',
        ]
        for pattern in industry_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data["industry"] = match.group(1).strip().title()
                break

        # Extract company type
        if re.search(r'startup|start-up', text, re.IGNORECASE):
            data["company_type"] = "startup"
        elif re.search(r'publicly\s+traded|NYSE|NASDAQ|public\s+company', text, re.IGNORECASE):
            data["company_type"] = "public"
        elif re.search(r'private\s+company|privately\s+held', text, re.IGNORECASE):
            data["company_type"] = "private"

        # Extract website
        website_patterns = [
            r'(https?://(?:www\.)?[a-z0-9-]+\.[a-z]{2,}(?:/[^\s]*)?)',
            r'(?:website|site):\s*((?:www\.)?[a-z0-9-]+\.[a-z]{2,})',
        ]
        for pattern in website_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                url = match.group(1)
                if not url.startswith('http'):
                    url = 'https://' + url
                if 'linkedin' not in url and 'crunchbase' not in url and 'wikipedia' not in url:
                    data["website_url"] = url
                    # Extract domain
                    domain_match = re.search(r'(?:https?://)?(?:www\.)?([a-z0-9-]+\.[a-z]{2,})', url)
                    if domain_match:
                        data["domain"] = domain_match.group(1)
                    break

        return data

    def _parse_person_data(self, text: str, person_name: str) -> dict:
        """Parse person data from Gemini response."""
        data = {}

        # Extract current title
        title_patterns = [
            rf'{re.escape(person_name)}\s*(?:is|,)\s*(?:the\s+)?(?:current\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:Officer|Executive|Director|Manager|Engineer|Scientist))?)',
            r'(?:CEO|CTO|CFO|COO|CMO|CPO|VP|President|Director|Founder|Co-Founder)',
            r'serves?\s+as\s+(?:the\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
        ]
        for pattern in title_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if match.groups():
                    data["current_title"] = match.group(1).strip()
                else:
                    data["current_title"] = match.group(0).strip()
                break

        # Check if executive
        if re.search(r'CEO|CTO|CFO|COO|CMO|CPO|Chief|C-level|President|Founder|Co-Founder', text, re.IGNORECASE):
            data["is_executive"] = True
            if re.search(r'CEO|CTO|CFO|COO|CMO|CPO|Chief', text, re.IGNORECASE):
                data["executive_level"] = "C-level"
            elif re.search(r'Founder|Co-Founder', text, re.IGNORECASE):
                data["executive_level"] = "Founder"
            elif re.search(r'VP|Vice President', text, re.IGNORECASE):
                data["executive_level"] = "VP"
            elif re.search(r'Director', text, re.IGNORECASE):
                data["executive_level"] = "Director"

        # Extract current company
        company_patterns = [
            rf'{re.escape(person_name)}\s+(?:is|works)\s+(?:at|for|with)\s+([A-Z][A-Za-z0-9\s]+?)(?:\.|,|as)',
            r'(?:CEO|CTO|CFO|COO|founder)\s+(?:of|at)\s+([A-Z][A-Za-z0-9\s]+?)(?:\.|,)',
            r'joined\s+([A-Z][A-Za-z0-9\s]+?)(?:\s+in|\s+as|\.)',
        ]
        for pattern in company_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                company = match.group(1).strip()
                # Clean up company name
                company = re.sub(r'\s+(?:in|as|where|and).*$', '', company, flags=re.IGNORECASE)
                if len(company) > 2 and len(company) < 50:
                    data["current_company"] = company
                    break

        # Extract location
        location_patterns = [
            r'based\s+in\s+([^,.]+)',
            r'lives?\s+in\s+([^,.]+)',
            r'from\s+([A-Z][a-z]+(?:,\s*[A-Z][a-z]+)?)',
        ]
        for pattern in location_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data["location"] = match.group(1).strip()
                break

        # Extract previous companies
        prev_company_patterns = [
            r'(?:previously|formerly|earlier)\s+(?:at|with|worked\s+(?:at|for))\s+([A-Z][A-Za-z0-9\s,]+?)(?:\.|;|and\s+(?:later|before))',
            r'(?:ex-|former\s+)[A-Za-z]+\s+(?:at|of)\s+([A-Z][A-Za-z0-9]+)',
        ]
        previous_companies = []
        for pattern in prev_company_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                companies = [c.strip() for c in match.split(',')]
                previous_companies.extend(companies)
        if previous_companies:
            data["previous_companies"] = list(set(previous_companies))[:5]

        # Extract education
        edu_patterns = [
            r'(?:graduated|studied|degree)\s+(?:from|at)\s+([A-Z][A-Za-z\s]+(?:University|College|Institute|School))',
            r'(Stanford|MIT|Harvard|Berkeley|Yale|Princeton|Cornell|Caltech|Carnegie\s+Mellon|Columbia)',
        ]
        education = []
        for pattern in edu_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            education.extend(matches)
        if education:
            data["education"] = list(set(education))[:3]

        # Extract skills/expertise
        skill_patterns = [
            r'expertise\s+in\s+([^,.]+)',
            r'specializes?\s+in\s+([^,.]+)',
            r'known\s+for\s+([^,.]+)',
        ]
        skills = []
        for pattern in skill_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            skills.extend(matches)
        if skills:
            data["skills"] = list(set(skills))[:5]

        return data

    async def enrich_company_with_search(self, entity_id: int) -> EnrichmentResult:
        """Enrich a company entity using web search for real data."""
        entity = self.kg.get_entity_by_id(entity_id)
        if not entity or entity.entity_type != "company":
            return EnrichmentResult(
                success=False,
                source="none",
                entity_type="company",
                error="Entity not found or not a company"
            )

        company_name = entity.name
        logger.info("enriching_company_with_search", company=company_name)

        # Build search query for company info
        search_query = f"""Find detailed information about {company_name} company including:
- Company description and what they do
- Number of employees and company size
- Headquarters location (city, state, country)
- Year founded
- Total funding raised and latest funding round
- Industry/sector
- Company type (startup, public, private)
- Website URL

Provide factual, current information with specific numbers where available."""

        # Get search results
        search_result = await self._search_with_gemini(search_query)

        enrichment = CompanyEnrichment()

        if search_result:
            # Parse the search result
            parsed = self._parse_company_data(search_result, company_name)

            enrichment.description = parsed.get("description")
            enrichment.employee_count = parsed.get("employee_count")
            enrichment.employee_range = parsed.get("employee_range")
            enrichment.headquarters = parsed.get("headquarters")
            enrichment.founded_year = parsed.get("founded_year")
            enrichment.total_funding = parsed.get("total_funding")
            enrichment.total_funding_amount = parsed.get("total_funding_amount")
            enrichment.last_funding_type = parsed.get("last_funding_type")
            enrichment.industry = parsed.get("industry")
            enrichment.company_type = parsed.get("company_type")
            enrichment.domain = parsed.get("domain")
            enrichment.website_url = parsed.get("website_url")

        # Generate URL templates as fallback
        clean_name = self._clean_company_name(company_name)
        if not enrichment.linkedin_url:
            enrichment.linkedin_url = f"https://www.linkedin.com/company/{clean_name}"
        if not enrichment.crunchbase_url:
            enrichment.crunchbase_url = f"https://www.crunchbase.com/organization/{clean_name}"
        if not enrichment.domain:
            enrichment.domain = self._infer_domain(company_name)
        if enrichment.domain and not enrichment.website_url:
            enrichment.website_url = f"https://{enrichment.domain}"

        # Supplement with KG data
        funding_rels = self.kg.query(subject=company_name, predicate="FUNDED_BY", limit=20)
        if funding_rels:
            enrichment.funding_rounds = max(enrichment.funding_rounds, len(funding_rels))
            kg_investors = list(set(r.object.name for r in funding_rels))
            if kg_investors:
                enrichment.investors = kg_investors

        acq_rels = self.kg.query(subject=company_name, predicate="ACQUIRED", limit=10)
        if acq_rels and not enrichment.company_type:
            enrichment.company_type = "acquirer"

        hire_rels = self.kg.query(obj=company_name, predicate="HIRED_BY", limit=20)
        if hire_rels:
            enrichment.is_hiring = True
            enrichment.job_openings_count = len(hire_rels)

        # Store enrichment
        source = "web_search" if search_result else "internal"
        self.kg.add_enrichment(entity_id, source, enrichment.to_dict())

        logger.info("company_enriched", entity_id=entity_id, company=company_name, source=source)

        return EnrichmentResult(
            success=True,
            source=source,
            entity_type="company",
            data=enrichment.to_dict()
        )

    async def enrich_person_with_search(self, entity_id: int) -> EnrichmentResult:
        """Enrich a person entity using web search for real data."""
        entity = self.kg.get_entity_by_id(entity_id)
        if not entity or entity.entity_type != "person":
            return EnrichmentResult(
                success=False,
                source="none",
                entity_type="person",
                error="Entity not found or not a person"
            )

        person_name = entity.name
        logger.info("enriching_person_with_search", person=person_name)

        # Get context from KG to make search more specific
        context_parts = []
        hire_rels = self.kg.query(subject=person_name, predicate="HIRED_BY", limit=5)
        if hire_rels:
            context_parts.append(f"at {hire_rels[0].object.name}")

        exec_rels = self.kg.query(subject=person_name, limit=20)
        for rel in exec_rels:
            if rel.predicate in ['CEO_OF', 'CTO_OF', 'CFO_OF', 'FOUNDED']:
                context_parts.append(f"{rel.predicate.replace('_', ' ').lower()} {rel.object.name}")
                break

        context = " ".join(context_parts)

        # Build search query
        search_query = f"""Find information about {person_name} {context} including:
- Current job title and company
- Executive status and level (CEO, CTO, VP, Director, etc.)
- Previous companies they worked at
- Location
- Education background
- Areas of expertise

Provide factual information about this professional."""

        # Get search results
        search_result = await self._search_with_gemini(search_query)

        enrichment = PersonEnrichment()

        if search_result:
            # Parse the search result
            parsed = self._parse_person_data(search_result, person_name)

            enrichment.current_title = parsed.get("current_title")
            enrichment.current_company = parsed.get("current_company")
            enrichment.is_executive = parsed.get("is_executive", False)
            enrichment.executive_level = parsed.get("executive_level")
            enrichment.location = parsed.get("location")
            enrichment.previous_companies = parsed.get("previous_companies", [])
            enrichment.education = parsed.get("education", [])
            enrichment.skills = parsed.get("skills", [])

        # Generate LinkedIn search URL
        clean_name = quote_plus(person_name)
        enrichment.linkedin_url = f"https://www.linkedin.com/search/results/people/?keywords={clean_name}"

        # Supplement with KG data
        hire_rels = self.kg.query(subject=person_name, predicate="HIRED_BY", limit=20)
        if hire_rels:
            if not enrichment.current_company:
                enrichment.current_company = hire_rels[0].object.name
            kg_prev = list(set(r.object.name for r in hire_rels[1:]))
            enrichment.previous_companies = list(set(enrichment.previous_companies + kg_prev))

        exec_predicates = ["CEO_OF", "CTO_OF", "CFO_OF", "FOUNDED"]
        for pred in exec_predicates:
            exec_rels = self.kg.query(subject=person_name, predicate=pred, limit=5)
            if exec_rels:
                enrichment.is_executive = True
                if pred == "CEO_OF" and not enrichment.current_title:
                    enrichment.executive_level = "C-level"
                    enrichment.current_title = "CEO"
                elif pred == "CTO_OF" and not enrichment.current_title:
                    enrichment.executive_level = "C-level"
                    enrichment.current_title = "CTO"
                elif pred == "CFO_OF" and not enrichment.current_title:
                    enrichment.executive_level = "C-level"
                    enrichment.current_title = "CFO"
                elif pred == "FOUNDED" and not enrichment.current_title:
                    enrichment.executive_level = "Founder"
                    enrichment.current_title = "Founder"
                break

        depart_rels = self.kg.query(subject=person_name, predicate="DEPARTED_FROM", limit=10)
        if depart_rels:
            kg_depart = [r.object.name for r in depart_rels]
            enrichment.previous_companies = list(set(enrichment.previous_companies + kg_depart))

        # Store enrichment
        source = "web_search" if search_result else "internal"
        self.kg.add_enrichment(entity_id, source, enrichment.to_dict())

        logger.info("person_enriched", entity_id=entity_id, person=person_name, source=source)

        return EnrichmentResult(
            success=True,
            source=source,
            entity_type="person",
            data=enrichment.to_dict()
        )

    # Alias old methods to new ones for backward compatibility
    async def enrich_company(self, entity_id: int) -> EnrichmentResult:
        """Enrich a company entity with external data."""
        return await self.enrich_company_with_search(entity_id)

    async def enrich_person(self, entity_id: int) -> EnrichmentResult:
        """Enrich a person entity with external data."""
        return await self.enrich_person_with_search(entity_id)

    async def enrich_all_companies(self, limit: int = 100) -> List[EnrichmentResult]:
        """Enrich all company entities."""
        companies = self.kg.search_entities("", entity_type="company")
        results = []

        for company in companies[:limit]:
            try:
                result = await self.enrich_company(company.id)
                results.append(result)
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error("company_enrichment_failed", entity_id=company.id, error=str(e))

        return results

    async def enrich_all_people(self, limit: int = 100) -> List[EnrichmentResult]:
        """Enrich all person entities."""
        people = self.kg.search_entities("", entity_type="person")
        results = []

        for person in people[:limit]:
            try:
                result = await self.enrich_person(person.id)
                results.append(result)
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error("person_enrichment_failed", entity_id=person.id, error=str(e))

        return results

    def _clean_company_name(self, name: str) -> str:
        """Clean company name for URL generation."""
        for suffix in [" Inc", " Inc.", " Corp", " Corp.", " LLC", " Ltd", " Ltd."]:
            if name.endswith(suffix):
                name = name[:-len(suffix)]

        clean = name.lower().strip()
        clean = re.sub(r"[^a-z0-9\s-]", "", clean)
        clean = re.sub(r"\s+", "-", clean)
        return clean

    def _infer_domain(self, company_name: str) -> Optional[str]:
        """Try to infer company domain from name."""
        clean = self._clean_company_name(company_name)
        clean = clean.replace("-", "")

        patterns = [
            f"{clean}.com",
            f"{clean}.io",
            f"{clean}.ai",
            f"get{clean}.com",
            f"{clean}hq.com",
        ]

        return patterns[0] if clean else None
