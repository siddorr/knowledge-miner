from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import re
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import AcquisitionRun, Artifact, DocumentChunk, ParseRun, ParsedDocument, Source


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

    run = ParseRun(
        id=f"parse_{uuid.uuid4().hex[:12]}",
        acq_run_id=acq_run_id,
        retry_failed_only=retry_failed_only,
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
                text, parser_used = _extract_artifact_text(artifact)
                chunks = _chunk_text(text)
                doc.status = "parsed"
                doc.body_text = text
                doc.language = "unknown"
                doc.parser_used = parser_used
                doc.char_count = len(text)
                doc.section_count = 1
                doc.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                doc.last_error = None
                parsed_total += 1
                chunked_total += len(chunks)
                for idx, (chunk_text, start, end) in enumerate(chunks):
                    db.add(
                        DocumentChunk(
                            id=f"chunk_{uuid.uuid4().hex[:12]}",
                            parse_run_id=run_id,
                            parsed_document_id=doc.id,
                            chunk_index=idx,
                            text=chunk_text,
                            start_char=start,
                            end_char=end,
                            content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                        )
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


def _extract_artifact_text(artifact: Artifact) -> tuple[str, str]:
    path = Path(settings.artifacts_dir) / artifact.path
    if not path.exists():
        raise FileNotFoundError("artifact_file_missing")
    if artifact.kind == "html":
        html = path.read_text(encoding="utf-8", errors="ignore")
        return _extract_html_text(html), "html_simple"
    if artifact.kind == "pdf":
        # Minimal fallback parser for now; dedicated PDF parsing improvements are part of Phase 3 task 3.
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="ignore")
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            raise RuntimeError("pdf_text_empty")
        return cleaned, "pdf_naive"
    raise RuntimeError("unsupported_artifact_kind")


def _extract_html_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise RuntimeError("html_text_empty")
    return text


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
