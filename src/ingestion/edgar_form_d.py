"""SEC EDGAR Form D fetcher for funding round data."""

import re
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass, field

import structlog

from ..config.settings import settings

logger = structlog.get_logger()


@dataclass
class FormDFiling:
    """Structured Form D filing data."""
    company_name: str
    cik: str
    file_number: str
    filing_date: datetime

    # Offering details
    total_amount: Optional[float] = None
    amount_sold: Optional[float] = None
    amount_remaining: Optional[float] = None

    # Company details
    state_of_incorporation: Optional[str] = None
    entity_type: Optional[str] = None  # Corporation, LLC, LP, etc.
    year_founded: Optional[int] = None

    # Industry
    industry_group: Optional[str] = None

    # Investors
    total_investors: int = 0
    accredited_investors: int = 0

    # Officers/Directors
    officers: List[dict] = field(default_factory=list)

    # Source
    source_url: str = ""


class FormDFetcher:
    """Fetches and parses SEC Form D filings."""

    SEC_IDENTITY = settings.sec_edgar_email if hasattr(settings, 'sec_edgar_email') else "recruiter-intel@example.com"

    def __init__(self):
        self._edgar = None
        self._initialized = False

    def _init_edgar(self):
        """Lazy initialization of edgar library."""
        if self._initialized:
            return

        try:
            from edgar import set_identity
            set_identity(self.SEC_IDENTITY)
            self._initialized = True
            logger.info("edgar_initialized", identity=self.SEC_IDENTITY)
        except ImportError:
            logger.warning("edgar_not_installed", msg="pip install edgartools")
            raise ImportError("edgartools not installed. Run: pip install edgartools")

    def fetch_recent(self, days_back: int = 30) -> List[FormDFiling]:
        """Fetch Form D filings from the last N days."""
        self._init_edgar()

        from edgar import get_current_filings

        filings = []
        since_date = datetime.now() - timedelta(days=days_back)

        try:
            # Use get_current_filings for real-time Form D data
            current = get_current_filings(form="D")

            if current is None:
                logger.info("form_d_no_results", days_back=days_back)
                return []

            count = 0
            for filing in current:
                # Filter by date
                try:
                    filing_date = datetime.strptime(str(filing.filing_date), '%Y-%m-%d')
                    if filing_date < since_date:
                        continue
                except (ValueError, AttributeError):
                    pass

                try:
                    parsed = self._parse_filing(filing)
                    if parsed:
                        filings.append(parsed)
                        count += 1
                except Exception as e:
                    logger.warning("form_d_parse_error", cik=getattr(filing, 'cik', 'unknown'), error=str(e))

                if count >= 500:  # Limit per batch
                    break

            logger.info("form_d_fetched", count=len(filings), days_back=days_back)
            return filings

        except Exception as e:
            logger.error("form_d_fetch_error", error=str(e))
            return []

    def _parse_filing(self, filing) -> Optional[FormDFiling]:
        """Parse a single Form D filing into structured data."""
        try:
            # Get the Form D document
            doc = filing.obj()

            # Extract offering data
            offering = doc.offering_data if hasattr(doc, 'offering_data') else None

            # Parse amounts from offering_sales_amounts
            total_amount = None
            amount_sold = None
            if offering and hasattr(offering, 'offering_sales_amounts'):
                osa = offering.offering_sales_amounts
                total_amount = self._parse_amount(getattr(osa, 'total_offering_amount', None))
                amount_sold = self._parse_amount(getattr(osa, 'total_amount_sold', None))

            # Parse issuer info from primary_issuer
            issuer = doc.primary_issuer if hasattr(doc, 'primary_issuer') else None
            company_name = filing.company  # Default from filing
            state = None
            entity_type = None
            year_founded = None

            if issuer:
                company_name = getattr(issuer, 'entity_name', None) or filing.company
                state = getattr(issuer, 'jurisdiction', None)
                entity_type = getattr(issuer, 'entity_type', None)
                year_founded = getattr(issuer, 'year_of_incorporation', None)

            # Parse industry group
            industry_group = None
            if offering and hasattr(offering, 'industry_group'):
                ig = offering.industry_group
                if hasattr(ig, 'value'):
                    industry_group = ig.value
                elif hasattr(ig, 'name'):
                    industry_group = ig.name
                elif isinstance(ig, str):
                    industry_group = ig

            # Parse investors
            total_investors = 0
            accredited_investors = 0
            if offering and hasattr(offering, 'investors'):
                inv = offering.investors
                if inv:
                    total_investors = getattr(inv, 'total_already_invested', 0) or 0
                    accredited_investors = getattr(inv, 'accredited_investors', 0) or 0

            # Parse related persons (officers/directors)
            officers = []
            if hasattr(doc, 'related_persons') and doc.related_persons:
                for person in doc.related_persons:
                    first_name = getattr(person, 'first_name', '') or ''
                    last_name = getattr(person, 'last_name', '') or ''
                    name = f"{first_name} {last_name}".strip()
                    if name:
                        officers.append({
                            "name": name,
                            "title": "",  # Not available in this API
                            "relationship": [],
                        })

            # Get filing date properly
            filing_date = filing.filing_date
            if isinstance(filing_date, str):
                filing_date = datetime.strptime(filing_date, '%Y-%m-%d')

            return FormDFiling(
                company_name=company_name,
                cik=str(filing.cik),
                file_number=getattr(filing, 'accession_no', '') or getattr(filing, 'accession_number', ''),
                filing_date=filing_date,
                total_amount=total_amount,
                amount_sold=amount_sold,
                state_of_incorporation=state,
                entity_type=entity_type,
                year_founded=year_founded,
                industry_group=industry_group,
                total_investors=int(total_investors),
                accredited_investors=int(accredited_investors),
                officers=officers,
                source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={filing.cik}&type=D",
            )

        except Exception as e:
            logger.debug("form_d_parse_failed", error=str(e))
            return None

    def _parse_amount(self, value) -> Optional[float]:
        """Parse dollar amount from various formats."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            # Remove $ and commas
            cleaned = re.sub(r'[$,]', '', value)
            try:
                return float(cleaned)
            except ValueError:
                return None

        return None

    def _clean_entity_name(self, name: str) -> str:
        """Clean up entity name by removing placeholder prefixes and fixing common issues."""
        if not name:
            return name
        # Remove common placeholder prefixes
        prefixes_to_remove = ['N/A ', 'n/a ', '--- ', '[none] ', '. ', '- ', '-- ']
        for prefix in prefixes_to_remove:
            if name.startswith(prefix):
                name = name[len(prefix):]

        # Fix "LLC CompanyName" -> "CompanyName LLC"
        if name.upper().startswith('LLC '):
            name = name[4:] + ' LLC'

        return name.strip()

    def _is_organization_name(self, name: str) -> bool:
        """Detect if a name is an organization rather than a person."""
        if not name:
            return False
        name_upper = name.upper()
        # Organization suffixes
        org_indicators = [
            'LLC', 'L.L.C.', 'INC', 'INC.', 'CORP', 'CORP.', 'LTD', 'LTD.',
            'L.P.', 'LP', 'LIMITED', 'PARTNERS', 'PARTNERSHIP', 'FUND',
            'CAPITAL', 'VENTURES', 'MANAGEMENT', 'ADVISORS', 'HOLDINGS',
            'TRUST', 'REIT', 'GROUP', 'COMPANY', 'CO.', 'SARL', 'S.A.'
        ]
        for indicator in org_indicators:
            if indicator in name_upper:
                return True
        # Names starting with organization-like patterns
        if name_upper.startswith(('LLC ', 'THE ')):
            return True
        return False

    def _extract_underlying_company(self, name: str) -> Optional[str]:
        """Extract underlying company from SPV name like 'SpaceX Dec 2025 a Series of...'"""
        # Pattern: "CompanyName ... a Series of ..."
        match = re.match(r'^([A-Za-z0-9\s]+?)(?:\s+(?:Dec|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov)\s+\d{4})?\s+a\s+[Ss]eries\s+of', name)
        if match:
            return match.group(1).strip()
        return None

    def to_extraction_result(self, filing: FormDFiling):
        """Convert Form D filing to extraction result for knowledge graph."""
        from ..extraction.interfaces import ExtractionResult, Entity, Relationship

        entities = []
        relationships = []

        # Clean company name
        clean_company_name = self._clean_entity_name(filing.company_name)

        # Company entity
        company = Entity(
            name=clean_company_name,
            entity_type="company",
            confidence=0.95,  # High confidence - legal filing
            attributes={
                "state": filing.state_of_incorporation,
                "entity_type": filing.entity_type,
                "industry": filing.industry_group,
                "year_founded": filing.year_founded,
            }
        )
        entities.append(company)

        # Check if this is an SPV - extract underlying company
        underlying = self._extract_underlying_company(clean_company_name)
        if underlying and underlying != clean_company_name:
            entities.append(Entity(
                name=underlying,
                entity_type="company",
                confidence=0.75,  # Lower confidence - inferred
                attributes={"source": "extracted_from_spv"}
            ))

        # Officer/Director entities
        for officer in filing.officers:
            raw_name = officer.get('name', '')
            if not raw_name:
                continue

            # Clean the name
            clean_name = self._clean_entity_name(raw_name)
            if not clean_name:
                continue

            # Determine if this is a person or organization
            is_org = self._is_organization_name(clean_name)
            entity_type = "company" if is_org else "person"

            person = Entity(
                name=clean_name,
                entity_type=entity_type,
                confidence=0.95,
                attributes={
                    "title": officer.get('title'),
                    "role": officer.get('relationship'),
                }
            )
            entities.append(person)

            # Add relationship
            if 'Director' in str(officer.get('relationship', [])):
                rel_type = "DIRECTOR_OF"
            elif 'Executive' in str(officer.get('relationship', [])):
                rel_type = "EXECUTIVE_OF"
            else:
                rel_type = "OFFICER_OF"

            relationships.append(Relationship(
                subject=clean_name,
                subject_type=entity_type,
                predicate=rel_type,
                object=clean_company_name,
                object_type="company",
                confidence=0.95,
                context=f"SEC Form D filing {filing.file_number}",
            ))

        # Funding relationship (if amount disclosed)
        if filing.total_amount and filing.total_amount > 0:
            # Use underlying company if extracted from SPV, otherwise use the filing company
            funding_company = underlying if underlying else clean_company_name

            relationships.append(Relationship(
                subject=funding_company,
                subject_type="company",
                predicate="RAISED_FUNDING",
                object="Undisclosed Investors",  # Form D doesn't list specific investors
                object_type="investor",
                confidence=0.95,
                context=f"${filing.total_amount:,.0f} raised via SEC Form D ({filing.total_investors} investors)",
                event_date=filing.filing_date.date() if hasattr(filing.filing_date, 'date') else filing.filing_date,
            ))

        return ExtractionResult(
            entities=entities,
            relationships=relationships,
            source_url=filing.source_url,
            event_date=filing.filing_date.date() if hasattr(filing.filing_date, 'date') else filing.filing_date,
        )


async def fetch_form_d_filings(days_back: int = 30) -> List[FormDFiling]:
    """Async wrapper for Form D fetching."""
    fetcher = FormDFetcher()
    return fetcher.fetch_recent(days_back)
