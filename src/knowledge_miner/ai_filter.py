from __future__ import annotations

from dataclasses import dataclass
import json

import httpx

from .config import settings


ALLOWED_DECISIONS = {"auto_accept", "needs_review", "auto_reject"}


class AIAuthError(RuntimeError):
    pass


class AIRateLimitError(RuntimeError):
    pass


class AITimeoutError(RuntimeError):
    pass


class AIProviderError(RuntimeError):
    pass


@dataclass
class AIRelevanceResult:
    decision: str
    confidence: float
    reason: str | None = None


@dataclass
class AIDocumentIdentityResult:
    title: str | None
    doi: str | None
    confidence: float


def describe_ai_filter_runtime(*, use_ai_filter: bool, api_key: str | None) -> tuple[bool, str | None]:
    if not use_ai_filter:
        return False, "AI filter disabled (USE_AI_FILTER=false); heuristic filtering only."
    if not api_key:
        return False, "AI filter requested but AI_API_KEY is missing; heuristic filtering only."
    return True, None


class AIRelevanceFilter:
    def __init__(
        self,
        *,
        enabled: bool | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.enabled = settings.use_ai_filter if enabled is None else enabled
        self.api_key = settings.ai_api_key if api_key is None else api_key
        self.model = settings.ai_model if model is None else model
        self.base_url = settings.ai_base_url if base_url is None else base_url
        self.timeout_seconds = settings.ai_timeout_seconds if timeout_seconds is None else timeout_seconds
        self._runtime_disabled_reason: str | None = None
        self._last_error_category: str | None = None
        self._runtime_warning_ready = False

    def evaluate(
        self,
        *,
        title: str,
        abstract: str | None,
        base_score: float,
        base_decision: str,
        session_queries: list[str] | None = None,
        session_context: str | None = None,
    ) -> AIRelevanceResult | None:
        self._last_error_category = None
        if self._runtime_disabled_reason is not None:
            return None
        if not self.enabled or not self.api_key:
            return None

        payload = self._build_payload(
            title=title,
            abstract=abstract,
            base_score=base_score,
            base_decision=base_decision,
            session_queries=session_queries or [],
            session_context=session_context or "",
        )
        try:
            content = self._chat(payload)
            return self._parse_result(content)
        except AIAuthError:
            self._last_error_category = "auth_error"
            self._runtime_disabled_reason = "auth_error"
            self._runtime_warning_ready = True
            return None
        except AIRateLimitError:
            self._last_error_category = "rate_limited"
            return None
        except AITimeoutError:
            self._last_error_category = "timeout"
            return None
        except Exception:
            self._last_error_category = "provider_error"
            return None

    def pop_last_error_category(self) -> str | None:
        category = self._last_error_category
        self._last_error_category = None
        return category

    def consume_runtime_warning(self) -> str | None:
        if not self._runtime_warning_ready:
            return None
        self._runtime_warning_ready = False
        if self._runtime_disabled_reason == "auth_error":
            return "AI filter disabled for run after authentication failure (401/403); falling back to needs_review."
        return None

    def _chat(self, payload: dict) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code in {401, 403}:
                    raise AIAuthError(f"ai_filter_http_{response.status_code}")
                if response.status_code == 429:
                    raise AIRateLimitError("ai_filter_http_429")
                if response.status_code >= 400:
                    raise AIProviderError(f"ai_filter_http_{response.status_code}")
                body = response.json()
        except httpx.TimeoutException as exc:
            raise AITimeoutError("ai_filter_timeout") from exc
        return body["choices"][0]["message"]["content"]

    def _build_payload(
        self,
        *,
        title: str,
        abstract: str | None,
        base_score: float,
        base_decision: str,
        session_queries: list[str],
        session_context: str,
    ) -> dict:
        system = (
            "You classify paper relevance for UPW in semiconductor manufacturing. "
            "Respond with strict JSON only."
        )
        user = {
            "task": "Classify relevance.",
            "rules": {
                "domain": "session relevance to the user-selected run topic and queries",
                "decisions": ["auto_accept", "needs_review", "auto_reject"],
            },
            "input": {
                "title": title,
                "abstract": abstract or "",
                "heuristic_score": base_score,
                "heuristic_decision": base_decision,
                "session_queries": session_queries,
                "session_context": session_context,
            },
            "required_output": {
                "decision": "auto_accept|needs_review|auto_reject",
                "confidence": "0.0-1.0",
                "reason": "short string",
            },
        }
        return {
            "model": self.model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
        }

    def _parse_result(self, content: str) -> AIRelevanceResult:
        data = json.loads(content)
        decision = str(data.get("decision", "")).strip()
        if decision not in ALLOWED_DECISIONS:
            raise ValueError("invalid_ai_decision")
        confidence = float(data.get("confidence", 0.0))
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("invalid_ai_confidence")
        reason = data.get("reason")
        return AIRelevanceResult(decision=decision, confidence=confidence, reason=reason if isinstance(reason, str) else None)


def generate_query_suggestions(
    *,
    session_context: str,
    existing_queries: list[str] | None = None,
    max_suggestions: int = 8,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> list[str]:
    resolved_api_key = settings.ai_api_key if api_key is None else api_key
    if not resolved_api_key:
      raise AIAuthError("ai_query_suggestions_missing_api_key")
    resolved_model = settings.ai_model if model is None else model
    resolved_base_url = settings.ai_base_url if base_url is None else base_url
    resolved_timeout = settings.ai_timeout_seconds if timeout_seconds is None else timeout_seconds
    url = f"{resolved_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {resolved_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": resolved_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Generate concise academic search queries for literature discovery. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Generate search queries for the research session.",
                        "rules": {
                            "count": max_suggestions,
                            "avoid_duplicates": True,
                            "prefer_academic_search_phrasing": True,
                            "do_not_number_results": True,
                        },
                        "input": {
                            "session_context": session_context,
                            "existing_queries": existing_queries or [],
                        },
                        "required_output": {
                            "suggestions": ["query 1", "query 2"],
                        },
                    }
                ),
            },
        ],
    }
    try:
        with httpx.Client(timeout=resolved_timeout) as client:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code in {401, 403}:
                raise AIAuthError(f"ai_query_suggestions_http_{response.status_code}")
            if response.status_code == 429:
                raise AIRateLimitError("ai_query_suggestions_http_429")
            if response.status_code >= 400:
                raise AIProviderError(f"ai_query_suggestions_http_{response.status_code}")
            body = response.json()
    except httpx.TimeoutException as exc:
        raise AITimeoutError("ai_query_suggestions_timeout") from exc

    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    raw = parsed.get("suggestions", [])
    if not isinstance(raw, list):
        raise ValueError("invalid_query_suggestions_payload")
    suggestions: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        suggestions.append(text)
        if len(suggestions) >= max_suggestions:
            break
    return suggestions


