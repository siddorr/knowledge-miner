from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..db import get_db
from ..models import AcquisitionItem, AcquisitionRun, Artifact, Run, Source
from ..rate_limit import require_rate_limit

router = APIRouter(tags=["library_export"])


def _load_export_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return run


def _accepted_sources(db: Session, run_id: str, source_ids: list[str] | None) -> list[Source]:
    stmt = select(Source).where(Source.run_id == run_id, Source.accepted.is_(True)).order_by(Source.relevance_score.desc(), Source.id.asc())
    rows = db.scalars(stmt).all()
    if not source_ids:
        return rows
    wanted = set(source_ids)
    filtered = [row for row in rows if row.id in wanted]
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


def _source_link(source: Source) -> str:
    if source.doi:
        return f"https://doi.org/{source.doi}"
    return source.url or ""


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
    latest_acq = _latest_acquisition_run(db, run_id)
    item_status_by_source: dict[str, str] = {}
    if latest_acq is not None:
        items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == latest_acq.id)).all()
        item_status_by_source = {item.source_id: item.status for item in items}

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
                "status": item_status_by_source.get(source.id, "pending"),
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
    latest_acq = _latest_acquisition_run(db, run_id)
    if latest_acq is None:
        raise HTTPException(status_code=409, detail="no_acquisition_run")

    wanted = {source.id: source for source in sources}
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.acq_run_id == latest_acq.id, Artifact.source_id.in_(list(wanted.keys())))
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
    ).all()

    best_pdf_by_source: dict[str, Artifact] = {}
    for artifact in artifacts:
        if artifact.kind != "pdf":
            continue
        best_pdf_by_source.setdefault(artifact.source_id, artifact)

    if not best_pdf_by_source:
        raise HTTPException(status_code=409, detail="no_pdf_artifacts")

    archive = io.BytesIO()
    added = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source in sources:
            artifact = best_pdf_by_source.get(source.id)
            if artifact is None:
                continue
            path = Path(artifact.path)
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
