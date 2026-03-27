from __future__ import annotations

import logging
import random
import threading
import time
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from .config import settings
from .domain_allowlist import is_allowed_url, load_domain_allowlist

if TYPE_CHECKING:
    from .models import Source


logger = logging.getLogger("knowledge_miner")


class RetryableProviderError(Exception):
    pass


_SEMANTIC_SCHOLAR_STATE_LOCK = threading.Lock()
_SEMANTIC_SCHOLAR_NEXT_REQUEST_AT = 0.0
_SEMANTIC_SCHOLAR_COOLDOWN_UNTIL = 0.0
_SEMANTIC_SCHOLAR_CACHE: dict[
    tuple[str, str, tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]],
    tuple[float, dict[str, Any]],
] = {}


class Connector(Protocol):
    name: str

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        ...

    def expand_citations(self, source: "Source", *, per_direction_limit: int, iteration: int) -> tuple[list[dict], list[dict]]:
        ...


class MockConnector:
    def __init__(self, name: str, source_type: str, *, search_limit: int | None = None) -> None:
        self.name = name
        self.source_type = source_type
        self.search_limit = search_limit

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        rnd = random.Random(f"{self.name}:{run_id}:{iteration}:{query}")
        seed_terms = ["ultrapure water", "UPW", "semiconductor", "wafer cleaning", "TOC", "silica", "particles"]
        negatives = ["drinking water", "desalination", "agriculture irrigation"]
        results: list[dict] = []
        for idx in range(6):
            include_negative = rnd.random() < 0.18
            term = rnd.choice(seed_terms)
            neg = f" {rnd.choice(negatives)}" if include_negative else ""
            title = f"{term} process optimization in semiconductor fabs {idx}{neg}"
            abstract = f"Study of {term} control for UPW loops, RO EDI and UV254 in wafer cleaning lines."
            doi = None
            if rnd.random() < 0.35:
                doi = f"10.1000/{iteration}{idx}{abs(hash(query + self.name + run_id)) % 10000}"
            url = f"https://example.org/{self.name}/{iteration}/{abs(hash(query + run_id + str(idx))) % 50000}"
            results.append(
                {
                    "title": title,
                    "year": 2015 + (idx + iteration) % 11,
                    "url": url,
                    "doi": doi,
                    "abstract": abstract,
                    "journal": "Mock Semiconductor Water Journal",
                    "authors": ["A. Researcher", "B. Engineer", "C. Analyst"],
                    "citation_count": 10 + idx,
                    "source": self.name,
                    "source_native_id": f"{self.name}_{abs(hash(url + run_id)) % 100000}",
                    "patent_office": None,
                    "patent_number": None,
                    "type": self.source_type,
                    "discovery_method": "seed_search" if iteration == 1 else "query_expansion",
                    "parent_source_id": None,
                }
            )
        if self.search_limit is not None:
            return results[: max(0, self.search_limit)]
        return results

    def expand_citations(self, source: "Source", *, per_direction_limit: int, iteration: int) -> tuple[list[dict], list[dict]]:
        rnd = random.Random(f"cite:{self.name}:{source.id}:{iteration}")
        backward: list[dict] = []
        forward: list[dict] = []
        n = 3 if per_direction_limit <= 0 else min(3, max(0, per_direction_limit))
        for idx in range(n):
            year = (source.year or 2020) - 1 - idx
            backward.append(
                {
                    "title": f"{source.title} reference {idx + 1}",
                    "year": max(1900, year),
                    "url": f"https://example.org/{self.name}/ref/{abs(hash(source.id + str(idx))) % 100000}",
                    "doi": None,
                    "abstract": "Reference related to UPW process and semiconductor manufacturing.",
                    "source": self.name,
                    "source_native_id": f"{self.name}_ref_{abs(hash(source.id + str(idx))) % 100000}",
                    "openalex_id": None,
                    "semantic_scholar_id": None,
                    "patent_office": None,
                    "patent_number": None,
                    "type": source.type,
                    "discovery_method": "backward_citation",
                    "parent_source_id": source.id,
                }
            )
            forward.append(
                {
                    "title": f"{source.title} citing work {idx + 1}",
                    "year": (source.year or 2020) + 1 + idx,
                    "url": f"https://example.org/{self.name}/cited_by/{abs(hash(str(idx) + source.id)) % 100000}",
                    "doi": None,
                    "abstract": "Citing paper discussing UPW and wafer cleaning.",
                    "source": self.name,
                    "source_native_id": f"{self.name}_cited_by_{abs(hash(str(idx) + source.id)) % 100000}",
                    "openalex_id": None,
                    "semantic_scholar_id": None,
                    "patent_office": None,
                    "patent_number": None,
                    "type": source.type,
                    "discovery_method": "forward_citation",
                    "parent_source_id": source.id,
                }
            )
        return backward, forward


