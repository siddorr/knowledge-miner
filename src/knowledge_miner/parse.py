from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .artifact_quality import classify_html_artifact, extract_html_text
from .ai_filter import AIRelevanceFilter, describe_ai_filter_runtime
from .config import settings
from .db import SessionLocal
from .models import AcquisitionRun, Artifact, DocumentChunk, ParseRun, ParsedDocument, Run, Source
from .observability import ParseObservability
from .runtime_state import acquire_run_lock, is_primary_instance, release_run_lock
from .scoring import decision_from_score, score_text

logger = logging.getLogger("knowledge_miner")
_PYPDF_MISSING_LOGGED = False


def _selected_artifacts_for_parse(
    db: Session,
    acq_run_id: str,
    *,
    retry_failed_only: bool,
    unparsed_only: bool = False,
) -> list[Artifact]:
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
    if unparsed_only:
        parsed_artifact_ids = set(
            db.scalars(
                select(ParsedDocument.artifact_id)
                .join(ParseRun, ParseRun.id == ParsedDocument.parse_run_id)
                .where(
                    ParseRun.acq_run_id == acq_run_id,
                    ParsedDocument.status == "parsed",
                )
            ).all()
        )
        selected_artifacts = [artifact for artifact in selected_artifacts if artifact.id not in parsed_artifact_ids]
    return selected_artifacts


def _create_parse_run_for_artifacts(
    db: Session,
    acq_run_id: str,
    *,
    retry_failed_only: bool,
    selected_artifacts: list[Artifact],
) -> ParseRun:
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
    return run


def create_parse_run(db: Session, acq_run_id: str, *, retry_failed_only: bool) -> ParseRun:
    acq_run = db.get(AcquisitionRun, acq_run_id)
    if acq_run is None:
        raise ValueError("run_not_found")
    if acq_run.status != "completed":
        raise RuntimeError("run_not_complete")
    active_existing = db.scalars(
        select(ParseRun)
        .where(
            ParseRun.acq_run_id == acq_run_id,
            ParseRun.retry_failed_only.is_(retry_failed_only),
            ParseRun.status.in_(("queued", "running")),
        )
        .order_by(ParseRun.created_at.desc(), ParseRun.id.desc())
    ).first()
    if active_existing is not None:
        return active_existing

    selected_artifacts = _selected_artifacts_for_parse(db, acq_run_id, retry_failed_only=retry_failed_only)
    run = _create_parse_run_for_artifacts(
        db,
        acq_run_id,
        retry_failed_only=retry_failed_only,
        selected_artifacts=selected_artifacts,
    )
    db.commit()
    db.refresh(run)
    return run


def create_parse_runs_for_session_downloads(db: Session, session_id: str) -> list[ParseRun]:
    session_run_ids = db.scalars(select(Run.id).where(Run.session_id == session_id)).all()
    if not session_run_ids:
        return []
    acq_runs = db.scalars(
        select(AcquisitionRun)
        .where(
            AcquisitionRun.discovery_run_id.in_(session_run_ids),
            select(Artifact.id).where(Artifact.acq_run_id == AcquisitionRun.id).exists(),
        )
        .order_by(AcquisitionRun.created_at.asc(), AcquisitionRun.id.asc())
    ).all()
    queued_runs: list[ParseRun] = []
    for acq_run in acq_runs:
        active_existing = db.scalars(
            select(ParseRun)
            .where(
                ParseRun.acq_run_id == acq_run.id,
                ParseRun.retry_failed_only.is_(False),
                ParseRun.status.in_(("queued", "running")),
            )
            .order_by(ParseRun.created_at.desc(), ParseRun.id.desc())
        ).first()
        if active_existing is not None:
            queued_runs.append(active_existing)
            continue
        selected_artifacts = _selected_artifacts_for_parse(
            db,
            acq_run.id,
            retry_failed_only=False,
            unparsed_only=True,
        )
        if not selected_artifacts:
            continue
        queued_runs.append(
            _create_parse_run_for_artifacts(
                db,
                acq_run.id,
                retry_failed_only=False,
                selected_artifacts=selected_artifacts,
            )
        )
    db.commit()
    for run in queued_runs:
        db.refresh(run)
    return queued_runs


def enqueue_parse_run(parse_run_id: str) -> None:
    if not is_primary_instance():
        return
    run_lock = acquire_run_lock(base_dir=settings.runtime_state_dir, phase="parse", run_id=parse_run_id)
    if run_lock is None:
        return
    worker = threading.Thread(target=_execute_parse_run_with_lock, args=(parse_run_id, run_lock), daemon=True)
    worker.start()


