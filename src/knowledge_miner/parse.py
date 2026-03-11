from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import html
from pathlib import Path
import re
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_filter import AIRelevanceFilter, describe_ai_filter_runtime
from .config import settings
from .db import SessionLocal
from .models import AcquisitionRun, Artifact, DocumentChunk, ParseRun, ParsedDocument, Source
from .scoring import decision_from_score, score_text


def create_parse_run(db: Session, acq_run_id: str, *, retry_failed_only: bool) -> ParseRun:
    acq_run = db.get(AcquisitionRun, acq_run_id)
    if acq_run is None:
        raise ValueError("run_not_found")
    if acq_run.status != "completed":
        raise RuntimeError("run_not_complete")

    artifacts = db.scalars(select(Artifact).where(Artifact.acq_run_id == acq_run_id).order_by(Artifact.id.asc())).all()
    selected_artifacts = artifacts
    if retry_failed_only:
        previous = db.scalars(
            select(ParseRun).where(ParseRun.acq_run_id == acq_run_id).order_by(ParseRun.created_at.desc(), ParseRun.id.desc())
        ).first()
        if previous is not None:
            failed_artifact_ids = set(
                db.scalars(
                    select(ParsedDocument.artifact_id).where(
                        ParsedDocument.parse_run_id == previous.id,
                        ParsedDocument.status == "failed",
                    )
                ).all()
            )
            selected_artifacts = [a for a in artifacts if a.id in failed_artifact_ids]
        else:
            selected_artifacts = []

    ai_filter_active, ai_filter_warning = describe_ai_filter_runtime(
        use_ai_filter=settings.use_ai_filter,
        api_key=settings.ai_api_key,
    )
    run = ParseRun(
        id=f"parse_{uuid.uuid4().hex[:12]}",
        acq_run_id=acq_run_id,
        retry_failed_only=retry_failed_only,
        ai_filter_active=ai_filter_active,
        ai_filter_warning=ai_filter_warning,
        status="queued",
        total_documents=len(selected_artifacts),
        parsed_total=0,
        failed_total=0,
        chunked_total=0,
    )
    db.add(run)
    db.flush()

    for artifact in selected_artifacts:
        source = db.get(Source, artifact.source_id)
        db.add(
            ParsedDocument(
                id=f"doc_{uuid.uuid4().hex[:12]}",
                parse_run_id=run.id,
                source_id=artifact.source_id,
                artifact_id=artifact.id,
                status="queued",
                title=source.title if source is not None else None,
                publication_year=source.year if source is not None else None,
            )
        )

    db.commit()
    db.refresh(run)
    return run


def enqueue_parse_run(parse_run_id: str) -> None:
    worker = threading.Thread(target=execute_parse_run_by_id, args=(parse_run_id,), daemon=True)
    worker.start()