class OpenAlexConnector:
    name = "openalex"

    def __init__(self, *, search_limit: int | None = None) -> None:
        self.search_limit = search_limit

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        del run_id
        url = f"{settings.openalex_base_url.rstrip('/')}/works"
        configured_limit = settings.openalex_search_limit if self.search_limit is None else self.search_limit
        params = {"search": query, "per-page": max(1, min(int(configured_limit), 200))}
        response = _request_json("GET", url, params=params)
        rows = response.get("results", [])
        out: list[dict] = []
        for row in rows:
            title = row.get("title")
            if not title:
                continue
            openalex_id = row.get("id")
            abstract = _openalex_abstract(row.get("abstract_inverted_index"))
            year = row.get("publication_year")
            doi = (row.get("doi") or "").replace("https://doi.org/", "") or None
            source_url = row.get("primary_location", {}).get("landing_page_url") or row.get("id")
            out.append(
                {
                    "title": title,
                    "year": year if isinstance(year, int) else None,
                    "url": source_url,
                    "doi": doi,
                    "abstract": abstract,
                    "journal": _openalex_journal(row),
                    "authors": _openalex_authors(row),
                    "citation_count": _openalex_citation_count(row),
                    "source": self.name,
                    "source_native_id": openalex_id,
                    "openalex_id": openalex_id,
                    "semantic_scholar_id": None,
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "seed_search" if iteration == 1 else "query_expansion",
                    "parent_source_id": None,
                }
            )
        return out

    def expand_citations(self, source: "Source", *, per_direction_limit: int, iteration: int) -> tuple[list[dict], list[dict]]:
        del iteration
        work = _openalex_fetch_work(source)
        if not work:
            return [], []

        all_backward_ids = list(work.get("referenced_works", []))
        backward_ids = all_backward_ids if per_direction_limit <= 0 else all_backward_ids[: max(0, per_direction_limit)]
        backward: list[dict] = []
        for wid in backward_ids:
            row = _request_json("GET", f"{settings.openalex_base_url.rstrip('/')}/works/{wid}")
            c = _openalex_work_to_candidate(row, discovery_method="backward_citation", parent_source_id=source.id)
            if c is not None:
                backward.append(c)

        forward: list[dict] = []
        cited_by_api_url = work.get("cited_by_api_url") or _openalex_cited_by_api_url(work)
        if cited_by_api_url:
            if per_direction_limit > 0:
                sep = "&" if "?" in cited_by_api_url else "?"
                resp = _request_json("GET", f"{cited_by_api_url}{sep}per-page={min(per_direction_limit, 200)}")
                for row in resp.get("results", []):
                    c = _openalex_work_to_candidate(row, discovery_method="forward_citation", parent_source_id=source.id)
                    if c is not None:
                        forward.append(c)
                return backward, forward[: max(0, per_direction_limit)]

            # Unbounded mode: iterate all pages with OpenAlex cursor pagination.
            cursor = "*"
            while True:
                sep = "&" if "?" in cited_by_api_url else "?"
                resp = _request_json("GET", f"{cited_by_api_url}{sep}per-page=200&cursor={cursor}")
                rows = resp.get("results", [])
                for row in rows:
                    c = _openalex_work_to_candidate(row, discovery_method="forward_citation", parent_source_id=source.id)
                    if c is not None:
                        forward.append(c)
                meta = resp.get("meta") or {}
                next_cursor = meta.get("next_cursor")
                if not rows or not next_cursor:
                    break
                cursor = str(next_cursor)
        return backward, forward


