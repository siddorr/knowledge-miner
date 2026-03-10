from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./knowledge_miner.db")
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


settings = Settings()