def execute_parse_run_by_id(parse_run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(ParseRun, parse_run_id)
        if run is None:
            return
        execute_parse_run(db, run)


def execute_parse_run(db: Session, run: ParseRun) -> None:
    run_id = run.id
    ai_filter = AIRelevanceFilter()
    try:
        run.status = "running"
        db.commit()

        docs = db.scalars(select(ParsedDocument).where(ParsedDocument.parse_run_id == run_id).order_by(ParsedDocument.id.asc())).all()
        parsed_total = 0
        failed_total = 0
        chunked_total = 0
        for doc in docs:
            artifact = db.get(Artifact, doc.artifact_id)
            if artifact is None:
                doc.status = "failed"
                doc.last_error = "artifact_not_found"
                failed_total += 1
                continue
            try:
                text, parser_used, section_count = _extract_artifact_text(artifact)
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                cached_doc = _find_cached_parsed_document(
                    db,
                    acq_run_id=run.acq_run_id,
                    source_id=doc.source_id,
                    content_hash=content_hash,
                    exclude_parse_run_id=run_id,
                )
                chunks = _chunk_text(text)
                doc.status = "parsed"
                doc.body_text = text
                doc.language = "unknown"
                doc.parser_used = "cached_chunks" if cached_doc is not None else parser_used
                doc.char_count = len(text)
                doc.section_count = section_count
                doc.content_hash = content_hash
                doc.last_error = None
                doc_score, doc_decision, doc_confidence, doc_reason = _classify_text(
                    title=doc.title or "",
                    text=text[:6000],
                    ai_filter=ai_filter,
                )
                doc.relevance_score = doc_score
                doc.decision = doc_decision
                doc.confidence = doc_confidence
                doc.reason = doc_reason
                parsed_total += 1
                if cached_doc is not None:
                    chunked_total += _copy_chunks_from_cached_document(db, run_id=run_id, target_doc_id=doc.id, cached_doc_id=cached_doc.id)
                else:
                    chunked_total += _persist_chunks_for_document(
                        db,
                        run_id=run_id,
                        parsed_document_id=doc.id,
                        title=doc.title or "",
                        chunks=chunks,
                        ai_filter=ai_filter,
                    )
            except Exception as exc:
                doc.status = "failed"
                doc.last_error = str(exc)
                failed_total += 1
            doc.updated_at = datetime.now(UTC)

        db_run = db.get(ParseRun, run_id)
        if db_run is None:
            return
        db_run.parsed_total = parsed_total
        db_run.failed_total = failed_total
        db_run.chunked_total = chunked_total
        db_run.status = "completed"
        db_run.updated_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:  # pragma: no cover
        db.rollback()
        db_run = db.get(ParseRun, run_id)
        if db_run is not None:
            db_run.status = "failed"
            db_run.error_message = str(exc)
            db_run.updated_at = datetime.now(UTC)
            db.commit()
        raise


def _extract_artifact_text(artifact: Artifact) -> tuple[str, str, int]:
    path = Path(settings.artifacts_dir) / artifact.path
    if not path.exists():
        raise FileNotFoundError("artifact_file_missing")
    if artifact.kind == "html":
        html = path.read_text(encoding="utf-8", errors="ignore")
        text, sections = _extract_html_text(html)
        return text, "html_readability_heuristic", sections
    if artifact.kind == "pdf":
        text, parser_used = _extract_pdf_text(path)
        return text, parser_used, _estimate_section_count(text)
    raise RuntimeError("unsupported_artifact_kind")


def _extract_pdf_text(path: Path) -> tuple[str, str]:
    # Deterministic parser order: pypdf -> byte decode fallback.
    text = _extract_pdf_text_pypdf(path)
    if text:
        return text, "pdf_pypdf"
    raw = path.read_bytes()
    cleaned = re.sub(r"\s+", " ", raw.decode("utf-8", errors="ignore")).strip()
    if cleaned:
        return cleaned, "pdf_naive"
    cleaned_latin = re.sub(r"\s+", " ", raw.decode("latin-1", errors="ignore")).strip()
    if cleaned_latin:
        return cleaned_latin, "pdf_naive_latin1"
    raise RuntimeError("pdf_text_empty")


def _extract_pdf_text_pypdf(path: Path) -> str | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return None
    try:
        reader = PdfReader(str(path))
    except Exception:
        return None
    page_texts: list[str] = []
    for page in reader.pages:
        try:
            extracted = page.extract_text() or ""
        except Exception:
            extracted = ""
        cleaned = re.sub(r"\s+", " ", extracted).strip()
        if cleaned:
            page_texts.append(cleaned)
    if not page_texts:
        return None
    merged = "\n\n".join(page_texts).strip()
    return merged or None


def _extract_html_text(html_text: str) -> tuple[str, int]:
    # Remove clearly non-content regions.
    reduced = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    reduced = re.sub(r"<style[\s\S]*?</style>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<noscript[\s\S]*?</noscript>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<nav[\s\S]*?</nav>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<header[\s\S]*?</header>", " ", reduced, flags=re.I)
    reduced = re.sub(r"<footer[\s\S]*?</footer>", " ", reduced, flags=re.I)

    preferred_blocks = re.findall(r"<(article|main)\b[\s\S]*?</\1>", reduced, flags=re.I)
    block_html = ""
    if preferred_blocks:
        # If any explicit content block exists, prefer the first longest one.
        candidates = re.findall(r"<(?:article|main)\b[\s\S]*?</(?:article|main)>", reduced, flags=re.I)
        block_html = max(candidates, key=len, default="")
    else:
        # Fallback: choose longest section/div block as readability-like heuristic.
        candidates = re.findall(r"<(?:section|div)\b[\s\S]*?</(?:section|div)>", reduced, flags=re.I)
        block_html = max(candidates, key=len, default=reduced)

    heading_count = len(re.findall(r"<h[1-6]\b", block_html, flags=re.I))
    text = re.sub(r"<[^>]+>", " ", block_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 200:
        # Fallback to full-page extraction when chosen block is too small.
        text = re.sub(r"<[^>]+>", " ", reduced)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        heading_count = len(re.findall(r"<h[1-6]\b", reduced, flags=re.I))
    if not text:
        raise RuntimeError("html_text_empty")
    return text, max(1, heading_count)


def _estimate_section_count(text: str) -> int:
    paragraph_like = len([p for p in text.split("\n\n") if p.strip()])
    if paragraph_like > 0:
        return paragraph_like
    return 1


def _chunk_text(text: str, *, target_size: int = 1200, overlap: int = 200) -> list[tuple[str, int, int]]:
    if len(text) <= target_size:
        return [(text, 0, len(text))]
    chunks: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + target_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append((chunk, start, end))
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _deterministic_chunk_id(*, parsed_document_id: str, chunk_index: int, chunk_content_hash: str) -> str:
    base = f"{parsed_document_id}|{chunk_index}|{chunk_content_hash}"
    return f"chunk_{hashlib.sha1(base.encode('utf-8')).hexdigest()[:20]}"


def _persist_chunks_for_document(
    db: Session,
    *,
    run_id: str,
    parsed_document_id: str,
    title: str,
    chunks: list[tuple[str, int, int]],
    ai_filter: AIRelevanceFilter,
) -> int:
    for idx, (chunk_text, start, end) in enumerate(chunks):
        chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        score, decision, confidence, reason = _classify_text(title=title, text=chunk_text, ai_filter=ai_filter)
        db.add(
            DocumentChunk(
                id=_deterministic_chunk_id(
                    parsed_document_id=parsed_document_id,
                    chunk_index=idx,
                    chunk_content_hash=chunk_hash,
                ),
                parse_run_id=run_id,
                parsed_document_id=parsed_document_id,
                chunk_index=idx,
                text=chunk_text,
                relevance_score=score,
                decision=decision,
                confidence=confidence,
                reason=reason,
                start_char=start,
                end_char=end,
                content_hash=chunk_hash,
            )
        )
    return len(chunks)


def _find_cached_parsed_document(
    db: Session,
    *,
    acq_run_id: str,
    source_id: str,
    content_hash: str,
    exclude_parse_run_id: str,
) -> ParsedDocument | None:
    return db.scalars(
        select(ParsedDocument)
        .join(ParseRun, ParseRun.id == ParsedDocument.parse_run_id)
        .where(
            ParseRun.acq_run_id == acq_run_id,
            ParsedDocument.source_id == source_id,
            ParsedDocument.content_hash == content_hash,
            ParsedDocument.status == "parsed",
            ParsedDocument.parse_run_id != exclude_parse_run_id,
        )
        .order_by(ParsedDocument.updated_at.desc(), ParsedDocument.id.desc())
    ).first()


def _copy_chunks_from_cached_document(
    db: Session,
    *,
    run_id: str,
    target_doc_id: str,
    cached_doc_id: str,
) -> int:
    cached_chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.parsed_document_id == cached_doc_id)
        .order_by(DocumentChunk.chunk_index.asc())
    ).all()
    for chunk in cached_chunks:
        db.add(
            DocumentChunk(
                id=_deterministic_chunk_id(
                    parsed_document_id=target_doc_id,
                    chunk_index=chunk.chunk_index,
                    chunk_content_hash=chunk.content_hash,
                ),
                parse_run_id=run_id,
                parsed_document_id=target_doc_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                relevance_score=chunk.relevance_score,
                decision=chunk.decision,
                confidence=chunk.confidence,
                reason=chunk.reason,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                content_hash=chunk.content_hash,
            )
        )
    return len(cached_chunks)


def _classify_text(*, title: str, text: str, ai_filter: AIRelevanceFilter) -> tuple[float, str, float, str]:
    score = score_text(title, text)
    _, base_decision = decision_from_score(score)
    decision = base_decision
    confidence = _heuristic_confidence(score=score, decision=base_decision)
    reason = "heuristic_score"

    ai_result = ai_filter.evaluate(title=title, abstract=text, base_score=score, base_decision=base_decision)
    if ai_result is not None and ai_result.confidence >= settings.ai_min_confidence_override:
        decision = ai_result.decision
        confidence = float(ai_result.confidence)
        reason = f"ai_override:{ai_result.reason or 'no_reason'}"
    return score, decision, confidence, reason


def _heuristic_confidence(*, score: float, decision: str) -> float:
    if decision == "auto_accept":
        value = 0.7 + min(0.29, max(0.0, (score - 5.0) * 0.03))
        return round(value, 3)
    if decision == "auto_reject":
        value = 0.7 + min(0.29, max(0.0, (3.0 - score) * 0.05))
        return round(value, 3)
    return 0.55
