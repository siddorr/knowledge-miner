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
    execute_citation_iteration_run,
    execute_run,
    export_sources_raw,
    review_source,
)
from knowledge_miner.models import CitationEdge, DiscoveryRunQuery, Run, Source
from knowledge_miner.observability import RunObservability


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
        assert updated.final_decision == "human_accept"
        assert updated.decision_source == "human_review"


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


def test_ai_filter_decision_is_authoritative_in_ai_first_mode():
    class StubAIFilter:
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
        _ingest_candidates(db, run.id, 1, candidates, ai_filter=StubAIFilter())
        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None
        assert source.review_status == "auto_reject"
        assert source.final_decision == "auto_reject"
        assert source.decision_source == "ai"
        assert source.accepted is False
        assert source.ai_decision == "auto_reject"


def test_manual_citation_iteration_persists_citation_edges(monkeypatch):
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    try:
        object.__setattr__(settings, "use_ai_filter", True)
        object.__setattr__(settings, "ai_api_key", "token")
        monkeypatch.setattr(
            "knowledge_miner.ai_filter.AIRelevanceFilter.evaluate",
            lambda self, *, title, abstract, base_score, base_decision: AIRelevanceResult(  # noqa: ARG005
                decision="auto_accept",
                confidence=0.95,
                reason="test",
            ),
        )
        with SessionLocal() as db:
            source_run = create_run(db, ["ultrapure water semiconductor"], max_iterations=1)
            execute_run(db, source_run)
            db.refresh(source_run)
            execute_citation_iteration_run(db, source_run, source_run_id=source_run.id)
            db.refresh(source_run)
            edge_count = db.query(CitationEdge).filter(CitationEdge.run_id == source_run.id).count()
            assert edge_count > 0
            assert source_run.citation_edges_total > 0
            assert source_run.expanded_candidates_total > 0
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)


def test_run_metrics_fields_default_on_create():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        db.refresh(run)
        assert isinstance(run, Run)
        assert run.expanded_candidates_total == 0
        assert run.citation_edges_total == 0


def test_create_run_persists_discovery_run_queries():
    with SessionLocal() as db:
        run = create_run(db, ["upw", "semiconductor"], max_iterations=1)
        rows = db.scalars(
            select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id).order_by(DiscoveryRunQuery.position.asc())
        ).all()
        assert [row.query_text for row in rows] == ["upw", "semiconductor"]
        assert all(row.status == "waiting" for row in rows)


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


