from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_api_key
from ..db import get_db
from ..discovery import create_bookmark_seeded_run, enqueue_bookmark_seed_run
from ..models import Bookmark, DiscoveryCitationSeed, Run, SessionProfile, Source
from ..rate_limit import require_rate_limit
from ..schemas import (
    BookmarkCreateRequest,
    BookmarkCreateSessionResponse,
    BookmarkRead,
    BookmarksListResponse,
)

router = APIRouter(tags=["bookmarks"])


def _source_session_name(db: Session, session_id: str | None) -> str | None:
    if not session_id:
        return None
    profile = db.get(SessionProfile, session_id)
    if profile and isinstance(profile.name, str) and profile.name.strip():
        return profile.name.strip()
    return session_id


def _bookmark_read(db: Session, bookmark: Bookmark) -> BookmarkRead:
    return BookmarkRead(
        id=bookmark.id,
        source_id=bookmark.source_id,
        title=bookmark.title,
        abstract=bookmark.abstract,
        year=bookmark.year,
        doi=bookmark.doi,
        doi_url=(f"https://doi.org/{bookmark.doi}" if bookmark.doi else None),
        source_url=bookmark.source_url,
        source_session_id=bookmark.source_session_id,
        source_session_name=_source_session_name(db, bookmark.source_session_id),
        source_run_id=bookmark.source_run_id,
        created_at=bookmark.created_at.isoformat() if bookmark.created_at else None,
    )


def _normalize_session_name_from_title(title: str | None) -> str:
    normalized = re.sub(r"[^A-Za-z0-9\s]+", " ", str(title or "")).strip()
    words = [word for word in normalized.split() if word]
    if not words:
        return "Bookmark Branch"
    return " ".join(words[:4])


def _session_context_from_bookmark(bookmark: Bookmark) -> str:
    lines = [
        "Citation research seed paper context.",
        f"Title: {bookmark.title}",
    ]
    if bookmark.year is not None:
        lines.append(f"Year: {bookmark.year}")
    if bookmark.doi:
        lines.append(f"DOI: {bookmark.doi}")
    if bookmark.abstract:
        lines.append(f"Abstract: {bookmark.abstract}")
    return "\n".join(lines).strip()


def _enqueue_bookmark_task(background_tasks: BackgroundTasks, run_id: str) -> None:
    try:
        from .. import main as main_module

        enqueue_fn = getattr(main_module, "enqueue_bookmark_seed_run", enqueue_bookmark_seed_run)
    except Exception:
        enqueue_fn = enqueue_bookmark_seed_run
    background_tasks.add_task(enqueue_fn, run_id)


@router.get("/v1/bookmarks", response_model=BookmarksListResponse)
def list_bookmarks(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> BookmarksListResponse:
    rows = db.scalars(select(Bookmark).order_by(Bookmark.created_at.desc(), Bookmark.id.desc())).all()
    if q and q.strip():
        needle = q.strip().lower()
        rows = [
            row
            for row in rows
            if needle in (row.title or "").lower()
            or needle in (row.abstract or "").lower()
            or needle in (row.doi or "").lower()
        ]
    page = rows[offset : offset + limit]
    return BookmarksListResponse(
        items=[_bookmark_read(db, row) for row in page],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


@router.post("/v1/bookmarks", response_model=BookmarkRead)
def upsert_bookmark(
    payload: BookmarkCreateRequest,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> BookmarkRead:
    source = db.get(Source, payload.source_id.strip())
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source_not_found")
    existing = db.scalars(select(Bookmark).where(Bookmark.source_id == source.id).limit(1)).first()
    run = db.get(Run, source.run_id)
    if existing is None:
        existing = Bookmark(id=f"bookmark_{uuid.uuid4().hex[:12]}", source_id=source.id)
        db.add(existing)
    existing.title = source.title
    existing.abstract = source.abstract
    existing.year = source.year
    existing.doi = source.doi
    existing.source_url = source.url
    existing.source_run_id = source.run_id
    existing.source_session_id = run.session_id if run is not None else None
    db.commit()
    db.refresh(existing)
    return _bookmark_read(db, existing)


@router.delete("/v1/bookmarks/{bookmark_id}")
def delete_bookmark(
    bookmark_id: str,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> dict:
    bookmark = db.get(Bookmark, bookmark_id)
    if bookmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bookmark_not_found")
    for seed in db.scalars(
        select(DiscoveryCitationSeed).where(DiscoveryCitationSeed.origin_bookmark_id == bookmark_id)
    ).all():
        seed.origin_bookmark_id = None
    db.delete(bookmark)
    db.commit()
    return {"bookmark_id": bookmark_id, "status": "deleted"}


@router.post(
    "/v1/bookmarks/{bookmark_id}/create-session",
    response_model=BookmarkCreateSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_session_from_bookmark(
    bookmark_id: str,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
    __: None = Depends(require_rate_limit),
    db: Session = Depends(get_db),
) -> BookmarkCreateSessionResponse:
    bookmark = db.get(Bookmark, bookmark_id)
    if bookmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bookmark_not_found")
    source = db.get(Source, bookmark.source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bookmark_source_not_found")

    session_id = f"session_{uuid.uuid4().hex[:12]}"
    session_name = _normalize_session_name_from_title(bookmark.title)
    profile = SessionProfile(
        session_id=session_id,
        name=session_name,
        session_context=_session_context_from_bookmark(bookmark),
    )
    db.add(profile)
    db.commit()
    run = create_bookmark_seeded_run(
        db,
        session_id=session_id,
        session_context=profile.session_context or "",
        bookmark=bookmark,
        seed_source_ids=[bookmark.source_id],
    )
    _enqueue_bookmark_task(background_tasks, run.id)
    return BookmarkCreateSessionResponse(
        session_id=session_id,
        session_name=session_name,
        discovery_run_id=run.id,
        status=run.status,
        bookmarked_parent_count=1,
    )