class SemanticScholarConnector:
    name = "semantic_scholar"

    def __init__(self, *, search_limit: int | None = None) -> None:
        self.search_limit = search_limit

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        del run_id
        url = f"{settings.semantic_scholar_base_url.rstrip('/')}/paper/search/bulk"
        configured_limit = 25 if self.search_limit is None else self.search_limit
        params = {
            "query": query,
            "limit": max(1, min(int(configured_limit), 100)),
            "fields": "paperId,title,year,url,abstract,externalIds",
        }
        headers: dict[str, str] = {}
        if settings.semantic_scholar_api_key:
            headers["x-api-key"] = settings.semantic_scholar_api_key
        response = _semantic_scholar_request_json("GET", url, params=params, headers=headers)
        rows = response.get("data", [])
        out: list[dict] = []
        for row in rows:
            title = row.get("title")
            if not title:
                continue
            external_ids = row.get("externalIds") or {}
            doi = external_ids.get("DOI")
            paper_id = row.get("paperId")
            out.append(
                {
                    "title": title,
                    "year": row.get("year") if isinstance(row.get("year"), int) else None,
                    "url": row.get("url"),
                    "doi": doi,
                    "abstract": row.get("abstract"),
                    "journal": _semantic_scholar_journal(row),
                    "authors": _semantic_scholar_authors(row),
                    "citation_count": _semantic_scholar_citation_count(row),
                    "source": self.name,
                    "source_native_id": paper_id,
                    "openalex_id": None,
                    "semantic_scholar_id": paper_id,
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "seed_search" if iteration == 1 else "query_expansion",
                    "parent_source_id": None,
                }
            )
        return out

    def expand_citations(self, source: "Source", *, per_direction_limit: int, iteration: int) -> tuple[list[dict], list[dict]]:
        del iteration
        forward = _semantic_scholar_fetch_forward_citations_by_doi(source, per_direction_limit=per_direction_limit)
        paper = _semantic_scholar_fetch_paper(source)
        if not paper:
            return [], forward

        backward_rows = list(paper.get("references") or [])
        forward_rows = [] if forward else list(paper.get("citations") or [])
        if per_direction_limit > 0:
            backward_rows = backward_rows[: max(0, per_direction_limit)]
            forward_rows = forward_rows[: max(0, per_direction_limit)]

        backward: list[dict] = []
        backward_dropped_missing_title = 0
        for row in backward_rows:
            c = _semantic_scholar_paper_to_candidate(
                row,
                discovery_method="backward_citation",
                parent_source_id=source.id,
            )
            if c is None:
                backward_dropped_missing_title += 1
                continue
            backward.append(c)

        if backward_rows or forward or forward_rows:
            first_backward = backward_rows[0] if backward_rows else {}
            nested_backward = {}
            if isinstance(first_backward, dict):
                nested_backward = first_backward.get("citedPaper") or first_backward.get("citingPaper") or {}
            logger.info(
                "semantic_scholar_backward_debug doi=%s source_id=%s raw_backward=%s mapped_backward=%s dropped_backward=%s raw_forward_embedded=%s direct_forward=%s first_backward_keys=%s nested_keys=%s",
                getattr(source, "doi", None),
                getattr(source, "id", None),
                len(backward_rows),
                len(backward),
                backward_dropped_missing_title,
                len(forward_rows),
                len(forward),
                sorted(first_backward.keys()) if isinstance(first_backward, dict) else [],
                sorted(nested_backward.keys()) if isinstance(nested_backward, dict) else [],
            )

        if forward:
            return backward, forward[: max(0, per_direction_limit)] if per_direction_limit > 0 else forward

        fallback_forward: list[dict] = []
        for row in forward_rows:
            c = _semantic_scholar_paper_to_candidate(
                row,
                discovery_method="forward_citation",
                parent_source_id=source.id,
            )
            if c is not None:
                fallback_forward.append(c)
        return backward, fallback_forward