def extract_document_identity(
    *,
    filename: str,
    first_page_text: str,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> AIDocumentIdentityResult | None:
    resolved_api_key = settings.ai_api_key if api_key is None else api_key
    if not resolved_api_key:
        return None
    resolved_model = settings.ai_model if model is None else model
    resolved_base_url = settings.ai_base_url if base_url is None else base_url
    resolved_timeout = settings.ai_timeout_seconds if timeout_seconds is None else timeout_seconds
    url = f"{resolved_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {resolved_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": resolved_model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract academic paper identity from first-page PDF text. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Extract the most likely academic paper title and DOI from first-page PDF text.",
                        "rules": {
                            "do_not_invent": True,
                            "prefer_verbatim_title": True,
                            "doi_format": "10.xxxx/.... or null",
                        },
                        "input": {
                            "filename": filename,
                            "first_page_text": first_page_text[:4000],
                        },
                        "required_output": {
                            "title": "string or null",
                            "doi": "string or null",
                            "confidence": "0.0-1.0",
                        },
                    }
                ),
            },
        ],
    }
    try:
        with httpx.Client(timeout=resolved_timeout) as client:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code in {401, 403}:
                raise AIAuthError(f"ai_document_identity_http_{response.status_code}")
            if response.status_code == 429:
                raise AIRateLimitError("ai_document_identity_http_429")
            if response.status_code >= 400:
                raise AIProviderError(f"ai_document_identity_http_{response.status_code}")
            body = response.json()
    except httpx.TimeoutException as exc:
        raise AITimeoutError("ai_document_identity_timeout") from exc
    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    title = parsed.get("title")
    doi = parsed.get("doi")
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except Exception as exc:
        raise ValueError("invalid_ai_document_identity_confidence") from exc
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError("invalid_ai_document_identity_confidence")
    return AIDocumentIdentityResult(
        title=title.strip() if isinstance(title, str) and title.strip() else None,
        doi=doi.strip().lower() if isinstance(doi, str) and doi.strip() else None,
        confidence=confidence,
    )
