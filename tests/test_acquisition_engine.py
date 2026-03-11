from __future__ import annotations

from pathlib import Path
import json
import hashlib

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


def _seed_completed_run_with_two_sources() -> tuple[str, list[str]]:
    with SessionLocal() as db:
        run = Run(
            id="run_acq_test_two",
            status="completed",
            seed_queries=["upw semiconductor"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=2,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled (USE_AI_FILTER=false); heuristic filtering only.",
        )
        db.add(run)
        source_ids = ["doi:10.1000/acq-engine-a", "doi:10.1000/acq-engine-b"]
        for sid, url in zip(source_ids, ["https://publisher.example/a.pdf", "https://publisher.example/b"], strict=True):
            db.add(
                Source(
                    id=sid,
                    run_id=run.id,
                    title=f"UPW source {sid}",
                    year=2023,
                    url=url,
                    doi=sid.removeprefix("doi:"),
                    abstract="UPW abstract",
                    type="academic",
                    source="openalex",
                    source_native_id=sid,
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
            )
        db.commit()
        return run.id, source_ids


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
                retryable=False,
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
            assert artifact.path == f"acquisition/{run.id}/{source_id}/source.pdf"
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
                return acquisition.DownloadResult(
                    kind=None,
                    mime_type=None,
                    content=None,
                    url=url,
                    error="http_404",
                    retryable=False,
                )
            return acquisition.DownloadResult(
                kind="html",
                mime_type="text/html",
                content=b"<html><body>ok</body></html>",
                url=url,
                error=None,
                retryable=False,
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

            manifest_path = Path(tmp_path) / "acquisition" / run.id / "manifest.json"
            assert manifest_path.exists()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert manifest["acq_run_id"] == run.id
            assert manifest["totals"]["partial_total"] == 1
            assert len(manifest["items"]) == 1
            assert len(manifest["artifacts"]) == 1
            assert manifest["artifacts"][0]["path"] == f"acquisition/{run.id}/doi:10.1000/acq-engine/source.html"
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_download_with_retries_retries_transient(monkeypatch):
    state = {"n": 0}

    def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
        del url, timeout_seconds, max_bytes
        state["n"] += 1
        if state["n"] < 3:
            return acquisition.DownloadResult(
                kind=None,
                mime_type=None,
                content=None,
                url="https://x",
                error="network_error",
                retryable=True,
            )
        return acquisition.DownloadResult(
            kind="pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.4",
            url="https://x",
            error=None,
            retryable=False,
        )

    monkeypatch.setattr(acquisition, "_download_url", fake_download)
    result, attempts = acquisition._download_with_retries(  # noqa: SLF001
        "https://x",
        timeout_seconds=1.0,
        max_bytes=1000,
        delays=(0.0, 0.0, 0.0),
        sleep=lambda _: None,
    )
    assert result.kind == "pdf"
    assert attempts == 3


def test_retry_failed_only_selects_only_failed_sources(monkeypatch):
    run_id, source_id = _seed_completed_run("https://publisher.example/paper.pdf")
    with SessionLocal() as db:
        first = acquisition.create_acquisition_run(db, run_id, retry_failed_only=False)
        first_item = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == first.id)).first()
        assert first_item is not None
        first_item.status = "failed"
        db.commit()

        second = acquisition.create_acquisition_run(db, run_id, retry_failed_only=True)
        second_items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == second.id)).all()
        assert len(second_items) == 1
        assert second_items[0].source_id == source_id
        assert second.retry_failed_only is True


def test_retry_failed_only_without_previous_run_creates_no_items():
    run_id, _ = _seed_completed_run("https://publisher.example/paper.pdf")
    with SessionLocal() as db:
        run = acquisition.create_acquisition_run(db, run_id, retry_failed_only=True)
        items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == run.id)).all()
        assert run.total_sources == 0
        assert items == []


def test_execute_acquisition_run_mixed_success_and_failure(monkeypatch, tmp_path):
    run_id, source_ids = _seed_completed_run_with_two_sources()
    original_artifacts_dir = settings.artifacts_dir
    try:
        object.__setattr__(settings, "artifacts_dir", str(tmp_path))
        with SessionLocal() as db:
            acq_run = acquisition.create_acquisition_run(db, run_id, retry_failed_only=False)

        def fake_acquire(source: Source):  # noqa: ANN001
            if source.id == source_ids[0]:
                return acquisition.AcquisitionOutcome(
                    kind="pdf",
                    mime_type="application/pdf",
                    content=b"%PDF-1.4 mixed",
                    url=source.url,
                    attempts=1,
                    error=None,
                )
            return acquisition.AcquisitionOutcome(
                kind=None,
                mime_type=None,
                content=None,
                url=source.url,
                attempts=3,
                error="http_500",
            )

        monkeypatch.setattr(acquisition, "_acquire_source_content", fake_acquire)
        with SessionLocal() as db:
            run = db.get(AcquisitionRun, acq_run.id)
            assert run is not None
            acquisition.execute_acquisition_run(db, run)
            db.refresh(run)
            assert run.downloaded_total == 1
            assert run.failed_total == 1
            assert run.partial_total == 0

            items = db.scalars(
                select(AcquisitionItem).where(AcquisitionItem.acq_run_id == run.id).order_by(AcquisitionItem.source_id.asc())
            ).all()
            assert len(items) == 2
            statuses = {i.source_id: i.status for i in items}
            assert statuses[source_ids[0]] == "downloaded"
            assert statuses[source_ids[1]] == "failed"
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_pdf_artifact_checksum_and_size(monkeypatch, tmp_path):
    run_id, source_id = _seed_completed_run("https://publisher.example/paper.pdf")
    original_artifacts_dir = settings.artifacts_dir
    payload = b"%PDF-1.4 checksum"
    try:
        object.__setattr__(settings, "artifacts_dir", str(tmp_path))
        with SessionLocal() as db:
            acq_run = acquisition.create_acquisition_run(db, run_id, retry_failed_only=False)

        def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
            del url, timeout_seconds, max_bytes
            return acquisition.DownloadResult(
                kind="pdf",
                mime_type="application/pdf",
                content=payload,
                url="https://publisher.example/paper.pdf",
                error=None,
                retryable=False,
            )

        monkeypatch.setattr(acquisition, "_download_url", fake_download)
        with SessionLocal() as db:
            run = db.get(AcquisitionRun, acq_run.id)
            assert run is not None
            acquisition.execute_acquisition_run(db, run)
            artifact = db.scalars(select(Artifact).where(Artifact.acq_run_id == run.id, Artifact.source_id == source_id)).first()
            assert artifact is not None
            assert artifact.size_bytes == len(payload)
            assert artifact.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)