class BraveConnector:
    name = "brave"

    def __init__(self, *, search_count: int | None = None) -> None:
        self.search_count = search_count

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        del run_id
        if not settings.brave_api_key:
            return []
        url = f"{settings.brave_base_url.rstrip('/')}/res/v1/web/search"
        configured_count = settings.brave_search_count if self.search_count is None else self.search_count
        params = {"q": query, "count": max(1, min(int(configured_count), 20))}
        headers = {"Accept": "application/json", "X-Subscription-Token": settings.brave_api_key}
        response = _request_json("GET", url, params=params, headers=headers)
        rows = response.get("web", {}).get("results", [])
        allowlist = load_domain_allowlist(settings.domains_allowlist_path) if settings.brave_require_allowlist else []
        out: list[dict] = []
        for row in rows:
            title = row.get("title")
            row_url = row.get("url")
            if not title:
                continue
            if settings.brave_require_allowlist and not is_allowed_url(row_url, allowlist):
                continue
            out.append(
                {
                    "title": title,
                    "year": _extract_year(row.get("age")),
                    "url": row_url,
                    "doi": None,
                    "abstract": row.get("description"),
                    "journal": None,
                    "authors": [],
                    "citation_count": None,
                    "source": self.name,
                    "source_native_id": row.get("url"),
                    "openalex_id": None,
                    "semantic_scholar_id": None,
                    "patent_office": None,
                    "patent_number": None,
                    "type": "web",
                    "discovery_method": "seed_search" if iteration == 1 else "query_expansion",
                    "parent_source_id": None,
                }
            )
        return out

    def expand_citations(self, source: "Source", *, per_direction_limit: int, iteration: int) -> tuple[list[dict], list[dict]]:
        del source, per_direction_limit, iteration
        # Web search results do not provide structured citation graph data.
        return [], []


def build_mock_connectors(provider_limits: dict[str, int] | None = None) -> list[Connector]:
    provider_limits = provider_limits or {}
    out = [
        MockConnector("openalex", "academic", search_limit=provider_limits.get("openalex")),
        MockConnector("brave", "web", search_limit=provider_limits.get("brave")),
    ]
    if settings.use_semantic_scholar:
        out.insert(
            1,
            MockConnector("semantic_scholar", "academic", search_limit=provider_limits.get("semantic_scholar")),
        )
    return out


def build_real_connectors(provider_limits: dict[str, int] | None = None) -> list[Connector]:
    provider_limits = provider_limits or {}
    out: list[Connector] = [
        OpenAlexConnector(search_limit=provider_limits.get("openalex")),
        BraveConnector(search_count=provider_limits.get("brave")),
    ]
    if settings.use_semantic_scholar:
        out.insert(1, SemanticScholarConnector(search_limit=provider_limits.get("semantic_scholar")))
    return out


def build_connectors(provider_limits: dict[str, int] | None = None) -> list[Connector]:
    if settings.use_mock_connectors:
        return build_mock_connectors(provider_limits=provider_limits)
    return build_real_connectors(provider_limits=provider_limits)


def _request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.request(method, url, params=params, headers=headers)
    except httpx.RequestError as exc:
        raise RetryableProviderError(str(exc)) from exc

    if resp.status_code == 429 or 500 <= resp.status_code <= 599:
        raise RetryableProviderError(f"provider_transient_http_{resp.status_code}")
    if resp.status_code >= 400:
        return {}
    return resp.json()


def _openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    max_pos = -1
    for positions in inverted_index.values():
        if positions:
            max_pos = max(max_pos, max(positions))
    if max_pos < 0:
        return None
    terms = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            if 0 <= pos < len(terms):
                terms[pos] = word
    text = " ".join(t for t in terms if t).strip()
    return text or None


def _openalex_journal(row: dict[str, Any]) -> str | None:
    source = ((row.get("primary_location") or {}).get("source") or {})
    display_name = source.get("display_name")
    return display_name or None


