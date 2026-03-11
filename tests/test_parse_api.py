from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

import knowledge_miner.main as main_module
from knowledge_miner.config import settings
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import AcquisitionRun, Artifact, DocumentChunk, ParsedDocument, Run, Source
from knowledge_miner.parse import execute_parse_run


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_acq_with_html(tmp_path: Path) -> tuple[str, str]:
    with SessionLocal() as db:
        run = Run(
            id="run_parse_seed",
            status="completed",
            seed_queries=["upw semiconductor"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled (USE_AI_FILTER=false); heuristic filtering only.",
        )
        db.add(run)
        src = Source(
            id="doi:10.1000/parse-seed",
            run_id=run.id,
            title="UPW parsing seed paper",
            year=2024,
            url="https://example.org/parse",
            doi="10.1000/parse-seed",
            abstract="UPW parsing seed abstract",
            type="academic",
            source="openalex",
            source_native_id="W_PARSE",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=6.0,
            accepted=True,
            review_status="auto_accept",
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add(src)
        acq = AcquisitionRun(
            id="acq_parse_seed",
            discovery_run_id=run.id,
            retry_failed_only=False,
            status="completed",
            total_sources=1,
            downloaded_total=0,
            partial_total=1,
            failed_total=0,
            skipped_total=0,
        )
        db.add(acq)
        rel_path = "acquisition/acq_parse_seed/doi:10.1000/parse-seed/source.html"
        html_path = tmp_path / rel_path
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            "<html><body><h1>UPW in semiconductor fabs</h1><p>UPW quality control and TOC reduction.</p></body></html>",
            encoding="utf-8",
        )
        art = Artifact(
            id="artifact_parse_seed",
            acq_run_id=acq.id,
            source_id=src.id,
            item_id=None,
            kind="html",
            path=rel_path,
            checksum_sha256="x",
            size_bytes=html_path.stat().st_size,
            mime_type="text/html",
        )
        db.add(art)
        db.commit()
    return "acq_parse_seed", str(tmp_path)


def test_parse_endpoints_basic(monkeypatch, tmp_path):
    acq_run_id, artifacts_dir = _seed_acq_with_html(tmp_path)
    object.__setattr__(settings, "artifacts_dir", artifacts_dir)
    monkeypatch.setattr(main_module, "enqueue_parse_run", lambda parse_run_id: None)

    client = TestClient(app)
    created = client.post("/v1/parse/runs", json={"acq_run_id": acq_run_id}, headers=_auth_headers())
    assert created.status_code == 202
    parse_run_id = created.json()["parse_run_id"]

    with SessionLocal() as db:
        run = db.get(main_module.ParseRun, parse_run_id)  # type: ignore[attr-defined]
        assert run is not None
        execute_parse_run(db, run)

    status = client.get(f"/v1/parse/runs/{parse_run_id}", headers=_auth_headers())
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["ai_filter_active"] is False
    assert status.json()["ai_filter_warning"] is not None

    docs = client.get(f"/v1/parse/runs/{parse_run_id}/documents", headers=_auth_headers())
    assert docs.status_code == 200
    assert docs.json()["total"] == 1
    doc_id = docs.json()["items"][0]["document_id"]

    doc = client.get(f"/v1/parse/documents/{doc_id}", headers=_auth_headers())
    assert doc.status_code == 200
    assert doc.json()["status"] == "parsed"
    assert doc.json()["parser_used"] == "html_readability_heuristic"
    assert doc.json()["decision"] in {"auto_accept", "needs_review", "auto_reject"}
    assert doc.json()["confidence"] is not None
    assert doc.json()["reason"] is not None

    text = client.get(f"/v1/parse/documents/{doc_id}/text", headers=_auth_headers())
    assert text.status_code == 200
    assert "UPW in semiconductor fabs" in text.json()["text"]

    chunks = client.get(f"/v1/parse/runs/{parse_run_id}/chunks", headers=_auth_headers())
    assert chunks.status_code == 200
    assert chunks.json()["total"] >= 1
    assert chunks.json()["items"][0]["decision"] in {"auto_accept", "needs_review", "auto_reject"}

    search = client.post(
        "/v1/search",
        json={"parse_run_id": parse_run_id, "query": "UPW", "limit": 5},
        headers=_auth_headers(),
    )
    assert search.status_code == 200
    assert search.json()["total"] >= 1


def test_parse_incremental_reuses_chunks_for_unchanged_document(monkeypatch, tmp_path):
    acq_run_id, artifacts_dir = _seed_acq_with_html(tmp_path)
    object.__setattr__(settings, "artifacts_dir", artifacts_dir)
    monkeypatch.setattr(main_module, "enqueue_parse_run", lambda parse_run_id: None)
    client = TestClient(app)

    first = client.post("/v1/parse/runs", json={"acq_run_id": acq_run_id}, headers=_auth_headers())
    assert first.status_code == 202
    first_id = first.json()["parse_run_id"]
    with SessionLocal() as db:
        first_run = db.get(main_module.ParseRun, first_id)  # type: ignore[attr-defined]
        assert first_run is not None
        execute_parse_run(db, first_run)
        first_doc = db.scalars(select(ParsedDocument).where(ParsedDocument.parse_run_id == first_id)).first()
        assert first_doc is not None
        first_chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.parsed_document_id == first_doc.id)).all()
        assert first_chunks

    second = client.post("/v1/parse/runs", json={"acq_run_id": acq_run_id}, headers=_auth_headers())
    assert second.status_code == 202
    second_id = second.json()["parse_run_id"]
    with SessionLocal() as db:
        second_run = db.get(main_module.ParseRun, second_id)  # type: ignore[attr-defined]
        assert second_run is not None
        execute_parse_run(db, second_run)
        second_doc = db.scalars(select(ParsedDocument).where(ParsedDocument.parse_run_id == second_id)).first()
        assert second_doc is not None
        assert second_doc.parser_used == "cached_chunks"
        second_chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.parsed_document_id == second_doc.id)).all()
        assert len(second_chunks) == len(first_chunks)
