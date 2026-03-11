from __future__ import annotations

import os
from dataclasses import dataclass


def _default_database_url() -> str:
    # Keep local development friction low while aligning production default with spec.
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    app_env = os.getenv("APP_ENV", "development").lower()
    if app_env in {"production", "prod"}:
        return "postgresql+psycopg://knowledge_miner:knowledge_miner@localhost:5432/knowledge_miner"
    return "sqlite:///./knowledge_miner.db"


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = _default_database_url()
    api_token: str = os.getenv("API_TOKEN", "dev-token")
    artifacts_dir: str = os.getenv("ARTIFACTS_DIR", "./artifacts")
    use_mock_connectors: bool = os.getenv("USE_MOCK_CONNECTORS", "true").lower() == "true"
    openalex_base_url: str = os.getenv("OPENALEX_BASE_URL", "https://api.openalex.org")
    semantic_scholar_base_url: str = os.getenv("SEMANTIC_SCHOLAR_BASE_URL", "https://api.semanticscholar.org/graph/v1")
    semantic_scholar_api_key: str | None = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    brave_base_url: str = os.getenv("BRAVE_BASE_URL", "https://api.search.brave.com")
    brave_api_key: str | None = os.getenv("BRAVE_API_KEY")
    use_ai_filter: bool = os.getenv("USE_AI_FILTER", "false").lower() == "true"
    ai_api_key: str | None = os.getenv("AI_API_KEY")
    ai_model: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    ai_timeout_seconds: float = float(os.getenv("AI_TIMEOUT_SECONDS", "20"))
    ai_min_confidence_override: float = float(os.getenv("AI_MIN_CONFIDENCE_OVERRIDE", "0.6"))
    citation_expansion_limit_per_direction: int = int(os.getenv("CITATION_EXPANSION_LIMIT_PER_DIRECTION", "50"))
    citation_expansion_parent_cap_per_iteration: int = int(os.getenv("CITATION_EXPANSION_PARENT_CAP_PER_ITERATION", "10"))
    domains_allowlist_path: str = os.getenv("DOMAINS_ALLOWLIST_PATH", "./config/domains_allowlist.txt")
    acquisition_timeout_seconds: float = float(os.getenv("ACQUISITION_TIMEOUT_SECONDS", "20"))
    acquisition_max_bytes: int = int(os.getenv("ACQUISITION_MAX_BYTES", "25000000"))


settings = Settings()


def is_sqlite_url(database_url: str) -> bool:
    return database_url.strip().lower().startswith("sqlite")
