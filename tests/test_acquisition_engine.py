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


def test_resolve_candidate_urls_prefers_doi_then_publisher():
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
    assert urls[0] == "https://doi.org/10.1000/xyz"
    assert urls[1] == "https://publisher.example/paper"


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

        def fake_acquire(source: Source, *, internal_repository_base_url: str | None = None):  # noqa: ANN001
            del internal_repository_base_url
            if source.id == source_ids[0]:
                return acquisition.AcquisitionOutcome(
                    kind="pdf",
                    mime_type="application/pdf",
                    content=b"%PDF-1.4 mixed",
                    url=source.url,
                    selected_url_source="publisher",
                    resolution_attempts=[],
                    attempts=1,
                    error=None,
                    reason_code=None,
                )
            return acquisition.AcquisitionOutcome(
                kind=None,
                mime_type=None,
                content=None,
                url=source.url,
                selected_url_source=None,
                resolution_attempts=[],
                attempts=3,
                error="http_500",
                reason_code="source_error",
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
            coverage_path = Path(tmp_path) / "acquisition" / run.id / "acquisition_coverage_report.json"
            assert coverage_path.exists()
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            assert coverage["coverage"]["manual_recovery_required"] >= 1
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_reason_code_mapping_for_http_errors():
    assert acquisition._reason_code_from_error("http_403") == "paywalled"  # noqa: SLF001
    assert acquisition._reason_code_from_error("http_429") == "rate_limited"  # noqa: SLF001
    assert acquisition._reason_code_from_error("no_candidate_urls") == "no_oa_found"  # noqa: SLF001
    assert acquisition._reason_code_from_error("http_500") == "source_error"  # noqa: SLF001


def test_resolve_candidate_chain_ordering_with_legal_sources(monkeypatch):
    source = Source(
        id="s2",
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
    monkeypatch.setattr(acquisition, "_lookup_openalex_oa_url", lambda _s: "https://openalex.org/oa.pdf")
    monkeypatch.setattr(acquisition, "_lookup_unpaywall_oa_url", lambda _s: "https://unpaywall.org/oa.pdf")
    chain = acquisition._resolve_candidate_chain(source)  # noqa: SLF001
    assert [entry["candidate_source"] for entry in chain] == ["doi", "openalex", "unpaywall", "publisher"]


def test_resolve_candidate_chain_includes_internal_repository_after_doi(monkeypatch):
    source = Source(
        id="s_internal",
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
    monkeypatch.setattr(acquisition, "_lookup_openalex_oa_url", lambda _s: "https://openalex.org/oa.pdf")
    monkeypatch.setattr(acquisition, "_lookup_unpaywall_oa_url", lambda _s: "https://unpaywall.org/oa.pdf")
    chain = acquisition._resolve_candidate_chain(  # noqa: SLF001
        source,
        internal_repository_base_url="https://repo.example.org/service",
    )
    assert [entry["candidate_source"] for entry in chain] == [
        "doi",
        "internal_repository",
        "openalex",
        "unpaywall",
        "publisher",
    ]
    assert chain[1]["candidate_url"] == "https://repo.example.org/service?doi=10.1000%2Fxyz"


def test_create_acquisition_run_stores_internal_repository_url():
    run_id, _ = _seed_completed_run("https://publisher.example/paper.pdf")
    with SessionLocal() as db:
        run = acquisition.create_acquisition_run(
            db,
            run_id,
            retry_failed_only=False,
            internal_repository_base_url="https://repo.example.org/service/",
        )
        assert run.internal_repository_base_url == "https://repo.example.org/service"


def test_acquire_source_content_prefers_internal_repository_pdf(monkeypatch):
    source = Source(
        id="s_internal_pdf",
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

    def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
        del timeout_seconds, max_bytes
        if "repo.example.org" in url:
            return acquisition.DownloadResult(
                kind="pdf",
                mime_type="application/pdf",
                content=b"%PDF-1.4 internal",
                url=url,
                error=None,
                retryable=False,
            )
        return acquisition.DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=url,
            error="http_404",
            retryable=False,
        )

    monkeypatch.setattr(acquisition, "_download_url", fake_download)
    result = acquisition._acquire_source_content(  # noqa: SLF001
        source,
        internal_repository_base_url="https://repo.example.org/service",
    )
    assert result.kind == "pdf"
    assert result.selected_url_source == "internal_repository"
    assert any(
        attempt["candidate_source"] == "internal_repository"
        for attempt in result.resolution_attempts
    )


def test_acquire_source_content_falls_through_internal_repository_html_to_later_pdf(monkeypatch):
    source = Source(
        id="s_internal_html",
        run_id="r1",
        title="t",
        year=2020,
        url="https://publisher.example/paper.pdf",
        doi="10.1000/xyz",
        abstract=None,
        type="academic",
        source="crossref",
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

    def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
        del timeout_seconds, max_bytes
        if "repo.example.org" in url:
            return acquisition.DownloadResult(
                kind="html",
                mime_type="text/html",
                content=b"<html>internal</html>",
                url=url,
                error=None,
                retryable=False,
            )
        if url.endswith(".pdf"):
            return acquisition.DownloadResult(
                kind="pdf",
                mime_type="application/pdf",
                content=b"%PDF-1.4 publisher",
                url=url,
                error=None,
                retryable=False,
            )
        return acquisition.DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=url,
            error="http_404",
            retryable=False,
        )

    monkeypatch.setattr(acquisition, "_download_url", fake_download)
    monkeypatch.setattr(acquisition, "_lookup_openalex_oa_url", lambda _s: None)
    result = acquisition._acquire_source_content(  # noqa: SLF001
        source,
        internal_repository_base_url="https://repo.example.org/service",
    )
    assert result.kind == "pdf"
    assert result.selected_url_source == "publisher"


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


def test_acquisition_observability_logs_and_counters(monkeypatch, tmp_path, caplog):
    run_id, source_id = _seed_completed_run("https://publisher.example/paper.pdf")
    original_artifacts_dir = settings.artifacts_dir
    try:
        object.__setattr__(settings, "artifacts_dir", str(tmp_path))
        with SessionLocal() as db:
            acq_run = acquisition.create_acquisition_run(db, run_id, retry_failed_only=False)

        def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
            del timeout_seconds, max_bytes
            return acquisition.DownloadResult(
                kind="pdf",
                mime_type="application/pdf",
                content=b"%PDF-observe",
                url=url,
                error=None,
                retryable=False,
            )

        monkeypatch.setattr(acquisition, "_download_url", fake_download)
        caplog.set_level("INFO", logger="knowledge_miner")
        with SessionLocal() as db:
            run = db.get(AcquisitionRun, acq_run.id)
            assert run is not None
            acquisition.execute_acquisition_run(db, run)

        download_events = []
        summary_events = []
        for record in caplog.records:
            if record.name != "knowledge_miner":
                continue
            payload = json.loads(record.message)
            if payload.get("event") == "acquisition_download":
                download_events.append(payload)
            if payload.get("event") == "acquisition_summary":
                summary_events.append(payload)

        assert download_events
        evt = download_events[-1]
        assert evt["acq_run_id"] == acq_run.id
        assert evt["source_id"] == source_id
        assert evt["domain"] == "publisher.example"
        assert evt["status"] == "downloaded"
        assert isinstance(evt["latency_ms"], float)

        assert summary_events
        summary = summary_events[-1]
        assert summary["acq_run_id"] == acq_run.id
        assert summary["status"] == "completed"
        assert summary["counters"]["attempted"] == 1
        assert summary["counters"]["downloaded"] == 1
        assert summary["latency_histograms"]
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)
