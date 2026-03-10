from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = (
    "ieee.org",
    "acm.org",
    "springer.com",
    "nature.com",
    "wiley.com",
    "sciencedirect.com",
    "elsevier.com",
    "arxiv.org",
    "usenix.org",
    "semiconductor-digest.com",
    "appliedmaterials.com",
    "lamresearch.com",
    "asml.com",
    "semiengineering.com",
)


@lru_cache(maxsize=8)
def load_domain_allowlist(path: str) -> frozenset[str]:
    file_path = Path(path)
    if not file_path.exists():
        return frozenset(DEFAULT_ALLOWED_DOMAINS)

    domains: set[str] = set()
    for raw in file_path.read_text(encoding="utf-8").splitlines():
        cleaned = raw.strip().lower()
        if not cleaned or cleaned.startswith("#"):
            continue
        domains.add(cleaned)

    if not domains:
        return frozenset(DEFAULT_ALLOWED_DOMAINS)
    return frozenset(domains)


def is_allowed_url(url: str | None, allowlist: frozenset[str]) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    for domain in allowlist:
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False