def _openalex_authors(row: dict[str, Any]) -> list[str]:
    authors: list[str] = []
    for authorship in row.get("authorships") or []:
        author = (authorship.get("author") or {}).get("display_name")
        if author:
            authors.append(author)
    return authors


def _openalex_citation_count(row: dict[str, Any]) -> int | None:
    value = row.get("cited_by_count")
    return value if isinstance(value, int) else None


def _extract_year(age_text: str | None) -> int | None:
    if not age_text:
        return None
    # Example values can contain date-like snippets; pull a 4-digit year if present.
    for token in age_text.replace("/", " ").replace("-", " ").split():
        if len(token) == 4 and token.isdigit():
            year = int(token)
            if 1900 <= year <= 2100:
                return year
    return None


def _openalex_fetch_work(source: "Source") -> dict[str, Any]:
    if source.source_native_id and str(source.source_native_id).startswith("https://openalex.org/"):
        work_id = source.source_native_id
    elif source.doi:
        work_id = f"https://doi.org/{source.doi}"
    else:
        return {}
    return _request_json("GET", f"{settings.openalex_base_url.rstrip('/')}/works/{work_id}")


def _openalex_cited_by_api_url(work: dict[str, Any]) -> str | None:
    existing = work.get("cited_by_api_url")
    if isinstance(existing, str) and existing.strip():
        return existing
    work_id = str(work.get("id") or "").strip()
    if not work_id:
        return None
    short_id = work_id.rsplit("/", 1)[-1]
    if not short_id:
        return None
    return f"{settings.openalex_base_url.rstrip('/')}/works?filter=cites:{short_id}"


def _openalex_work_to_candidate(row: dict[str, Any], *, discovery_method: str, parent_source_id: str) -> dict | None:
    title = row.get("title")
    if not title:
        return None
    openalex_id = row.get("id")
    abstract = _openalex_abstract(row.get("abstract_inverted_index"))
    year = row.get("publication_year")
    doi = (row.get("doi") or "").replace("https://doi.org/", "") or None
    source_url = row.get("primary_location", {}).get("landing_page_url") or row.get("id")
    return {
        "title": title,
        "year": year if isinstance(year, int) else None,
        "url": source_url,
        "doi": doi,
        "abstract": abstract,
        "journal": _openalex_journal(row),
        "authors": _openalex_authors(row),
        "citation_count": _openalex_citation_count(row),
        "source": "openalex",
        "source_native_id": openalex_id,
        "openalex_id": openalex_id,
        "semantic_scholar_id": None,
        "patent_office": None,
        "patent_number": None,
        "type": "academic",
        "discovery_method": discovery_method,
        "parent_source_id": parent_source_id,
    }


def _semantic_scholar_doi_variants(doi: str | None) -> list[str]:
    if not doi:
        return []
    value = str(doi).strip()
    if not value:
        return []
    variants = [value]
    upper_value = value.upper()
    if upper_value not in variants:
        variants.append(upper_value)
    return variants


def _semantic_scholar_citation_fields() -> str:
    return ",".join(
        [
            "paperId",
            "title",
            "year",
            "url",
            "externalIds",
            "citationCount",
            "references.paperId",
            "references.title",
            "references.year",
            "references.url",
            "references.externalIds",
            "references.citationCount",
            "citations.paperId",
            "citations.title",
            "citations.year",
            "citations.url",
            "citations.externalIds",
            "citations.citationCount",
        ]
    )


def _semantic_scholar_forward_citation_fields() -> str:
    return ",".join(
        [
            "citingPaper.paperId",
            "citingPaper.title",
            "citingPaper.year",
            "citingPaper.url",
            "citingPaper.externalIds",
            "citingPaper.authors",
            "citingPaper.citationCount",
        ]
    )


