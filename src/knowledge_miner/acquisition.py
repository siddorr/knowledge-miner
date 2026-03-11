from __future__ import annotations

from datetime import UTC, datetime
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import AcquisitionItem, AcquisitionRun, Artifact, Run, Source


def create_acquisition_run(db: Session, discovery_run_id: str, *, retry_failed_only: bool) -> AcquisitionRun:
    run = db.get(Run, discovery_run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run.status != "completed":
        raise RuntimeError("run_not_complete")

    accepted_sources = db.scalars(
        select(Source).where(Source.run_id == discovery_run_id, Source.accepted.is_(True)).order_by(Source.id.asc())
    ).all()
    acq_run = AcquisitionRun(
        id=f"acq_{uuid.uuid4().hex[:12]}",
        discovery_run_id=discovery_run_id,
        retry_failed_only=retry_failed_only,
        status="queued",
        total_sources=len(accepted_sources),
        downloaded_total=0,
        partial_total=0,
        failed_total=0,
        skipped_total=0,
    )
    db.add(acq_run)
    db.flush()

    for source in accepted_sources:
        db.add(
            AcquisitionItem(
                id=f"acq_item_{uuid.uuid4().hex[:12]}",
                acq_run_id=acq_run.id,
                source_id=source.id,
                status="queued",
                attempt_count=0,
                selected_url=source.url,
                last_error=None,
            )
        )

    db.commit()
    db.refresh(acq_run)
    return acq_run


def enqueue_acquisition_run(acq_run_id: str) -> None:
    worker = threading.Thread(target=execute_acquisition_run_by_id, args=(acq_run_id,), daemon=True)
    worker.start()


def execute_acquisition_run_by_id(acq_run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(AcquisitionRun, acq_run_id)
        if run is None:
            return
        execute_acquisition_run(db, run)


def execute_acquisition_run(db: Session, run: AcquisitionRun) -> None:
    try:
        run.status = "running"
        run.updated_at = datetime.now(UTC)
        db.commit()

        items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == run.id)).all()
        # Phase 2 task 1 scaffolding only; download engine is implemented in task 3.
        for item in items:
            item.status = "skipped"
            item.attempt_count = max(1, item.attempt_count)
            item.last_error = "download_engine_not_implemented_yet"
            item.updated_at = datetime.now(UTC)

        run.downloaded_total = 0
        run.partial_total = 0
        run.failed_total = 0
        run.skipped_total = len(items)
        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:  # pragma: no cover
        db.rollback()
        run.status = "failed"
        run.error_message = str(exc)
        run.updated_at = datetime.now(UTC)
        db.commit()
        raise


def build_manifest_payload(db: Session, acq_run_id: str) -> dict:
    run = db.get(AcquisitionRun, acq_run_id)
    if run is None:
        raise ValueError("acq_run_not_found")

    items = db.scalars(
        select(AcquisitionItem).where(AcquisitionItem.acq_run_id == acq_run_id).order_by(AcquisitionItem.source_id.asc())
    ).all()
    artifacts = db.scalars(select(Artifact).where(Artifact.acq_run_id == acq_run_id).order_by(Artifact.id.asc())).all()

    return {
        "acq_run_id": run.id,
        "discovery_run_id": run.discovery_run_id,
        "status": run.status,
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": {
            "total_sources": run.total_sources,
            "downloaded_total": run.downloaded_total,
            "partial_total": run.partial_total,
            "failed_total": run.failed_total,
            "skipped_total": run.skipped_total,
        },
        "items": [
            {
                "item_id": i.id,
                "source_id": i.source_id,
                "status": i.status,
                "attempt_count": i.attempt_count,
                "selected_url": i.selected_url,
                "last_error": i.last_error,
            }
            for i in items
        ],
        "artifacts": [
            {
                "artifact_id": a.id,
                "source_id": a.source_id,
                "item_id": a.item_id,
                "kind": a.kind,
                "path": a.path,
                "checksum_sha256": a.checksum_sha256,
                "size_bytes": a.size_bytes,
                "mime_type": a.mime_type,
            }
            for a in artifacts
        ],
    }