def _execute_parse_run_with_lock(parse_run_id: str, run_lock: Path) -> None:
    try:
        execute_parse_run_by_id(parse_run_id)
    finally:
        release_run_lock(run_lock)


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
        run.error_message = None
        run.updated_at = datetime.now(UTC)
        db.commit()

        doc_ids = db.scalars(
            select(ParsedDocument.id)
            .where(ParsedDocument.parse_run_id == run_id, ParsedDocument.status == "queued")
            .order_by(ParsedDocument.id.asc())
        ).all()
        for doc_id in doc_ids:
            started = time.perf_counter()
            doc = db.get(ParsedDocument, doc_id)
            if doc is None or doc.status != "queued":
                continue
            artifact_id = doc.artifact_id
            try:
                artifact = db.get(Artifact, doc.artifact_id)
                if artifact is None:
                    raise FileNotFoundError("artifact_not_found")
                text, parser_used, section_count = _extract_artifact_text(artifact)
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                cached_doc = _find_cached_parsed_document(
                    db,
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
                if cached_doc is not None:
                    chunk_count = _copy_chunks_from_cached_document(
                        db,
                        run_id=run_id,
                        target_doc_id=doc.id,
                        cached_doc_id=cached_doc.id,
                    )
                else:
                    chunk_count = _persist_chunks_for_document(
                        db,
                        run_id=run_id,
                        parsed_document_id=doc.id,
                        title=doc.title or "",
                        chunks=chunks,
                        ai_filter=ai_filter,
                    )
                observability.inc("chunked_chunks", chunk_count)
                doc.updated_at = datetime.now(UTC)
                _refresh_parse_run_progress(db, run_id)
                db.commit()
                observability.inc("parsed_documents")
                observability.record_document(
                    parse_run_id=run_id,
                    document_id=doc.id,
                    artifact_id=artifact_id,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status="parsed",
                    parser_used=doc.parser_used,
                    chunks=chunk_count,
                )
            except Exception as exc:
                db.rollback()
                doc = db.get(ParsedDocument, doc_id)
                if doc is None:
                    continue
                doc.status = "failed"
                doc.body_text = None
                doc.language = None
                doc.parser_used = None
                doc.char_count = 0
                doc.section_count = 0
                doc.content_hash = None
                doc.relevance_score = None
                doc.decision = None
                doc.confidence = None
                doc.reason = None
                doc.last_error = str(exc)
                doc.updated_at = datetime.now(UTC)
                _refresh_parse_run_progress(db, run_id)
                db.commit()
                observability.inc("failed_documents")
                observability.record_document(
                    parse_run_id=run_id,
                    document_id=doc.id,
                    artifact_id=artifact_id,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status="failed",
                    error=str(exc),
                )

        db_run = _refresh_parse_run_progress(db, run_id)
        if db_run is None:
            return
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


def _refresh_parse_run_progress(db: Session, run_id: str) -> ParseRun | None:
    db_run = db.get(ParseRun, run_id)
    if db_run is None:
        return None
    db_run.parsed_total = int(
        db.scalar(
            select(func.count()).select_from(ParsedDocument).where(
                ParsedDocument.parse_run_id == run_id,
                ParsedDocument.status == "parsed",
            )
        )
        or 0
    )
    db_run.failed_total = int(
        db.scalar(
            select(func.count()).select_from(ParsedDocument).where(
                ParsedDocument.parse_run_id == run_id,
                ParsedDocument.status == "failed",
            )
        )
        or 0
    )
    db_run.chunked_total = int(
        db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.parse_run_id == run_id)) or 0
    )
    db_run.updated_at = datetime.now(UTC)
    return db_run


def resume_queued_parse_runs() -> list[str]:
    resumed: list[str] = []
    with SessionLocal() as db:
        runs = db.scalars(
            select(ParseRun)
            .where(ParseRun.status.in_(("queued", "running")))
            .order_by(ParseRun.acq_run_id.asc(), ParseRun.retry_failed_only.asc(), ParseRun.created_at.desc(), ParseRun.id.desc())
        ).all()
        active_by_key: dict[tuple[str, bool], ParseRun] = {}
        for run in runs:
            key = (run.acq_run_id, bool(run.retry_failed_only))
            kept = active_by_key.get(key)
            if kept is not None:
                run.status = "failed"
                run.error_message = f"superseded_by_parse_run:{kept.id}"
                run.updated_at = datetime.now(UTC)
                continue
            active_by_key[key] = run
            remaining_docs = int(
                db.scalar(
                    select(func.count()).select_from(ParsedDocument).where(
                        ParsedDocument.parse_run_id == run.id,
                        ParsedDocument.status == "queued",
                    )
                )
                or 0
            )
            if remaining_docs <= 0:
                if run.status != "completed":
                    run.status = "completed"
                    run.updated_at = datetime.now(UTC)
                continue
            if run.status == "running":
                run.status = "queued"
                run.updated_at = datetime.now(UTC)
            resumed.append(run.id)
        db.commit()
    for run_id in resumed:
        enqueue_parse_run(run_id)
    return resumed


def _extract_artifact_text(artifact: Artifact) -> tuple[str, str, int]:
    path = Path(settings.artifacts_dir) / artifact.path
    if not path.exists():
        raise FileNotFoundError("artifact_file_missing")
    if artifact.kind == "html":
        html = path.read_text(encoding="utf-8", errors="ignore")
        quality_status = (artifact.quality_status or "").strip()
        quality_reason = (artifact.quality_reason or "").strip()
        if quality_status == "html_invalid":
            raise RuntimeError(f"html_invalid:{quality_reason or 'html_missing_article_signals'}")
        if not quality_status:
            quality = classify_html_artifact(html_text=html)
            if not quality.accepted:
                raise RuntimeError(f"html_invalid:{quality.reason}")
        text, sections = extract_html_text(html)
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
    global _PYPDF_MISSING_LOGGED
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        if not _PYPDF_MISSING_LOGGED:
            logger.warning("pdf_text_extraction_unavailable parser=pypdf reason=module_not_installed")
            _PYPDF_MISSING_LOGGED = True
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
    content_hash: str,
    exclude_parse_run_id: str,
) -> ParsedDocument | None:
    return db.scalars(
        select(ParsedDocument)
        .where(
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