def test_ingest_recovers_when_initial_id_check_is_stale(monkeypatch):
    with SessionLocal() as db:
        run_a = create_run(db, ["upw"], max_iterations=1)
        run_b = create_run(db, ["upw"], max_iterations=1)
        shared_doi = "10.1000/upw-race"
        base_candidate = {
            "title": "UPW process monitoring",
            "year": 2022,
            "url": "https://example.org/upw-race",
            "doi": shared_doi,
            "abstract": "UPW quality monitoring in semiconductor fabs",
            "source": "openalex",
            "source_native_id": "oa_upw_race",
            "openalex_id": "oa_upw_race",
            "semantic_scholar_id": None,
            "patent_office": None,
            "patent_number": None,
            "type": "academic",
            "discovery_method": "seed_search",
            "parent_source_id": None,
        }
        _ingest_candidates(db, run_a.id, 1, [base_candidate])

        from knowledge_miner import discovery as discovery_module

        original = discovery_module._run_scoped_source_id
        call_count = {"value": 0}

        def stale_once(db_session, run_id, canonical_sid):  # noqa: ANN001
            call_count["value"] += 1
            if call_count["value"] == 1:
                return canonical_sid
            return original(db_session, run_id, canonical_sid)

        monkeypatch.setattr(discovery_module, "_run_scoped_source_id", stale_once)

        candidate_b = dict(base_candidate)
        candidate_b["source_native_id"] = "oa_upw_race_second"
        candidate_b["openalex_id"] = "oa_upw_race_second"
        _ingest_candidates(db, run_b.id, 1, [candidate_b])

        inserted = db.scalars(select(Source).where(Source.run_id == run_b.id)).all()
        assert len(inserted) == 1
        assert inserted[0].id == f"doi:{shared_doi}::run:{run_b.id}"


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
        class StubAIFilter:
            def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
                return AIRelevanceResult(decision="auto_accept", confidence=0.9, reason="match")

        _ingest_candidates(db, run.id, 1, candidates, ai_filter=StubAIFilter())
        path = export_sources_raw(db, run.id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["sources"]
        assert "provenance_history" in payload["sources"][0]
        assert len(payload["sources"][0]["provenance_history"]) == 1
        assert payload["sources"][0]["decision_source"] == "ai"


def test_ai_runtime_failure_sets_needs_review_with_fallback_source():
    class FailingAIFilter:
        def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
            return None

    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        candidates = [
            {
                "title": "UPW process control for semiconductor wafer cleaning",
                "year": 2021,
                "url": f"https://example.org/test-ai-fallback/{run.id}",
                "doi": None,
                "abstract": "ultrapure water UPW semiconductor RO EDI UV254",
                "source": "openalex",
                "source_native_id": f"oa_test_fallback_{run.id}",
                "openalex_id": f"oa_test_fallback_{run.id}",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "seed_search",
                "parent_source_id": None,
            }
        ]
        _ingest_candidates(db, run.id, 1, candidates, ai_filter=FailingAIFilter(), ai_policy_no_ai=False)
        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None
        assert source.final_decision == "needs_review"
        assert source.decision_source == "fallback_heuristic"


def test_ai_policy_no_ai_sets_needs_review_and_policy_source():
    class StubAIFilter:
        def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
            return AIRelevanceResult(decision="auto_accept", confidence=0.99, reason="ignored")

    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        candidates = [
            {
                "title": "UPW process control for semiconductor wafer cleaning",
                "year": 2021,
                "url": f"https://example.org/test-policy-no-ai/{run.id}",
                "doi": None,
                "abstract": "ultrapure water UPW semiconductor RO EDI UV254",
                "source": "openalex",
                "source_native_id": f"oa_test_policy_no_ai_{run.id}",
                "openalex_id": f"oa_test_policy_no_ai_{run.id}",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "seed_search",
                "parent_source_id": None,
            }
        ]
        _ingest_candidates(db, run.id, 1, candidates, ai_filter=StubAIFilter(), ai_policy_no_ai=True)
        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None
        assert source.final_decision == "needs_review"
        assert source.decision_source == "policy_no_ai"


def test_ai_auth_error_fallback_increments_observability_counter():
    class AuthFailAIFilter:
        def __init__(self) -> None:
            self._failed = False
            self._error_category = None

        def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
            if not self._failed:
                self._failed = True
                self._error_category = "auth_error"
            return None

        def pop_last_error_category(self):  # noqa: ANN201
            value = self._error_category
            self._error_category = None
            return value

        def consume_runtime_warning(self):  # noqa: ANN201
            if self._failed:
                self._failed = False
                return "auth warning"
            return None

    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        observability = RunObservability()
        candidates = [
            {
                "title": "UPW process control for semiconductor wafer cleaning",
                "year": 2021,
                "url": f"https://example.org/test-ai-auth-fallback/{run.id}",
                "doi": None,
                "abstract": "ultrapure water UPW semiconductor RO EDI UV254",
                "source": "openalex",
                "source_native_id": f"oa_test_auth_fallback_{run.id}",
                "openalex_id": f"oa_test_auth_fallback_{run.id}",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "seed_search",
                "parent_source_id": None,
            }
        ]
        _ingest_candidates(
            db,
            run.id,
            1,
            candidates,
            ai_filter=AuthFailAIFilter(),
            ai_policy_no_ai=False,
            observability=observability,
        )
        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None
        assert source.final_decision == "needs_review"
        assert source.decision_source == "fallback_heuristic"
        counters = observability.snapshot()["counters"]
        assert counters.get("ai_auth_error", 0) == 1


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


def test_execute_run_uses_run_level_ai_when_global_ai_disabled(monkeypatch):
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    try:
        object.__setattr__(settings, "use_ai_filter", False)
        object.__setattr__(settings, "ai_api_key", "token")
        monkeypatch.setattr(
            "knowledge_miner.ai_filter.AIRelevanceFilter.evaluate",
            lambda self, *, title, abstract, base_score, base_decision: AIRelevanceResult(  # noqa: ARG005
                decision="auto_accept",
                confidence=0.95,
                reason="run-level-ai",
            ),
        )
        with SessionLocal() as db:
            run = create_run(db, ["upw"], max_iterations=1, ai_filter_enabled=True)
            assert run.ai_filter_active is True
            execute_run(db, run)
            rows = db.scalars(select(Source).where(Source.run_id == run.id)).all()
            assert rows
            assert any(row.decision_source == "ai" for row in rows)
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)