def _semantic_scholar_fetch_forward_citations_by_doi(source: "Source", *, per_direction_limit: int) -> list[dict[str, Any]]:
    base = settings.semantic_scholar_base_url.rstrip("/")
    headers: dict[str, str] = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    page_limit = max(1, min(int(settings.semantic_scholar_citations_page_limit), 9999))
    max_pages = max(1, int(settings.semantic_scholar_citations_max_pages))
    max_results = max(1, int(settings.semantic_scholar_citations_max_results))
    if per_direction_limit > 0:
        max_results = min(max_results, per_direction_limit)
    fields = _semantic_scholar_forward_citation_fields()
    forward_rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for doi_value in _semantic_scholar_doi_variants(getattr(source, "doi", None)):
        paper_id = f"DOI:{doi_value}"
        for mode in ("get_params", "post_json", "post_params"):
            pagination: dict[str, Any] = {}
            mode_rows: list[dict[str, Any]] = []
            for _page_index in range(max_pages):
                remaining = max_results - len(mode_rows)
                if remaining <= 0:
                    break
                response = _semantic_scholar_forward_citations_request(
                    base=base,
                    paper_id=paper_id,
                    fields=fields,
                    page_limit=min(page_limit, remaining),
                    pagination=pagination,
                    headers=headers,
                    mode=mode,
                )
                rows = response.get("data") or []
                if not rows:
                    break
                for row in rows:
                    candidate = _semantic_scholar_citing_paper_to_candidate(row, parent_source_id=source.id)
                    if candidate is None:
                        continue
                    key = _semantic_scholar_candidate_identity_key(candidate)
                    if key in seen_keys:
                        continue
                    mode_rows.append(candidate)
                    seen_keys.add(key)
                    if len(mode_rows) >= max_results:
                        break
                if len(mode_rows) >= max_results:
                    break
                next_page = _semantic_scholar_extract_pagination_token(response)
                if not next_page:
                    break
                pagination = next_page
            if mode_rows:
                forward_rows.extend(mode_rows)
                break
        if forward_rows:
            return forward_rows
    return []


def _semantic_scholar_fetch_paper(source: "Source") -> dict[str, Any]:
    base = settings.semantic_scholar_base_url.rstrip("/")
    doi = getattr(source, "doi", None)
    title = getattr(source, "title", None)
    semantic_scholar_id = getattr(source, "semantic_scholar_id", None)
    source_name = getattr(source, "source", None)
    source_native_id = getattr(source, "source_native_id", None)
    openalex_id = getattr(source, "openalex_id", None)
    url = getattr(source, "url", None)
    paper_ids: list[str] = []
    for doi_value in _semantic_scholar_doi_variants(doi):
        paper_ids.append(f"DOI:{doi_value}")
    if semantic_scholar_id:
        paper_ids.append(str(semantic_scholar_id))
    if source_name == "semantic_scholar" and source_native_id:
        paper_ids.append(str(source_native_id))
    if openalex_id:
        paper_ids.append(str(openalex_id))
    elif source_name == "openalex" and source_native_id:
        paper_ids.append(str(source_native_id))
    if url:
        paper_ids.append(str(url))
    if source_native_id:
        paper_ids.append(str(source_native_id))
    paper_ids = [paper_id for idx, paper_id in enumerate(paper_ids) if paper_id and paper_id not in paper_ids[:idx]]
    if not paper_ids:
        return {}

    fields = _semantic_scholar_citation_fields()
    headers: dict[str, str] = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    for paper_id in paper_ids:
        paper = _semantic_scholar_request_json(
            "GET",
            f"{base}/paper/{paper_id}",
            params={"fields": fields},
            headers=headers,
        )
        if paper:
            return paper
    if title:
        response = _semantic_scholar_request_json(
            "GET",
            f"{base}/paper/search",
            params={"query": str(title), "limit": 1, "fields": fields},
            headers=headers,
        )
        rows = response.get("data") or []
        if rows:
            return rows[0]
    return {}


