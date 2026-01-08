"""Data models for entity enrichment."""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class CompanyEnrichment:
    """Enriched company data from external sources."""

    # Basic info
    domain: Optional[str] = None
    description: Optional[str] = None
    industry: Optional[str] = None
    sub_industry: Optional[str] = None

    # Size info
    employee_count: Optional[int] = None
    employee_range: Optional[str] = None  # "51-200", "201-500", etc.

    # Location
    headquarters: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

    # Company details
    founded_year: Optional[int] = None
    company_type: Optional[str] = None  # "private", "public", "startup"

    # Funding data
    total_funding: Optional[str] = None  # "$150M"
    total_funding_amount: Optional[int] = None  # 150000000
    last_funding_date: Optional[str] = None
    last_funding_amount: Optional[str] = None
    last_funding_type: Optional[str] = None  # "Series B", "Seed", etc.
    funding_rounds: int = 0
    investors: List[str] = field(default_factory=list)

    # Social/web presence
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    website_url: Optional[str] = None
    crunchbase_url: Optional[str] = None

    # Recruiting signals
    is_hiring: bool = False
    recent_headcount_change: Optional[str] = None  # "+20%", "-10%"
    job_openings_count: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "domain": self.domain,
            "description": self.description,
            "industry": self.industry,
            "sub_industry": self.sub_industry,
            "employee_count": self.employee_count,
            "employee_range": self.employee_range,
            "headquarters": self.headquarters,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "founded_year": self.founded_year,
            "company_type": self.company_type,
            "total_funding": self.total_funding,
            "total_funding_amount": self.total_funding_amount,
            "last_funding_date": self.last_funding_date,
            "last_funding_amount": self.last_funding_amount,
            "last_funding_type": self.last_funding_type,
            "funding_rounds": self.funding_rounds,
            "investors": self.investors,
            "linkedin_url": self.linkedin_url,
            "twitter_url": self.twitter_url,
            "website_url": self.website_url,
            "crunchbase_url": self.crunchbase_url,
            "is_hiring": self.is_hiring,
            "recent_headcount_change": self.recent_headcount_change,
            "job_openings_count": self.job_openings_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CompanyEnrichment":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PersonEnrichment:
    """Enriched person data from external sources."""

    # Professional info
    linkedin_url: Optional[str] = None
    current_title: Optional[str] = None
    current_company: Optional[str] = None
    location: Optional[str] = None

    # Career info
    tenure_months: Optional[int] = None
    is_executive: bool = False
    executive_level: Optional[str] = None  # "C-level", "VP", "Director"

    # History
    previous_companies: List[str] = field(default_factory=list)
    previous_titles: List[str] = field(default_factory=list)

    # Education (if available)
    education: List[str] = field(default_factory=list)

    # Skills/expertise
    skills: List[str] = field(default_factory=list)

    # Social presence
    twitter_url: Optional[str] = None
    github_url: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "linkedin_url": self.linkedin_url,
            "current_title": self.current_title,
            "current_company": self.current_company,
            "location": self.location,
            "tenure_months": self.tenure_months,
            "is_executive": self.is_executive,
            "executive_level": self.executive_level,
            "previous_companies": self.previous_companies,
            "previous_titles": self.previous_titles,
            "education": self.education,
            "skills": self.skills,
            "twitter_url": self.twitter_url,
            "github_url": self.github_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PersonEnrichment":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class EnrichmentResult:
    """Result of an enrichment attempt."""

    success: bool
    source: str
    entity_type: str  # "company" or "person"
    data: dict = field(default_factory=dict)
    error: Optional[str] = None
    enriched_at: datetime = field(default_factory=datetime.utcnow)
