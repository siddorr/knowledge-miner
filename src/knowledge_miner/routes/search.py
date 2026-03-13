from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..db import get_db
from ..models import AcquisitionItem, DocumentChunk, ParseRun, ParsedDocument, Run, Source
from ..rate_limit import require_rate_limit
from ..schemas import (
    GlobalSearchResponse,
    GlobalSearchResultOut,
    SearchRequest,
    SearchResponse,
    SearchResultOut,
)

router = APIRouter(tags=["search"])


@router.get("/v1/search/global", response_model=GlobalSearchResponse)
def global_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> GlobalSearchResponse:
    needle = q.strip().lower()
    out: list[GlobalSearchResultOut] = []

    for run in db.scalars(select(Run).order_by(Run.updated_at.desc(), Run.id.asc())).all():
        if needle in run.id.lower() or any(needle in seed.lower() for seed in run.seed_queries):
            out.append(
                GlobalSearchResultOut(
                    result_type="run",
                    id=run.id,
                    label=f"Discovery run {run.id}",
                    snippet=f"status={run.status} accepted={run.accepted_total}",
                    context={"run_id": run.id, "phase": "discovery"},
                )
            )
        if len(out) >= limit:
            break

    if len(out) < limit:
        for source in db.scalars(select(Source).order_by(Source.updated_at.desc(), Source.id.asc())).all():
            blob = f"{source.id} {source.title} {source.doi or ''} {source.abstract or ''}".lower()
            if needle in blob:
                out.append(
                    GlobalSearchResultOut(
                        result_type="source",
                        id=source.id,
                        label=source.title,
                        snippet=source.abstract[:180] if source.abstract else None,
                        context={"run_id": source.run_id, "source_id": source.id, "phase": "discovery"},
                    )
                )
            if len(out) >= limit:
                break

    if len(out) < limit:
        for item in db.scalars(select(AcquisitionItem).order_by(AcquisitionItem.updated_at.desc(), AcquisitionItem.id.asc())).all():
            blob = f"{item.id} {item.source_id} {item.status} {item.last_error or ''}".lower()
            if needle in blob:
                out.append(
                    GlobalSearchResultOut(
                        result_type="acquisition_item",
                        id=item.id,
                        label=f"Acquisition item {item.id}",
                        snippet=f"status={item.status} source={item.source_id}",
                        context={"acq_run_id": item.acq_run_id, "source_id": item.source_id, "phase": "acquisition"},
                    )
                )
            if len(out) >= limit:
                break

    if len(out) < limit:
        for doc in db.scalars(select(ParsedDocument).order_by(ParsedDocument.updated_at.desc(), ParsedDocument.id.asc())).all():
            blob = f"{doc.id} {doc.title or ''} {doc.status} {doc.source_id}".lower()
            if needle in blob:
                out.append(
                    GlobalSearchResultOut(
                        result_type="parsed_document",
                        id=doc.id,
                        label=doc.title or doc.id,
                        snippet=f"status={doc.status}",
                        context={"parse_run_id": doc.parse_run_id, "document_id": doc.id, "phase": "parse"},
                    )
                )
            if len(out) >= limit:
                break

    if len(out) < limit:
        for chunk in db.scalars(select(DocumentChunk).order_by(DocumentChunk.id.asc())).all():
            if needle in (chunk.text or "").lower():
                out.append(
                    GlobalSearchResultOut(
                        result_type="chunk",
                        id=chunk.id,
                        label=f"Chunk {chunk.id}",
                        snippet=(chunk.text or "")[:180],
                        context={
                            "parse_run_id": chunk.parse_run_id,
                            "document_id": chunk.parsed_document_id,
                            "chunk_id": chunk.id,
                            "phase": "parse",
                        },
                    )
                )
            if len(out) >= limit:
                break

    return GlobalSearchResponse(query=q, items=out[:limit], total=len(out[:limit]))


@router.post("/v1/search", response_model=SearchResponse)
def search_corpus(
    payload: SearchRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> SearchResponse:
    run = db.get(ParseRun, payload.parse_run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    needle = payload.query.strip().lower()
    if not needle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request")

    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.parse_run_id == payload.parse_run_id).order_by(DocumentChunk.id.asc())
    ).all()
    scored: list[tuple[DocumentChunk, float]] = []
    for chunk in chunks:
        hay = chunk.text.lower()
        hits = hay.count(needle)
        if hits <= 0:
            continue
        score = float(hits)
        scored.append((chunk, score))
    scored.sort(key=lambda x: (-x[1], x[0].id))
    page = scored[: payload.limit]

    docs = {doc.id: doc for doc in db.scalars(select(ParsedDocument).where(ParsedDocument.parse_run_id == payload.parse_run_id)).all()}
    return SearchResponse(
        items=[
            SearchResultOut(
                document_id=chunk.parsed_document_id,
                chunk_id=chunk.id,
                source_id=docs[chunk.parsed_document_id].source_id if chunk.parsed_document_id in docs else "",
                score=score,
                snippet=chunk.text[:300],
            )
            for chunk, score in page
        ],
        total=len(scored),
    )

