from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import html
import json
from pathlib import Path
import re
import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_filter import AIRelevanceFilter, describe_ai_filter_runtime
from .config import settings
from .db import SessionLocal
from .models import AcquisitionRun, Artifact, DocumentChunk, ParseRun, ParsedDocument, Source
from .observability import ParseObservability
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
    observability = ParseObservability()
    try:
        run.status = "running"
        db.commit()

        docs = db.scalars(select(ParsedDocument).where(ParsedDocument.parse_run_id == run_id).order_by(ParsedDocument.id.asc())).all()
        parsed_total = 0
        failed_total = 0
        chunked_total = 0
        for doc in docs:
            started = time.perf_counter()
            artifact = db.get(Artifact, doc.artifact_id)
            if artifact is None:
                doc.status = "failed"
                doc.last_error = "artifact_not_found"
                failed_total += 1
                observability.inc("failed_documents")
                observability.record_document(
                    parse_run_id=run_id,
                    document_id=doc.id,
                    artifact_id=doc.artifact_id,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status="failed",
                    error="artifact_not_found",
                )
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
                observability.inc("parsed_documents")
                if cached_doc is not None:
                    chunk_count = _copy_chunks_from_cached_document(
                        db,
                        run_id=run_id,
                        target_doc_id=doc.id,
                        cached_doc_id=cached_doc.id,
                    )
                    chunked_total += chunk_count
                else:
                    chunk_count = _persist_chunks_for_document(
                        db,
                        run_id=run_id,
                        parsed_document_id=doc.id,
                        title=doc.title or "",
                        chunks=chunks,
                        ai_filter=ai_filter,
                    )
                    chunked_total += chunk_count
                observability.inc("chunked_chunks", chunk_count)
                observability.record_document(
                    parse_run_id=run_id,
                    document_id=doc.id,
                    artifact_id=doc.artifact_id,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status="parsed",
                    parser_used=doc.parser_used,
                    chunks=chunk_count,
                )
            except Exception as exc:
                doc.status = "failed"
                doc.last_error = str(exc)
                failed_total += 1
                observability.inc("failed_documents")
                observability.record_document(
                    parse_run_id=run_id,
                    document_id=doc.id,
                    artifact_id=doc.artifact_id,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status="failed",
                    error=str(exc),
                )
            doc.updated_at = datetime.now(UTC)

        db_run = db.get(ParseRun, run_id)
        if db_run is None:
            return
        db_run.parsed_total = parsed_total
        db_run.failed_total = failed_total
        db_run.chunked_total = chunked_total
        db_run.status = "completed"
        db_run.updated_at = datetime.now(UTC)
        indexing_started = time.perf_counter()
        artifact_stats = _write_parse_output_artifacts(db, db_run)
        observability.inc("indexed_documents", artifact_stats["indexed_documents"])
        observability.inc("indexed_chunks", artifact_stats["indexed_chunks"])
        observability.inc("findings_total", artifact_stats["findings_total"])
        observability.record_indexing(
            parse_run_id=run_id,
            latency_ms=(time.perf_counter() - indexing_started) * 1000.0,
            status="completed",
            indexed_documents=artifact_stats["indexed_documents"],
            indexed_chunks=artifact_stats["indexed_chunks"],
        )
        db.commit()
        observability.emit_summary(parse_run_id=run_id, status="completed")
    except Exception as exc:  # pragma: no cover
        db.rollback()
        db_run = db.get(ParseRun, run_id)
        if db_run is not None:
            db_run.status = "failed"
            db_run.error_message = str(exc)
            db_run.updated_at = datetime.now(UTC)
            db.commit()
        observability.record_indexing(
            parse_run_id=run_id,
            latency_ms=0.0,
            status="failed",
            indexed_documents=0,
            indexed_chunks=0,
            error=str(exc),
        )
        observability.emit_summary(parse_run_id=run_id, status="failed")
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


