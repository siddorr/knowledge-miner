from __future__ import annotations

from collections import Counter
import re

ANCHOR_TERMS = ("ultrapure water", "upw", "semiconductor")
NEGATIVE_TERMS = ("drinking water", "desalination", "agriculture irrigation")
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "system",
    "study",
    "analysis",
    "using",
    "water",
}


def extract_keywords(texts: list[str], top_k: int = 20) -> list[str]:
    tokens: Counter[str] = Counter()
    for text in texts:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]*", text.lower())
        tokens.update(w for w in words if w not in STOPWORDS and len(w) > 2)
    return [w for w, _ in tokens.most_common(top_k)]


def build_next_queries(keywords: list[str], max_queries: int = 10) -> list[str]:
    selected = keywords[: min(8, len(keywords))]
    out: list[str] = []
    for i, kw in enumerate(selected):
        base = f"{kw} process control ultrapure water semiconductor"
        if not _valid_query(base):
            continue
        out.append(base)
        if i + 1 >= max_queries:
            break
    return out


def _valid_query(query: str) -> bool:
    q = query.lower()
    has_anchor = any(anchor in q for anchor in ANCHOR_TERMS)
    if not has_anchor:
        return False

    has_negative = any(neg in q for neg in NEGATIVE_TERMS)
    if has_negative and not ("upw" in q and "semiconductor" in q):
        return False
    return True

