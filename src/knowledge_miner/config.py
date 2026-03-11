from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() == "true"


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


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
    auth_enabled: bool = _as_bool(os.getenv("AUTH_ENABLED"), default=False)
    api_token: str = os.getenv("API_TOKEN", "dev-token")
    hmi_api_token: str | None = _optional_env("HMI_API_TOKEN")
    artifacts_dir: str = os.getenv("ARTIFACTS_DIR", "./artifacts")
    use_mock_connectors: bool = _as_bool(os.getenv("USE_MOCK_CONNECTORS"), default=True)
    openalex_base_url: str = os.getenv("OPENALEX_BASE_URL", "https://api.openalex.org")
    semantic_scholar_base_url: str = os.getenv("SEMANTIC_SCHOLAR_BASE_URL", "https://api.semanticscholar.org/graph/v1")
    semantic_scholar_api_key: str | None = _optional_env("SEMANTIC_SCHOLAR_API_KEY")
    brave_base_url: str = os.getenv("BRAVE_BASE_URL", "https://api.search.brave.com")
    brave_api_key: str | None = _optional_env("BRAVE_API_KEY")
    use_ai_filter: bool = _as_bool(os.getenv("USE_AI_FILTER"), default=False)
    ai_api_key: str | None = _optional_env("AI_API_KEY")
    ai_model: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    ai_timeout_seconds: float = float(os.getenv("AI_TIMEOUT_SECONDS", "20"))
    ai_min_confidence_override: float = float(os.getenv("AI_MIN_CONFIDENCE_OVERRIDE", "0.6"))
    citation_expansion_limit_per_direction: int = int(os.getenv("CITATION_EXPANSION_LIMIT_PER_DIRECTION", "50"))
    citation_expansion_parent_cap_per_iteration: int = int(os.getenv("CITATION_EXPANSION_PARENT_CAP_PER_ITERATION", "10"))
    domains_allowlist_path: str = os.getenv("DOMAINS_ALLOWLIST_PATH", "./config/domains_allowlist.txt")
    acquisition_timeout_seconds: float = float(os.getenv("ACQUISITION_TIMEOUT_SECONDS", "20"))
    acquisition_max_bytes: int = int(os.getenv("ACQUISITION_MAX_BYTES", "25000000"))
    log_dir: str = os.getenv("LOG_DIR", "./logs")
    log_file: str = os.getenv("LOG_FILE", "knowledge_miner.log")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_max_bytes: int = int(os.getenv("LOG_MAX_BYTES", "10485760"))
    log_backup_count: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    runtime_state_dir: str = os.getenv("RUNTIME_STATE_DIR", "./runtime")
    clean_on_startup: bool = _as_bool(os.getenv("CLEAN_ON_STARTUP"), default=app_env.lower() != "production")


settings = Settings()


def is_sqlite_url(database_url: str) -> bool:
    return database_url.strip().lower().startswith("sqlite")
