from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from knowledge_miner.ai_filter import AIRelevanceResult
from knowledge_miner.config import settings
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.discovery import (
    _ingest_candidates,
    _rank_citation_candidates,
    create_run,
    execute_run,
    export_sources_raw,
    review_source,
)
from knowledge_miner.models import CitationEdge, Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_run_execution_creates_completed_run_and_sources():
    with SessionLocal() as db:
        run = create_run(db, ["ultrapure water semiconductor"], max_iterations=2)
        assert run.status == "queued"
        execute_run(db, run)
        db.refresh(run)
        assert run.status == "completed"
        assert run.current_iteration >= 1

        accepted_count = db.query(Source).filter(Source.run_id == run.id, Source.accepted.is_(True)).count()
        assert accepted_count >= 0


def test_review_source_accept():
    with SessionLocal() as db:
        run = create_run(db, ["UPW wafer cleaning"], max_iterations=1)
        execute_run(db, run)

        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None

        updated = review_source(db, source, "accept")
        assert updated.accepted is True
        assert updated.review_status == "human_accept"


def test_export_sources_raw_file_created():
    with SessionLocal() as db:
        run = create_run(db, ["UPW TOC silica"], max_iterations=1)
        execute_run(db, run)
        path = export_sources_raw(db, run.id)

    assert isinstance(path, Path)
    assert path.exists()
    assert path.name == "sources_raw.json"


def test_ai_filter_override_in_ingest():
    class StubAIFilter:
        def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
            return AIRelevanceResult(decision="needs_review", confidence=0.91, reason="acronym ambiguity")

    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        candidates = [
            {
                "title": "UPW for photolithography process quality",
                "year": 2020,
                "url": f"https://example.org/test-ai-override/{run.id}",
                "doi": None,
                "abstract": "UPW process in semiconductor fabs",
                "source": "openalex",
                "source_native_id": f"oa_test_1_{run.id}",
                "openalex_id": f"oa_test_1_{run.id}",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "seed_search",
                "parent_source_id": None,
            }
        ]
        _ingest_candidates(db, run.id, 1, candidates, ai_filter=StubAIFilter())
        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None
        assert source.review_status == "needs_review"
        assert source.accepted is False
        assert source.ai_decision == "needs_review"


def test_ai_filter_low_confidence_does_not_override():
    class LowConfidenceAIFilter:
        def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
            return AIRelevanceResult(decision="auto_reject", confidence=0.2, reason="low confidence")

    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        candidates = [
            {
                "title": "UPW process control for semiconductor wafer cleaning",
                "year": 2021,
                "url": f"https://example.org/test-ai-low-confidence/{run.id}",
                "doi": None,
                "abstract": "ultrapure water UPW semiconductor RO EDI UV254",
                "source": "openalex",
                "source_native_id": f"oa_test_low_conf_{run.id}",
                "openalex_id": f"oa_test_low_conf_{run.id}",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "seed_search",
                "parent_source_id": None,
            }
        ]
        _ingest_candidates(db, run.id, 1, candidates, ai_filter=LowConfidenceAIFilter())
        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None
        # Heuristic remains in effect; low-confidence AI proposal must not override.
        assert source.review_status == "auto_accept"
        assert source.accepted is True
        assert source.ai_decision is None


def test_run_execution_persists_citation_edges():
    with SessionLocal() as db:
        run = create_run(db, ["ultrapure water semiconductor"], max_iterations=1)
        execute_run(db, run)
        db.refresh(run)
        edge_count = db.query(CitationEdge).filter(CitationEdge.run_id == run.id).count()
        assert edge_count > 0
        assert run.citation_edges_total > 0
        assert run.expanded_candidates_total > 0


def test_run_metrics_fields_default_on_create():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        db.refresh(run)
        assert isinstance(run, Run)
        assert run.expanded_candidates_total == 0
        assert run.citation_edges_total == 0


def test_dedup_merge_preserves_provenance_history():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        first = [
            {
                "title": "UPW process for semiconductor cleaning",
                "year": 2021,
                "url": f"https://example.org/provenance/{run.id}/first",
                "doi": "10.1000/upw-merge",
                "abstract": "first abstract",
                "source": "openalex",
                "source_native_id": f"oa_{run.id}",
                "openalex_id": f"oa_{run.id}",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "seed_search",
                "parent_source_id": None,
            }
        ]
        second = [
            {
                "title": "UPW process for semiconductor cleaning",
                "year": 2021,
                "url": f"https://example.org/provenance/{run.id}/second",
                "doi": "10.1000/upw-merge",
                "abstract": "second abstract with extra details",
                "source": "semantic_scholar",
                "source_native_id": f"s2_{run.id}",
                "openalex_id": None,
                "semantic_scholar_id": f"s2_{run.id}",
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "forward_citation",
                "parent_source_id": "parent-1",
            }
        ]

        _ingest_candidates(db, run.id, 1, first)
        _ingest_candidates(db, run.id, 2, second)
        source = db.scalars(select(Source).where(Source.run_id == run.id)).first()
        assert source is not None
        assert len(source.provenance_history) == 2
        assert source.provenance_history[0]["discovery_method"] == "seed_search"
        assert source.provenance_history[1]["discovery_method"] == "forward_citation"
        assert source.provenance_history[1]["parent_source_id"] == "parent-1"


