from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..acquisition import find_reusable_artifact
from ..auth import require_api_key
from ..config import settings
from ..db import get_db
from ..models import AcquisitionItem, AcquisitionRun, Artifact, PaperAnnotation, Run, Source
from ..rate_limit import require_rate_limit

router = APIRouter(tags=["library_export"])


def _load_export_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return run


def _accepted_sources(db: Session, run_id: str, source_ids: list[str] | None) -> list[Source]:
    run = _load_export_run(db, run_id)
    if source_ids:
        wanted = {value for value in source_ids if value}
        filtered = db.scalars(
            select(Source)
            .join(Run, Run.id == Source.run_id)
            .where(
                Run.session_id == run.session_id,
                Source.accepted.is_(True),
                Source.id.in_(list(wanted)),
            )
            .order_by(Source.relevance_score.desc(), Source.id.asc())
        ).all()
    else:
        filtered = db.scalars(
            select(Source)
            .where(Source.run_id == run_id, Source.accepted.is_(True))
            .order_by(Source.relevance_score.desc(), Source.id.asc())
        ).all()
    if not filtered:
        raise HTTPException(status_code=404, detail="sources_not_found")
    return filtered


def _latest_acquisition_run(db: Session, run_id: str) -> AcquisitionRun | None:
    return db.scalars(
        select(AcquisitionRun)
        .where(AcquisitionRun.discovery_run_id == run_id)
        .order_by(AcquisitionRun.created_at.desc(), AcquisitionRun.id.desc())
        .limit(1)
    ).first()


def _effective_artifact_for_source(db: Session, source: Source) -> Artifact | None:
    artifact = db.scalars(
        select(Artifact)
        .where(Artifact.source_id == source.id)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1)
    ).first()
    if artifact is not None and _artifact_file_path(artifact.path).exists():
        return artifact
    return find_reusable_artifact(db, source)


def _source_link(source: Source) -> str:
    if source.doi:
        return f"https://doi.org/{source.doi}"
    return source.url or ""


def _artifact_file_path(path_value: str | None) -> Path:
    raw = (path_value or "").strip()
    path = Path(raw)
    if path.is_absolute():
        return path
    return Path(settings.artifacts_dir) / path


def _annotation_by_source(db: Session, session_id: str | None, source_ids: list[str]) -> dict[str, PaperAnnotation]:
    if not session_id or not source_ids:
        return {}
    rows = db.scalars(
        select(PaperAnnotation).where(
            PaperAnnotation.session_id == session_id,
            PaperAnnotation.source_id.in_(source_ids),
        )
    ).all()
    return {row.source_id: row for row in rows}


@router.get("/v1/library-export/runs/{run_id}/metadata.csv")
def export_library_metadata_csv(
    run_id: str,
    source_id: list[str] | None = Query(default=None),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> Response:
    _load_export_run(db, run_id)
    sources = _accepted_sources(db, run_id, source_id)
    run = _load_export_run(db, run_id)
    latest_acq = _latest_acquisition_run(db, run_id)
    item_status_by_source: dict[str, str] = {}
    if latest_acq is not None:
        items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == latest_acq.id)).all()
        item_status_by_source = {item.source_id: item.status for item in items}
    annotation_by_source = _annotation_by_source(db, run.session_id, [source.id for source in sources])

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "title",
            "authors",
            "year",
            "journal",
            "citations",
            "ai_score",
            "status",
            "freeform_tags",
            "approved_tags",
            "ai_summary",
            "source_link",
        ],
    )
    writer.writeheader()
    for source in sources:
        writer.writerow(
            {
                "title": source.title,
                "authors": ", ".join(source.authors or []),
                "year": source.year or "",
                "journal": source.journal or "",
                "citations": source.citation_count if source.citation_count is not None else "",
                "ai_score": f"{float(source.relevance_score):.2f}",
                "status": item_status_by_source.get(source.id, "downloaded" if _effective_artifact_for_source(db, source) is not None else "pending"),
                "freeform_tags": "; ".join((annotation_by_source.get(source.id).freeform_tags_json if annotation_by_source.get(source.id) else []) or []),
                "approved_tags": "; ".join((annotation_by_source.get(source.id).approved_tags_json if annotation_by_source.get(source.id) else []) or []),
                "ai_summary": (annotation_by_source.get(source.id).ai_summary if annotation_by_source.get(source.id) else "") or "",
                "source_link": _source_link(source),
            }
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="library_export_{run_id}.csv"'},
    )


@router.get("/v1/library-export/runs/{run_id}/pdfs.zip")
def export_library_pdfs_zip(
    run_id: str,
    source_id: list[str] | None = Query(default=None),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> Response:
    _load_export_run(db, run_id)
    sources = _accepted_sources(db, run_id, source_id)
    wanted = {source.id: source for source in sources}
    best_pdf_by_source: dict[str, Artifact] = {}
    for source in sources:
        artifact = _effective_artifact_for_source(db, source)
        if artifact is None or artifact.kind != "pdf":
            continue
        best_pdf_by_source[source.id] = artifact

    if not best_pdf_by_source:
        raise HTTPException(status_code=409, detail="no_pdf_artifacts")

    archive = io.BytesIO()
    added = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source in sources:
            artifact = best_pdf_by_source.get(source.id)
            if artifact is None:
                continue
            path = _artifact_file_path(artifact.path)
            if not path.exists() or not path.is_file():
                continue
            safe_title = "".join(ch if ch.isalnum() or ch in {"-", "_", " "} else "_" for ch in source.title).strip()
            safe_title = safe_title[:80].strip() or source.id.replace(":", "_")
            filename = f"{safe_title}.pdf"
            zf.write(path, arcname=filename)
            added += 1
    if added == 0:
        raise HTTPException(status_code=409, detail="pdf_files_missing")

    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="library_export_{run_id}.zip"'},
    )
