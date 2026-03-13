from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from .config import settings
from .domain_allowlist import is_allowed_url, load_domain_allowlist

if TYPE_CHECKING:
    from .models import Source


class RetryableProviderError(Exception):
    pass


class Connector(Protocol):
    name: str

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        ...

    def expand_citations(self, source: "Source", *, per_direction_limit: int, iteration: int) -> tuple[list[dict], list[dict]]:
        ...


class MockConnector:
    def __init__(self, name: str, source_type: str) -> None:
        self.name = name
        self.source_type = source_type

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
                    "source": self.name,
                    "source_native_id": f"{self.name}_{abs(hash(url + run_id)) % 100000}",
                    "patent_office": None,
                    "patent_number": None,
                    "type": self.source_type,
                    "discovery_method": "seed_search" if iteration == 1 else "query_expansion",
                    "parent_source_id": None,
                }
            )
        return results

    def expand_citations(self, source: "Source", *, per_direction_limit: int, iteration: int) -> tuple[list[dict], list[dict]]:
        rnd = random.Random(f"cite:{self.name}:{source.id}:{iteration}")
        backward: list[dict] = []
        forward: list[dict] = []
        n = min(3, max(0, per_direction_limit))
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

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        del run_id
        url = f"{settings.openalex_base_url.rstrip('/')}/works"
        params = {"search": query, "per-page": 25}
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

        backward_ids = list(work.get("referenced_works", []))[: max(0, per_direction_limit)]
        backward: list[dict] = []
        for wid in backward_ids:
            row = _request_json("GET", f"{settings.openalex_base_url.rstrip('/')}/works/{wid}")
            c = _openalex_work_to_candidate(row, discovery_method="backward_citation", parent_source_id=source.id)
            if c is not None:
                backward.append(c)

        forward: list[dict] = []
        cited_by_api_url = work.get("cited_by_api_url")
        if cited_by_api_url and per_direction_limit > 0:
            sep = "&" if "?" in cited_by_api_url else "?"
            resp = _request_json("GET", f"{cited_by_api_url}{sep}per-page={per_direction_limit}")
            for row in resp.get("results", []):
                c = _openalex_work_to_candidate(row, discovery_method="forward_citation", parent_source_id=source.id)
                if c is not None:
                    forward.append(c)
        return backward, forward[: max(0, per_direction_limit)]


class SemanticScholarConnector:
    name = "semantic_scholar"

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        del run_id
        url = f"{settings.semantic_scholar_base_url.rstrip('/')}/paper/search"
        params = {
            "query": query,
            "limit": 25,
            "fields": "paperId,title,year,url,abstract,externalIds",
        }
        headers: dict[str, str] = {}
        if settings.semantic_scholar_api_key:
            headers["x-api-key"] = settings.semantic_scholar_api_key
        response = _request_json("GET", url, params=params, headers=headers)
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
        paper = _semantic_scholar_fetch_paper(source)
        if not paper:
            return [], []

        backward_rows = list(paper.get("references") or [])[: max(0, per_direction_limit)]
        forward_rows = list(paper.get("citations") or [])[: max(0, per_direction_limit)]

        backward: list[dict] = []
        for row in backward_rows:
            c = _semantic_scholar_paper_to_candidate(
                row,
                discovery_method="backward_citation",
                parent_source_id=source.id,
            )
            if c is not None:
                backward.append(c)

        forward: list[dict] = []
        for row in forward_rows:
            c = _semantic_scholar_paper_to_candidate(
                row,
                discovery_method="forward_citation",
                parent_source_id=source.id,
            )
            if c is not None:
                forward.append(c)
        return backward, forward


class BraveConnector:
    name = "brave"

    def search(self, query: str, *, run_id: str, iteration: int) -> list[dict]:
        del run_id
        if not settings.brave_api_key:
            return []
        url = f"{settings.brave_base_url.rstrip('/')}/res/v1/web/search"
        params = {"q": query, "count": 20}
        headers = {"Accept": "application/json", "X-Subscription-Token": settings.brave_api_key}
        response = _request_json("GET", url, params=params, headers=headers)
        rows = response.get("web", {}).get("results", [])
        allowlist = load_domain_allowlist(settings.domains_allowlist_path)
        out: list[dict] = []
        for row in rows:
            title = row.get("title")
            row_url = row.get("url")
            if not title:
                continue
            if not is_allowed_url(row_url, allowlist):
                continue
            out.append(
                {
                    "title": title,
                    "year": _extract_year(row.get("age")),
                    "url": row_url,
                    "doi": None,
                    "abstract": row.get("description"),
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


def build_mock_connectors() -> list[Connector]:
    out = [MockConnector("openalex", "academic"), MockConnector("brave", "web")]
    if settings.use_semantic_scholar:
        out.insert(1, MockConnector("semantic_scholar", "academic"))
    return out


def build_real_connectors() -> list[Connector]:
    out: list[Connector] = [OpenAlexConnector(), BraveConnector()]
    if settings.use_semantic_scholar:
        out.insert(1, SemanticScholarConnector())
    return out


def build_connectors() -> list[Connector]:
    if settings.use_mock_connectors:
        return build_mock_connectors()
    return build_real_connectors()


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


def _semantic_scholar_fetch_paper(source: "Source") -> dict[str, Any]:
    base = settings.semantic_scholar_base_url.rstrip("/")
    paper_id = source.source_native_id
    if not paper_id and source.doi:
        paper_id = f"DOI:{source.doi}"
    if not paper_id:
        return {}

    fields = ",".join(
        [
            "paperId",
            "title",
            "year",
            "url",
            "abstract",
            "externalIds",
            "references.paperId",
            "references.title",
            "references.year",
            "references.url",
            "references.abstract",
            "references.externalIds",
            "citations.paperId",
            "citations.title",
            "citations.year",
            "citations.url",
            "citations.abstract",
            "citations.externalIds",
        ]
    )
    headers: dict[str, str] = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    return _request_json("GET", f"{base}/paper/{paper_id}", params={"fields": fields}, headers=headers)


def _semantic_scholar_paper_to_candidate(
    row: dict[str, Any],
    *,
    discovery_method: str,
    parent_source_id: str,
) -> dict | None:
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
