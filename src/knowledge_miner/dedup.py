from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
from urllib.parse import urlsplit, urlunsplit
import re


def normalize_title(title: str) -> str:
    lowered = title.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "/")
    return urlunsplit((scheme, netloc, path.rstrip("/") or "/", "", ""))


def canonical_id(
    *,
    doi: str | None,
    url: str | None,
    title: str,
    year: int | None,
    openalex_id: str | None = None,
    semantic_scholar_id: str | None = None,
    patent_office: str | None = None,
    patent_number: str | None = None,
) -> str:
    if doi:
        return f"doi:{doi.lower().strip()}"
    if openalex_id:
        return f"openalex:{openalex_id.strip()}"
    if semantic_scholar_id:
        return f"s2:{semantic_scholar_id.strip()}"
    if patent_office and patent_number:
        return f"patent:{patent_office.lower().strip()}:{patent_number.strip()}"
    if url:
        digest = hashlib.sha1(canonicalize_url(url).encode("utf-8")).hexdigest()
        return f"urlsha1:{digest}"

    norm = f"{normalize_title(title)}|{year or 'na'}"
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()
    return f"titleyearsha1:{digest}"


def is_fuzzy_duplicate(
    *,
    title_a: str,
    year_a: int | None,
    title_b: str,
    year_b: int | None,
    threshold: float = 0.92,
) -> bool:
    if year_a is not None and year_b is not None and abs(year_a - year_b) > 1:
        return False
    norm_a = normalize_title(title_a)
    norm_b = normalize_title(title_b)
    similarity = SequenceMatcher(None, norm_a, norm_b).ratio()
    return similarity >= threshold

