from __future__ import annotations

import re

DOMAIN_KEYWORDS = ["ultrapure water", "upw", "semiconductor", "wafer cleaning"]
PROCESS_KEYWORDS = ["ro", "edi", "uv185", "uv254", "degassing", "mixed bed"]
CONTAMINATION_KEYWORDS = ["particles", "toc", "trace metals", "silica", "bacteria"]
NEGATIVE_KEYWORDS = ["drinking water", "desalination", "agriculture irrigation"]


def _count_phrase(text: str, phrase: str) -> int:
    pattern = rf"\b{re.escape(phrase.lower())}\b"
    return len(re.findall(pattern, text.lower()))


def _has_upw_production_signal(text: str) -> bool:
    patterns = [
        r"\bultrapure water production\b",
        r"\bupw production\b",
        r"\bproduction of ultrapure water\b",
        r"\bultrapure water\b.{0,40}\bproduction\b",
        r"\bproduction\b.{0,40}\bultrapure water\b",
        r"\bupw\b.{0,25}\bproduction\b",
        r"\bproduction\b.{0,25}\bupw\b",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def score_text(title: str, abstract: str | None) -> float:
    text = f"{title} {abstract or ''}".lower()
    score = 0.0

    for kw in DOMAIN_KEYWORDS:
        score += _count_phrase(text, kw) * 2.0
    for kw in PROCESS_KEYWORDS:
        score += _count_phrase(text, kw) * 1.5
    for kw in CONTAMINATION_KEYWORDS:
        score += _count_phrase(text, kw) * 1.0
    for kw in NEGATIVE_KEYWORDS:
        score -= _count_phrase(text, kw) * 3.0
    # High-priority domain hint: explicit UPW/ultrapure-water production context.
    if _has_upw_production_signal(text):
        score += 2.0
    return round(score, 2)


def decision_from_score(score: float) -> tuple[bool, str]:
    if score >= 5.0:
        return True, "auto_accept"
    if score >= 3.0:
        return False, "needs_review"
    return False, "auto_reject"
