"""Application settings with environment variable support."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import Optional
from pathlib import Path


# Compute base_dir at module level
_BASE_DIR = Path(__file__).parent.parent.parent.resolve()


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RI_",  # RI_DATABASE_URL, RI_ANTHROPIC_API_KEY, etc.
    )

    # Paths - computed from base_dir
    base_dir: Path = _BASE_DIR
    data_dir: Path = _BASE_DIR / "data"
    logs_dir: Path = _BASE_DIR / "logs"
    outputs_dir: Path = _BASE_DIR / "outputs"

    # Database
    database_url: str = f"sqlite:///{_BASE_DIR / 'data' / 'recruiter_intel.db'}"
    kg_database_url: str = f"sqlite:///{_BASE_DIR / 'data' / 'knowledge_graph.db'}"

    # LLM
    llm_provider: str = "gemini"  # "gemini", "anthropic", or "openai"
    gemini_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    llm_model: str = "gemini-2.0-flash"  # or "gemini-2.5-flash" for thinking
    llm_max_tokens: int = 2000
    llm_temperature: float = 0.0

    # Ingestion
    fetch_timeout_seconds: int = 30
    fetch_rate_limit_per_second: float = 1.0
    fetch_max_retries: int = 3

    # Processing
    classification_confidence_threshold: float = 0.7
    extraction_confidence_threshold: float = 0.6
    max_articles_per_run: int = 500

    # Features
    enable_llm_extraction: bool = True
    enable_full_content_fetch: bool = False


settings = Settings()