def _write_parse_output_artifacts(db: Session, run: ParseRun) -> dict[str, int]:
    db.flush()
    docs = db.scalars(
        select(ParsedDocument).where(ParsedDocument.parse_run_id == run.id).order_by(ParsedDocument.id.asc())
    ).all()
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.parse_run_id == run.id)
        .order_by(DocumentChunk.parsed_document_id.asc(), DocumentChunk.chunk_index.asc(), DocumentChunk.id.asc())
    ).all()
    artifacts = db.scalars(select(Artifact).where(Artifact.acq_run_id == run.acq_run_id).order_by(Artifact.id.asc())).all()

    chunks_by_doc: dict[str, list[DocumentChunk]] = {}
    for chunk in chunks:
        chunks_by_doc.setdefault(chunk.parsed_document_id, []).append(chunk)

    generated_at = run.updated_at.isoformat() if run.updated_at else datetime.now(UTC).isoformat()
    parsed_corpus_payload = {
        "schema_version": "1.0",
        "parse_run_id": run.id,
        "acq_run_id": run.acq_run_id,
        "generated_at": generated_at,
        "totals": {
            "documents": len(docs),
            "parsed_documents": sum(1 for d in docs if d.status == "parsed"),
            "failed_documents": sum(1 for d in docs if d.status == "failed"),
            "chunks": len(chunks),
        },
        "documents": [
            {
                "document_id": doc.id,
                "source_id": doc.source_id,
                "artifact_id": doc.artifact_id,
                "status": doc.status,
                "title": doc.title,
                "publication_year": doc.publication_year,
                "language": doc.language,
                "parser_used": doc.parser_used,
                "char_count": doc.char_count,
                "section_count": doc.section_count,
                "content_hash": doc.content_hash,
                "relevance_score": float(doc.relevance_score) if doc.relevance_score is not None else None,
                "decision": doc.decision,
                "confidence": float(doc.confidence) if doc.confidence is not None else None,
                "reason": doc.reason,
                "last_error": doc.last_error,
                "body_text": doc.body_text,
                "chunks": [
                    {
                        "chunk_id": chunk.id,
                        "chunk_index": chunk.chunk_index,
                        "start_char": chunk.start_char,
                        "end_char": chunk.end_char,
                        "content_hash": chunk.content_hash,
                        "relevance_score": float(chunk.relevance_score) if chunk.relevance_score is not None else None,
                        "decision": chunk.decision,
                        "confidence": float(chunk.confidence) if chunk.confidence is not None else None,
                        "reason": chunk.reason,
                        "text": chunk.text,
                    }
                    for chunk in chunks_by_doc.get(doc.id, [])
                ],
            }
            for doc in docs
        ],
    }

    findings_payload = _build_findings_report_payload(run=run, docs=docs, chunks_by_doc=chunks_by_doc, generated_at=generated_at)
    search_manifest_payload = {
        "schema_version": "1.0",
        "parse_run_id": run.id,
        "acq_run_id": run.acq_run_id,
        "generated_at": generated_at,
        "index": {
            "engine": "naive_substring_v1",
            "document_order": "document_id_asc",
            "chunk_order": "parsed_document_id_asc,chunk_index_asc,chunk_id_asc",
            "document_count": len(docs),
            "chunk_count": len(chunks),
        },
        "db_counts": {
            "total_documents": run.total_documents,
            "parsed_total": run.parsed_total,
            "failed_total": run.failed_total,
            "chunked_total": run.chunked_total,
        },
        "artifact_entries": [
            {
                "artifact_id": artifact.id,
                "source_id": artifact.source_id,
                "kind": artifact.kind,
                "path": artifact.path,
                "checksum_sha256": artifact.checksum_sha256,
                "size_bytes": artifact.size_bytes,
                "mime_type": artifact.mime_type,
            }
            for artifact in artifacts
        ],
        "documents": [
            {
                "document_id": doc.id,
                "source_id": doc.source_id,
                "artifact_id": doc.artifact_id,
                "status": doc.status,
                "decision": doc.decision,
                "confidence": float(doc.confidence) if doc.confidence is not None else None,
                "content_hash": doc.content_hash,
                "chunk_count": len(chunks_by_doc.get(doc.id, [])),
            }
            for doc in docs
        ],
    }

    base_dir = Path(settings.artifacts_dir) / "parse" / run.id
    base_dir.mkdir(parents=True, exist_ok=True)
    _write_json_file(base_dir / "parsed_corpus.json", parsed_corpus_payload)
    _write_json_file(base_dir / "search_index_manifest.json", search_manifest_payload)
    _write_json_file(base_dir / "findings_report.json", findings_payload)
    return {
        "indexed_documents": len(docs),
        "indexed_chunks": len(chunks),
        "findings_total": findings_payload["summary"]["findings_total"],
    }


def _build_findings_report_payload(
    *,
    run: ParseRun,
    docs: list[ParsedDocument],
    chunks_by_doc: dict[str, list[DocumentChunk]],
    generated_at: str,
) -> dict:
    decision_priority = {"auto_accept": 0, "needs_review": 1, "auto_reject": 2, None: 3}
    findings: list[dict] = []
    for doc in docs:
        if doc.status != "parsed":
            continue
        if doc.decision not in {"auto_accept", "needs_review"}:
            continue
        ranked_chunks = sorted(
            chunks_by_doc.get(doc.id, []),
            key=lambda chunk: (
                decision_priority.get(chunk.decision, 3),
                -(float(chunk.relevance_score) if chunk.relevance_score is not None else 0.0),
                -(float(chunk.confidence) if chunk.confidence is not None else 0.0),
                chunk.chunk_index,
                chunk.id,
            ),
        )
        for chunk in ranked_chunks[:2]:
            finding_id = hashlib.sha1(f"{run.id}|{doc.id}|{chunk.id}".encode("utf-8")).hexdigest()[:20]
            findings.append(
                {
                    "finding_id": f"finding_{finding_id}",
                    "parse_run_id": run.id,
                    "acq_run_id": run.acq_run_id,
                    "document_id": doc.id,
                    "source_id": doc.source_id,
                    "artifact_id": doc.artifact_id,
                    "document_title": doc.title,
                    "document_decision": doc.decision,
                    "document_confidence": float(doc.confidence) if doc.confidence is not None else None,
                    "chunk_id": chunk.id,
                    "chunk_index": chunk.chunk_index,
                    "chunk_decision": chunk.decision,
                    "chunk_confidence": float(chunk.confidence) if chunk.confidence is not None else None,
                    "chunk_relevance_score": float(chunk.relevance_score) if chunk.relevance_score is not None else None,
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                    "snippet": chunk.text[:400],
                    "reason": chunk.reason,
                }
            )

    return {
        "schema_version": "1.0",
        "parse_run_id": run.id,
        "acq_run_id": run.acq_run_id,
        "generated_at": generated_at,
        "summary": {
            "eligible_documents": sum(
                1
                for doc in docs
                if doc.status == "parsed" and doc.decision in {"auto_accept", "needs_review"}
            ),
            "findings_total": len(findings),
        },
        "findings": findings,
    }


def _write_json_file(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