def _semantic_scholar_paper_to_candidate(
    row: dict[str, Any],
    *,
    discovery_method: str,
    parent_source_id: str,
) -> dict | None:
    nested_paper = row.get("citedPaper") or row.get("citingPaper")
    if isinstance(nested_paper, dict) and nested_paper:
        row = nested_paper
    title = row.get("title")
    if not title:
        return None
    paper_id = row.get("paperId")
    external_ids = row.get("externalIds") or {}
    doi = external_ids.get("DOI")
    return {
        "title": title,
        "year": row.get("year") if isinstance(row.get("year"), int) else None,
        "url": row.get("url"),
        "doi": doi,
        "abstract": row.get("abstract"),
        "journal": _semantic_scholar_journal(row),
        "authors": _semantic_scholar_authors(row),
        "citation_count": _semantic_scholar_citation_count(row),
        "source": "semantic_scholar",
        "source_native_id": paper_id,
        "openalex_id": None,
        "semantic_scholar_id": paper_id,
        "patent_office": None,
        "patent_number": None,
        "type": "academic",
        "discovery_method": discovery_method,
        "parent_source_id": parent_source_id,
    }


def _semantic_scholar_citing_paper_to_candidate(row: dict[str, Any], *, parent_source_id: str) -> dict | None:
    paper = row.get("citingPaper") or {}
    title = paper.get("title")
    if not title:
        return None
    paper_id = paper.get("paperId")
    external_ids = paper.get("externalIds") or {}
    doi = external_ids.get("DOI")
    return {
        "title": title,
        "year": paper.get("year") if isinstance(paper.get("year"), int) else None,
        "url": paper.get("url"),
        "doi": doi,
        "abstract": None,
        "journal": _semantic_scholar_journal(paper),
        "authors": _semantic_scholar_authors(paper),
        "citation_count": _semantic_scholar_citation_count(paper),
        "source": "semantic_scholar",
        "source_native_id": paper_id,
        "openalex_id": None,
        "semantic_scholar_id": paper_id,
        "patent_office": None,
        "patent_number": None,
        "type": "academic",
        "discovery_method": "forward_citation",
        "parent_source_id": parent_source_id,
    }


def _semantic_scholar_journal(row: dict[str, Any]) -> str | None:
    venue = row.get("venue")
    return venue if isinstance(venue, str) and venue.strip() else None


