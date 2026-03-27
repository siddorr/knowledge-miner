from __future__ import annotations

from typing import NamedTuple
import html
import re


class HtmlArtifactQuality(NamedTuple):
    accepted: bool
    status: str
    reason: str
    extracted_text: str
    article_signal_score: float


_INVALID_TITLE_PATTERNS = (
    r"sci-hub:.*absent",
    r"\bredirecting\b",
    r"\baccess denied\b",
    r"\bsign in\b",
    r"\blog in\b",
    r"\bcaptcha\b",
    r"\bcloudflare\b",
    r"\benable javascript\b",
    r"\bnot found\b",
    r"\bchallenge\b",
)
_INVALID_BODY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sci-hub:.*(absent|not in the database)", "html_placeholder_page"),
    (r"redirecting", "html_redirect_page"),
    (r"(sign in|log in|institutional access|access through your institution|purchase access)", "html_login_or_challenge"),
    (r"(captcha|cloudflare|enable javascript|security check)", "html_login_or_challenge"),
    (r"(404|not found|access denied)", "html_missing_article_signals"),
)
_ARTICLE_SIGNAL_PATTERNS: tuple[tuple[str, float], ...] = (
    (r"\babstract\b", 0.8),
    (r"\bintroduction\b", 0.8),
    (r"\bmaterials?\b", 0.6),
    (r"\bmethods?\b", 0.6),
    (r"\bresults?\b", 0.8),
    (r"\bdiscussion\b", 0.8),
    (r"\bconclusions?\b", 0.8),
    (r"\breferences\b", 0.5),
    (r"10\.\d{4,9}/[-._;()/:a-z0-9]+", 0.8),
)


def extract_html_text(html_text: str) -> tuple[str, int]:
    reduced = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    reduced = re.sub(r"<style[\s\S]*?</style>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<noscript[\s\S]*?</noscript>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<nav[\s\S]*?</nav>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<header[\s\S]*?</header>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<footer[\s\S]*?</footer>", " ", reduced, flags=re.I)

    preferred_blocks = re.findall(r"<(article|main)\b[\s\S]*?</\1>", reduced, flags=re.I)
    block_html = ""
    if preferred_blocks:
        candidates = re.findall(r"<(?:article|main)\b[\s\S]*?</(?:article|main)>", reduced, flags=re.I)
        block_html = max(candidates, key=len, default="")
    else:
        candidates = re.findall(r"<(?:section|div)\b[\s\S]*?</(?:section|div)>", reduced, flags=re.I)
        block_html = max(candidates, key=len, default=reduced)

    heading_count = len(re.findall(r"<h[1-6]\b", block_html, flags=re.I))
    text = re.sub(r"<[^>]+>", " ", block_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 200:
        text = re.sub(r"<[^>]+>", " ", reduced)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        heading_count = len(re.findall(r"<h[1-6]\b", reduced, flags=re.I))
    if not text:
        raise RuntimeError("html_text_empty")
    return text, max(1, heading_count)


def classify_html_artifact(*, html_text: str, url: str | None = None) -> HtmlArtifactQuality:
    lowered = html_text.lower()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.I | re.S)
    title_text = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip().lower() if title_match else ""
    text, _ = extract_html_text(html_text)
    normalized_text = text.lower()

    for pattern in _INVALID_TITLE_PATTERNS:
        if title_text and re.search(pattern, title_text, flags=re.I):
            reason = "html_placeholder_page" if "sci-hub" in pattern else "html_redirect_page" if "redirecting" in pattern else "html_login_or_challenge"
            return HtmlArtifactQuality(False, "html_invalid", reason, text, 0.0)
    for pattern, reason in _INVALID_BODY_PATTERNS:
        if re.search(pattern, normalized_text[:6000], flags=re.I):
            return HtmlArtifactQuality(False, "html_invalid", reason, text, 0.0)

    score = 0.0
    if url and any(host in (url or "").lower() for host in ("ncbi.nlm.nih.gov", "arxiv.org", "doi.org")):
        score += 0.6
    if len(text) >= 3000:
        score += 1.2
    elif len(text) >= 1600:
        score += 0.8
    elif len(text) >= 800:
        score += 0.4
    for pattern, weight in _ARTICLE_SIGNAL_PATTERNS:
        if re.search(pattern, normalized_text, flags=re.I):
            score += weight
    if re.search(r"(citation_title|citation_author|dc\.title|dc\.creator)", lowered, flags=re.I):
        score += 0.8
    if re.search(r"<(?:article|main)\b", lowered, flags=re.I):
        score += 0.6

    if len(text) < 800 and score < 2.0:
        return HtmlArtifactQuality(False, "html_invalid", "html_body_too_short", text, score)
    if score < 2.4:
        return HtmlArtifactQuality(False, "html_invalid", "html_missing_article_signals", text, score)
    return HtmlArtifactQuality(True, "html_validated", "publisher_fulltext", text, score)