def test_export_includes_provenance_history():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        candidates = [
            {
                "title": "UPW conductivity control in fabs",
                "year": 2020,
                "url": f"https://example.org/export-provenance/{run.id}",
                "doi": f"10.1000/export-{run.id}",
                "abstract": "UPW semiconductor process",
                "source": "openalex",
                "source_native_id": f"oa_export_{run.id}",
                "openalex_id": f"oa_export_{run.id}",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "seed_search",
                "parent_source_id": None,
            }
        ]
        _ingest_candidates(db, run.id, 1, candidates)
        path = export_sources_raw(db, run.id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["sources"]
        assert "provenance_history" in payload["sources"][0]
        assert len(payload["sources"][0]["provenance_history"]) == 1


def test_citation_ranking_prioritizes_abstract_doi_recency_overlap():
    parent = Source(
        id="p1",
        run_id="r1",
        title="Ultrapure water control in semiconductor fabs",
        year=2022,
        url=None,
        doi=None,
        abstract="TOC silica control in UPW loops",
        type="academic",
        source="openalex",
        source_native_id=None,
        patent_office=None,
        patent_number=None,
        iteration=1,
        discovery_method="seed_search",
        relevance_score=5.5,
        accepted=True,
        review_status="auto_accept",
        ai_decision=None,
        ai_confidence=None,
        parent_source_id=None,
        provenance_history=[],
    )
    candidates = [
        {
            "title": "General water treatment overview",
            "year": 2025,
            "url": "https://example.org/a",
            "doi": None,
            "abstract": None,
            "source": "openalex",
        },
        {
            "title": "UPW TOC control in semiconductor fabs",
            "year": 2021,
            "url": "https://example.org/b",
            "doi": "10.1000/x",
            "abstract": "Detailed abstract",
            "source": "openalex",
        },
        {
            "title": "UPW silica reduction methods",
            "year": 2024,
            "url": "https://example.org/c",
            "doi": "10.1000/y",
            "abstract": "Another abstract",
            "source": "openalex",
        },
    ]
    ranked = _rank_citation_candidates(parent, candidates)
    assert ranked[0]["url"] == "https://example.org/c"
    assert ranked[1]["url"] == "https://example.org/b"
    assert ranked[2]["url"] == "https://example.org/a"


def test_execute_run_emits_observability_logs(caplog):
    caplog.set_level("INFO", logger="knowledge_miner")
    with SessionLocal() as db:
        run = create_run(db, ["ultrapure water semiconductor"], max_iterations=1)
        execute_run(db, run)

    provider_events = []
    summary_events = []
    for rec in caplog.records:
        if rec.name != "knowledge_miner":
            continue
        try:
            payload = json.loads(rec.message)
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "provider_call":
            provider_events.append(payload)
        if payload.get("event") == "run_summary":
            summary_events.append(payload)

    assert provider_events
    assert any("run_id" in event and "iteration" in event and "provider" in event for event in provider_events)
    assert summary_events
    counters = summary_events[-1]["counters"]
    assert "fetched" in counters


def test_create_run_ai_warning_disabled():
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    try:
        object.__setattr__(settings, "use_ai_filter", False)
        object.__setattr__(settings, "ai_api_key", None)
        with SessionLocal() as db:
            run = create_run(db, ["upw"], max_iterations=1)
            assert run.ai_filter_active is False
            assert run.ai_filter_warning is not None
            assert "USE_AI_FILTER=false" in run.ai_filter_warning
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)


def test_create_run_ai_warning_missing_token():
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    try:
        object.__setattr__(settings, "use_ai_filter", True)
        object.__setattr__(settings, "ai_api_key", None)
        with SessionLocal() as db:
            run = create_run(db, ["upw"], max_iterations=1)
            assert run.ai_filter_active is False
            assert run.ai_filter_warning is not None
            assert "AI_API_KEY is missing" in run.ai_filter_warning
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)


def test_create_run_ai_enabled_without_warning():
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    try:
        object.__setattr__(settings, "use_ai_filter", True)
        object.__setattr__(settings, "ai_api_key", "token")
        with SessionLocal() as db:
            run = create_run(db, ["upw"], max_iterations=1)
            assert run.ai_filter_active is True
            assert run.ai_filter_warning is None
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)
