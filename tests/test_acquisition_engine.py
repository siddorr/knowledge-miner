from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from knowledge_miner import acquisition
from knowledge_miner.config import settings
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.models import AcquisitionItem, AcquisitionRun, Artifact, Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _seed_completed_run(source_url: str, doi: str | None = None) -> tuple[str, str]:
    with SessionLocal() as db:
        run = Run(
            id="run_acq_test",
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
        source = Source(
            id="doi:10.1000/acq-engine",
            run_id=run.id,
            title="UPW control in fabs",
            year=2023,
            url=source_url,
            doi=doi,
            abstract="UPW abstract",
            type="academic",
            source="openalex",
            source_native_id="W_ACQ",
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
        db.add(source)
        db.commit()
        return run.id, source.id


def test_resolve_candidate_urls_prefers_source_then_doi():
    source = Source(
        id="s1",
        run_id="r1",
        title="t",
        year=2020,
        url="https://publisher.example/paper",
        doi="10.1000/xyz",
        abstract=None,
        type="academic",
        source="openalex",
        source_native_id=None,
        patent_office=None,
        patent_number=None,
        iteration=1,
        discovery_method="seed_search",
        relevance_score=0.0,
        accepted=False,
        review_status="auto_reject",
        ai_decision=None,
        ai_confidence=None,
        parent_source_id=None,
        provenance_history=[],
    )
    urls = acquisition._resolve_candidate_urls(source)  # noqa: SLF001
    assert urls[0] == "https://publisher.example/paper"
    assert urls[1] == "https://doi.org/10.1000/xyz"


def test_execute_acquisition_run_downloads_pdf(monkeypatch, tmp_path):
    run_id, source_id = _seed_completed_run("https://publisher.example/paper.pdf")
    original_artifacts_dir = settings.artifacts_dir
    try:
        object.__setattr__(settings, "artifacts_dir", str(tmp_path))
        with SessionLocal() as db:
            acq_run = acquisition.create_acquisition_run(db, run_id, retry_failed_only=False)

        def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
            del timeout_seconds, max_bytes
            assert url.endswith(".pdf")
            return acquisition.DownloadResult(
                kind="pdf",
                mime_type="application/pdf",
                content=b"%PDF-1.4 test",
                url=url,
                error=None,
            )

        monkeypatch.setattr(acquisition, "_download_url", fake_download)
        with SessionLocal() as db:
            run = db.get(AcquisitionRun, acq_run.id)
            assert run is not None
            acquisition.execute_acquisition_run(db, run)

            db.refresh(run)
            assert run.downloaded_total == 1
            assert run.partial_total == 0
            assert run.failed_total == 0

            item = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == run.id)).first()
            assert item is not None
            assert item.status == "downloaded"

            artifact = db.scalars(select(Artifact).where(Artifact.acq_run_id == run.id)).first()
            assert artifact is not None
            assert artifact.kind == "pdf"
            assert artifact.source_id == source_id
            assert artifact.path.endswith("source.pdf")
            assert (Path(tmp_path) / artifact.path).exists()
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_execute_acquisition_run_falls_back_to_html(monkeypatch, tmp_path):
    run_id, _ = _seed_completed_run("https://publisher.example/landing", doi="10.1000/fallback")
    original_artifacts_dir = settings.artifacts_dir
    try:
        object.__setattr__(settings, "artifacts_dir", str(tmp_path))
        with SessionLocal() as db:
            acq_run = acquisition.create_acquisition_run(db, run_id, retry_failed_only=False)

        def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
            del timeout_seconds, max_bytes
            if url.startswith("https://doi.org/"):
                return acquisition.DownloadResult(kind=None, mime_type=None, content=None, url=url, error="http_error")
            return acquisition.DownloadResult(
                kind="html",
                mime_type="text/html",
                content=b"<html><body>ok</body></html>",
                url=url,
                error=None,
            )

        monkeypatch.setattr(acquisition, "_download_url", fake_download)
        with SessionLocal() as db:
            run = db.get(AcquisitionRun, acq_run.id)
            assert run is not None
            acquisition.execute_acquisition_run(db, run)
            db.refresh(run)
            assert run.downloaded_total == 0
            assert run.partial_total == 1
            assert run.failed_total == 0
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)