def test_execute_run_ai_enabled_missing_key_routes_policy_no_ai_with_warning(caplog):
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    try:
        object.__setattr__(settings, "use_ai_filter", True)
        object.__setattr__(settings, "ai_api_key", "token")
        with SessionLocal() as db:
            run = create_run(db, ["upw"], max_iterations=1, ai_filter_enabled=True)
            assert run.ai_filter_active is True
            object.__setattr__(settings, "ai_api_key", None)
            caplog.set_level("INFO", logger="knowledge_miner")
            execute_run(db, run)
            db.refresh(run)
            assert run.ai_filter_warning is not None
            assert "missing at execution time" in run.ai_filter_warning
            rows = db.scalars(select(Source).where(Source.run_id == run.id)).all()
            assert rows
            assert all(row.final_decision == "needs_review" for row in rows)
            assert all(row.decision_source == "policy_no_ai" for row in rows)

        provider_events = []
        summaries = []
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
                summaries.append(payload)
        assert any(
            event.get("provider") == "ai_filter"
            and event.get("operation") == "evaluate"
            and event.get("ok") is False
            and event.get("error") == "missing_config"
            for event in provider_events
        )
        assert summaries
        counters = summaries[-1]["counters"]
        assert counters.get("ai_provider_error", 0) >= 1
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)


def test_execute_run_ai_disabled_run_ignores_global_ai_and_makes_no_ai_calls(caplog, monkeypatch):
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    try:
        object.__setattr__(settings, "use_ai_filter", True)
        object.__setattr__(settings, "ai_api_key", "token")

        def _should_not_call(*args, **kwargs):  # noqa: ANN002,ANN003
            raise AssertionError("AI evaluate should not be called for run-level AI disabled")

        monkeypatch.setattr("knowledge_miner.ai_filter.AIRelevanceFilter.evaluate", _should_not_call)
        with SessionLocal() as db:
            run = create_run(db, ["upw"], max_iterations=1, ai_filter_enabled=False)
            assert run.ai_filter_active is False
            caplog.set_level("INFO", logger="knowledge_miner")
            execute_run(db, run)
            rows = db.scalars(select(Source).where(Source.run_id == run.id)).all()
            assert rows
            assert all(row.decision_source == "policy_no_ai" for row in rows)
            assert all(row.final_decision == "needs_review" for row in rows)

        provider_events = []
        for rec in caplog.records:
            if rec.name != "knowledge_miner":
                continue
            try:
                payload = json.loads(rec.message)
            except json.JSONDecodeError:
                continue
            if payload.get("event") == "provider_call":
                provider_events.append(payload)
        assert not any(event.get("provider") == "ai_filter" and event.get("operation") == "evaluate" for event in provider_events)
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)


def test_cross_run_canonical_id_collision_does_not_fail():
    candidates = [
        {
            "title": "UPW process control for semiconductor fabs",
            "year": 2023,
            "url": "https://example.org/collision",
            "doi": "10.1000/collision",
            "abstract": "UPW and semiconductor process control",
            "source": "openalex",
            "source_native_id": "oa_collision",
            "openalex_id": "oa_collision",
            "semantic_scholar_id": None,
            "patent_office": None,
            "patent_number": None,
            "type": "academic",
            "discovery_method": "seed_search",
            "parent_source_id": None,
        }
    ]
    with SessionLocal() as db:
        run1 = create_run(db, ["upw"], max_iterations=1)
        run2 = create_run(db, ["upw"], max_iterations=1)
        _ingest_candidates(db, run1.id, 1, candidates)
        _ingest_candidates(db, run2.id, 1, candidates)

        rows = db.scalars(select(Source).where(Source.doi == "10.1000/collision").order_by(Source.run_id.asc())).all()
        assert len(rows) == 2
        assert rows[0].run_id != rows[1].run_id
        assert rows[0].id != rows[1].id