def _semantic_scholar_authors(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for author in row.get("authors") or []:
        name = author.get("name")
        if isinstance(name, str) and name.strip():
            out.append(name)
    return out


def _cache_key(
    method: str,
    url: str,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
) -> tuple[str, str, tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    items: tuple[tuple[str, str], ...] = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
    body_items: tuple[tuple[str, str], ...] = tuple(sorted((str(k), str(v)) for k, v in (json_body or {}).items()))
    return (method.upper(), url, items, body_items)


def _semantic_scholar_request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    retry_multiplier: float = 1.0,
) -> dict[str, Any]:
    global _SEMANTIC_SCHOLAR_NEXT_REQUEST_AT, _SEMANTIC_SCHOLAR_COOLDOWN_UNTIL
    cache_key = _cache_key(method, url, params, json_body)
    now = time.monotonic()
    with _SEMANTIC_SCHOLAR_STATE_LOCK:
        cached = _SEMANTIC_SCHOLAR_CACHE.get(cache_key)
        if cached is not None and now - cached[0] <= max(0.0, settings.semantic_scholar_cache_ttl_seconds):
            return dict(cached[1])
        if _SEMANTIC_SCHOLAR_COOLDOWN_UNTIL > now:
            raise RetryableProviderError("provider_transient_http_429_cooldown")
        wait_seconds = max(0.0, _SEMANTIC_SCHOLAR_NEXT_REQUEST_AT - now)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _SEMANTIC_SCHOLAR_NEXT_REQUEST_AT = time.monotonic() + max(0.0, settings.semantic_scholar_min_interval_seconds)

    max_attempts = max(1, int(round(max(1.0, float(settings.semantic_scholar_max_retries) * max(1.0, retry_multiplier)))))
    for attempt in range(max_attempts):
        retry_after_seconds: float | None = None
        try:
            with httpx.Client(timeout=settings.semantic_scholar_timeout_seconds) as client:
                resp = client.request(method, url, params=params, headers=headers, json=json_body)
        except httpx.TimeoutException as exc:
            if attempt >= max_attempts - 1:
                raise RetryableProviderError("provider_timeout") from exc
            time.sleep(_semantic_scholar_backoff_seconds(attempt))
            continue
        except httpx.RequestError as exc:
            if attempt >= max_attempts - 1:
                raise RetryableProviderError(str(exc)) from exc
            time.sleep(_semantic_scholar_backoff_seconds(attempt))
            continue

        if resp.status_code == 429:
            retry_after_seconds = _retry_after_seconds(resp.headers.get("Retry-After"))
            cooldown_seconds = retry_after_seconds or max(0.0, settings.semantic_scholar_cooldown_seconds)
            with _SEMANTIC_SCHOLAR_STATE_LOCK:
                _SEMANTIC_SCHOLAR_COOLDOWN_UNTIL = max(_SEMANTIC_SCHOLAR_COOLDOWN_UNTIL, time.monotonic() + cooldown_seconds)
            if attempt >= max_attempts - 1:
                raise RetryableProviderError("provider_transient_http_429")
            time.sleep(retry_after_seconds or _semantic_scholar_backoff_seconds(attempt))
            continue

        if 500 <= resp.status_code <= 599:
            if attempt >= max_attempts - 1:
                raise RetryableProviderError(f"provider_transient_http_{resp.status_code}")
            time.sleep(_semantic_scholar_backoff_seconds(attempt))
            continue

        if resp.status_code >= 400:
            return {}

        payload = resp.json()
        with _SEMANTIC_SCHOLAR_STATE_LOCK:
            _SEMANTIC_SCHOLAR_CACHE[cache_key] = (time.monotonic(), payload)
        return payload

    raise RetryableProviderError("provider_transient_http_429")


def _semantic_scholar_backoff_seconds(attempt: int) -> float:
    base = [2.0, 5.0, 10.0]
    index = min(max(attempt, 0), len(base) - 1)
    return random.uniform(0.0, base[index])


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = float(raw)
    except ValueError:
        return None
    return max(0.0, parsed)


def _semantic_scholar_extract_pagination_token(response: dict[str, Any]) -> dict[str, Any] | None:
    token = response.get("next") or response.get("nextPageToken") or response.get("token")
    if token:
        return {"token": token}
    offset = response.get("offset")
    next_offset = response.get("nextOffset")
    if isinstance(next_offset, int):
        return {"offset": next_offset}
    if isinstance(offset, int) and response.get("data"):
        limit = response.get("limit")
        if isinstance(limit, int) and limit > 0:
            return {"offset": offset + limit}
    return None


def _semantic_scholar_candidate_identity_key(candidate: dict[str, Any]) -> tuple[str, str]:
    if candidate.get("semantic_scholar_id"):
        return ("paper_id", str(candidate["semantic_scholar_id"]).strip())
    if candidate.get("doi"):
        return ("doi", str(candidate["doi"]).strip().lower())
    normalized_title = " ".join(str(candidate.get("title") or "").strip().lower().split())
    return ("title", f"{normalized_title}|{candidate.get('year') or ''}")


def _semantic_scholar_forward_citations_request(
    *,
    base: str,
    paper_id: str,
    fields: str,
    page_limit: int,
    pagination: dict[str, Any],
    headers: dict[str, str],
    mode: str,
) -> dict[str, Any]:
    url = f"{base}/paper/{paper_id}/citations"
    payload = {"fields": fields, "limit": page_limit, **pagination}
    if mode == "post_json":
        return _semantic_scholar_request_json(
            "POST",
            url,
            headers=headers,
            json_body=payload,
            retry_multiplier=settings.semantic_scholar_citations_retry_multiplier,
        )
    if mode == "post_params":
        return _semantic_scholar_request_json(
            "POST",
            url,
            headers=headers,
            params=payload,
            retry_multiplier=settings.semantic_scholar_citations_retry_multiplier,
        )
    return _semantic_scholar_request_json(
        "GET",
        url,
        headers=headers,
        params=payload,
        retry_multiplier=settings.semantic_scholar_citations_retry_multiplier,
    )


def _semantic_scholar_citation_count(row: dict[str, Any]) -> int | None:
    value = row.get("citationCount")
    return value if isinstance(value, int) else None
