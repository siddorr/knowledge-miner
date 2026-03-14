from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import AcquisitionRun, Artifact, Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_exportable_run(tmp_path: Path) -> tuple[str, str]:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test pdf")
    with SessionLocal() as db:
        run = Run(
            id="run_export_1",
            status="completed",
            seed_queries=["upw"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning=None,
        )
        source = Source(
            id="src_export_1",
            run_id=run.id,
            title="UPW export paper",
            year=2024,
            url="https://example.org/upw",
            doi="10.1000/upw-export",
            abstract="UPW export abstract",
            journal="Journal of Ultrapure Water",
            authors=["A. Expert", "B. Builder"],
            citation_count=33,
            type="academic",
            source="openalex",
            source_native_id="oa_1",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=9.4,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="accept",
            heuristic_score=8.1,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        acq_run = AcquisitionRun(
            id="acq_export_1",
            discovery_run_id=run.id,
            retry_failed_only=False,
            status="completed",
            total_sources=1,
            downloaded_total=1,
            partial_total=0,
            failed_total=0,
            skipped_total=0,
            error_message=None,
        )
        artifact = Artifact(
            id="art_export_1",
            acq_run_id=acq_run.id,
            source_id=source.id,
            item_id=None,
            kind="pdf",
            path=str(pdf_path),
            checksum_sha256="abc",
            size_bytes=pdf_path.stat().st_size,
            mime_type="application/pdf",
        )
        db.add(run)
        db.add(source)
        db.add(acq_run)
        db.add(artifact)
        db.commit()
        return run.id, source.id


def test_library_export_metadata_csv(tmp_path: Path):
    run_id, source_id = _seed_exportable_run(tmp_path)
    client = TestClient(app)
    response = client.get(
        f"/v1/library-export/runs/{run_id}/metadata.csv",
        params=[("source_id", source_id)],
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert "UPW export paper" in response.text
    assert "10.1000/upw-export" in response.text
    assert "Journal of Ultrapure Water" in response.text
    assert "A. Expert, B. Builder" in response.text
    assert "33" in response.text


def test_library_export_pdfs_zip(tmp_path: Path):
    run_id, source_id = _seed_exportable_run(tmp_path)
    client = TestClient(app)
    response = client.get(
        f"/v1/library-export/runs/{run_id}/pdfs.zip",
        params=[("source_id", source_id)],
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.headers.get("content-type") == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert names
    assert names[0].endswith(".pdf")
