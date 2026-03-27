from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import time

from sqlalchemy import select

from knowledge_miner.ai_filter import AIRelevanceResult
from knowledge_miner.config import settings
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.discovery import (
    _checkpoint_path,
    _load_checkpoint,
    _save_citation_checkpoint,
    _expand_citations_for_parent_unbounded,
    _ingest_candidates,
    _rank_citation_candidates,
    create_run,
    execute_citation_iteration_run,
    execute_run,
    export_sources_raw,
    mark_interrupted_runs_on_startup,
    resume_queued_discovery_runs,
    resume_stale_citation_runs,
    review_source,
    session_citation_parent_ids,
)
from knowledge_miner.models import CitationEdge, CitationExpansionParent, DiscoveryCitationSeed, DiscoveryRunQuery, Run, Source
from knowledge_miner.observability import RunObservability


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    checkpoint_dir = Path(settings.runtime_state_dir) / "citation_checkpoints"
    if checkpoint_dir.exists():
        for path in checkpoint_dir.glob("*.json"):
            path.unlink(missing_ok=True)


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


def test_citation_expansion_uses_cross_provider_resolution_for_openalex_parent():
    class StubConnector:
        def __init__(self, name: str, rows: list[dict]) -> None:
            self.name = name
            self._rows = rows

        def expand_citations(self, source: Source, *, per_direction_limit: int, iteration: int):  # noqa: ANN001
            del source, per_direction_limit, iteration
            return self._rows, []

    parent = Source(
        id="doi:10.1/example-parent",
        run_id="run_test",
        title="Parent paper",
        year=2024,
        url="https://doi.org/10.1/example-parent",
        doi="10.1/example-parent",
        abstract="parent abstract",
        journal=None,
        authors=[],
        citation_count=1,
        type="academic",
        source="openalex",
        source_native_id="https://openalex.org/W123",
        patent_office=None,
        patent_number=None,
        iteration=1,
        discovery_method="seed_search",
        relevance_score=0.9,
        accepted=True,
        review_status="human_accept",
        final_decision="human_accept",
        decision_source="human_review",
        heuristic_recommendation="accept",
        heuristic_score=0.9,
        ai_decision=None,
        ai_confidence=None,
        parent_source_id=None,
        provenance_history=[],
    )
    openalex_candidate = {
        "title": "Shared citing paper",
        "year": 2023,
        "url": "https://example.org/shared",
        "doi": "10.2/shared",
        "abstract": None,
        "source": "openalex",
        "source_native_id": "https://openalex.org/W999",
        "openalex_id": "https://openalex.org/W999",
        "semantic_scholar_id": None,
        "patent_office": None,
        "patent_number": None,
        "type": "academic",
        "discovery_method": "backward_citation",
        "parent_source_id": parent.id,
    }
    semantic_candidate = {
        **openalex_candidate,
        "source": "semantic_scholar",
        "source_native_id": "semantic_shared",
        "openalex_id": None,
        "semantic_scholar_id": "semantic_shared",
        "abstract": "richer abstract",
    }
    unique_semantic_candidate = {
        **semantic_candidate,
        "title": "Semantic-only citing paper",
        "doi": "10.2/semantic-only",
        "url": "https://example.org/semantic-only",
        "semantic_scholar_id": "semantic_only",
    }

    candidates, edges, provider_counts, provider_errors, citation_metrics = _expand_citations_for_parent_unbounded(
        run_id="run_test",
        parent=parent,
        connectors_by_name={
            "openalex": StubConnector("openalex", [openalex_candidate]),
            "semantic_scholar": StubConnector("semantic_scholar", [semantic_candidate, unique_semantic_candidate]),
        },
        observability=RunObservability(),
        iteration=1,
    )

    assert provider_counts["openalex"] == 1
    assert provider_counts["semantic_scholar"] == 2
    assert len(candidates) == 2
    assert {candidate["doi"] for candidate in candidates} == {"10.2/shared", "10.2/semantic-only"}
    shared = next(candidate for candidate in candidates if candidate["doi"] == "10.2/shared")
    assert shared["source"] == "semantic_scholar"
    assert shared["abstract"] == "richer abstract"
    assert len(edges) == 2
    assert provider_errors == {}
    assert citation_metrics["provider_direction_counts"] == {
        "openalex_backward": 1,
        "openalex_forward": 0,
        "semantic_scholar_backward": 2,
        "semantic_scholar_forward": 0,
    }
    assert citation_metrics["direction_overlap_counts"] == {"backward_overlap": 1, "forward_overlap": 0}
    assert citation_metrics["direction_deduped_counts"] == {"backward_deduped": 2, "forward_deduped": 0}


def test_execute_run_records_per_provider_statuses(monkeypatch):
    class StubConnector:
        def __init__(self, name: str, rows: list[dict] | None = None, error: Exception | None = None) -> None:
            self.name = name
            self._rows = rows or []
            self._error = error

        def search(self, query: str, *, run_id: str, iteration: int):  # noqa: ANN001
            del query, run_id, iteration
            if self._error is not None:
                raise self._error
            return list(self._rows)

        def expand_citations(self, source: Source, *, per_direction_limit: int, iteration: int):  # noqa: ANN001
            del source, per_direction_limit, iteration
            return [], []

    openalex_rows = [
        {
            "title": "OpenAlex result",
            "year": 2024,
            "url": "https://example.org/openalex-result",
            "doi": "10.1/openalex-result",
            "abstract": "result",
            "source": "openalex",
            "source_native_id": "oa1",
            "openalex_id": "oa1",
            "semantic_scholar_id": None,
            "patent_office": None,
            "patent_number": None,
            "type": "academic",
            "discovery_method": "seed_search",
            "parent_source_id": None,
        }
    ]
    monkeypatch.setattr(
        "knowledge_miner.discovery.build_connectors",
        lambda provider_limits=None: [  # noqa: ARG005
            StubConnector("openalex", rows=openalex_rows),
            StubConnector("semantic_scholar", error=Exception("provider_transient_http_429")),
            StubConnector("brave", rows=[]),
        ],
    )

    with SessionLocal() as db:
        run = create_run(db, ["semiconductor wastewater"], max_iterations=1)
        execute_run(db, run)
        query_row = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query_row is not None
        assert query_row.openalex_status == "ok"
        assert query_row.semantic_scholar_status == "rate_limited"
        assert query_row.brave_status == "empty"
        assert query_row.semantic_scholar_error_message == "provider_transient_http_429"


def test_execute_citation_iteration_emits_parent_provider_comparison(monkeypatch):
    recorded: list[dict] = []

    def capture(self, **payload):  # noqa: ANN001
        recorded.append(payload)

    monkeypatch.setattr(RunObservability, "record_citation_parent_counts", capture)
    monkeypatch.setattr(
        "knowledge_miner.discovery._expand_citations_for_parent_unbounded",
        lambda **kwargs: (  # noqa: ARG005
            [
                {
                    "title": "Shared citation",
                    "year": 2024,
                    "url": "https://example.org/shared-citation",
                    "doi": "10.2/shared-citation",
                    "abstract": "test abstract",
                    "source": "semantic_scholar",
                    "source_native_id": "ss_shared",
                    "openalex_id": None,
                    "semantic_scholar_id": "ss_shared",
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "backward_citation",
                    "parent_source_id": "parent_source_1",
                }
            ],
            [("parent_source_1", "doi:10.2/shared-citation", "cites")],
            {"openalex": 3, "semantic_scholar": 2},
            {},
            {
                "provider_direction_counts": {
                    "openalex_backward": 3,
                    "openalex_forward": 0,
                    "semantic_scholar_backward": 2,
                    "semantic_scholar_forward": 0,
                },
                "direction_overlap_counts": {"backward_overlap": 1, "forward_overlap": 0},
                "direction_deduped_counts": {"backward_deduped": 1, "forward_deduped": 0},
            },
        ),
    )
    monkeypatch.setattr("knowledge_miner.discovery.session_citation_parent_ids", lambda *args, **kwargs: ["parent_source_1"])
    with SessionLocal() as db:
        source_run = create_run(db, ["ultrapure water semiconductor"], max_iterations=1)
        source_run.current_iteration = 1
        parent = Source(
            id="parent_source_1",
            run_id=source_run.id,
            title="Parent paper",
            year=2024,
            url="https://doi.org/10.1/parent",
            doi="10.1/parent",
            abstract="parent abstract",
            journal=None,
            authors=[],
            citation_count=1,
            type="academic",
            source="openalex",
            source_native_id="oa_parent",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=0.9,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="accept",
            heuristic_score=0.9,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add(parent)
        db.commit()
        execute_citation_iteration_run(db, source_run, source_run_id=source_run.id)

    assert recorded
    payload = recorded[0]
    assert payload["run_id"] == source_run.id
    assert payload["query_id"].startswith("run_query_")
    assert payload["parent_source_id"] == "parent_source_1"
    assert payload["parent_title"] == "Parent paper"
    assert payload["parent_doi"] == "10.1/parent"
    assert payload["provider_counts"] == {"openalex": 3, "semantic_scholar": 2}
    assert payload["provider_direction_counts"] == {
        "openalex_backward": 3,
        "openalex_forward": 0,
        "semantic_scholar_backward": 2,
        "semantic_scholar_forward": 0,
    }
    assert payload["direction_overlap_counts"] == {"backward_overlap": 1, "forward_overlap": 0}
    assert payload["direction_deduped_counts"] == {"backward_deduped": 1, "forward_deduped": 0}
    assert payload["raw_total"] == 5
    assert payload["deduped_candidates"] == 1
    assert payload["edge_count"] == 1


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


def test_resume_queued_discovery_runs_enqueues_existing_runs(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    monkeypatch.setattr("knowledge_miner.discovery.enqueue_run", lambda run_id: enqueued.append(run_id))
    with SessionLocal() as db:
        first = create_run(db, ["upw"], max_iterations=1)
        first_id = first.id
        second = create_run(db, ["semiconductor"], max_iterations=1)
        second.status = "completed"
        db.commit()

    resumed = resume_queued_discovery_runs()
    assert resumed == [first_id]
    assert enqueued == [first_id]


def test_resume_queued_discovery_runs_recovers_stale_running_searching_runs(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    monkeypatch.setattr("knowledge_miner.discovery.enqueue_run", lambda run_id: enqueued.append(run_id))
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        run.status = "running"
        query = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query is not None
        query.status = "searching"
        query.discovered_count = 7
        query.openalex_count = 5
        query.processing_count = 7
        query.started_at = datetime.now(UTC)
        db.commit()
        run_id = run.id
        query_id = query.id

    resumed = resume_queued_discovery_runs()
    assert resumed == [run_id]
    assert enqueued == [run_id]

    with SessionLocal() as db:
        refreshed_run = db.get(Run, run_id)
        refreshed_query = db.get(DiscoveryRunQuery, query_id)
        assert refreshed_run is not None
        assert refreshed_query is not None
        assert refreshed_run.status == "queued"
        assert refreshed_query.status == "waiting"
        assert refreshed_query.discovered_count == 0
        assert refreshed_query.openalex_count == 0
        assert refreshed_query.processing_count == 0
        assert refreshed_query.started_at is None


def test_resume_queued_discovery_runs_does_not_reset_citation_expansion_runs(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    monkeypatch.setattr("knowledge_miner.discovery.enqueue_run", lambda run_id: enqueued.append(run_id))
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        run.status = "running"
        query = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query is not None
        query.query_text = "citation expansion"
        query.status = "searching"
        db.commit()
        run_id = run.id
        query_id = query.id

    resumed = resume_queued_discovery_runs()
    assert resumed == []
    assert enqueued == []


def test_resume_queued_discovery_runs_recovers_mixed_running_runs_without_resetting_completed_queries(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    monkeypatch.setattr("knowledge_miner.discovery.enqueue_run", lambda run_id: enqueued.append(run_id))
    with SessionLocal() as db:
        run = create_run(db, ["upw", "reuse"], max_iterations=1)
        run.status = "running"
        queries = db.scalars(
            select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id).order_by(DiscoveryRunQuery.position.asc())
        ).all()
        assert len(queries) == 2
        queries[0].status = "completed"
        queries[0].accepted_count = 3
        queries[0].completed_at = datetime.now(UTC)
        queries[1].status = "ranking_relevance"
        queries[1].discovered_count = 11
        queries[1].openalex_count = 7
        queries[1].processing_count = 11
        queries[1].started_at = datetime.now(UTC)
        db.commit()
        run_id = run.id
        completed_query_id = queries[0].id
        stale_query_id = queries[1].id

    resumed = resume_queued_discovery_runs()
    assert resumed == [run_id]
    assert enqueued == [run_id]

    with SessionLocal() as db:
        refreshed_run = db.get(Run, run_id)
        completed_query = db.get(DiscoveryRunQuery, completed_query_id)
        stale_query = db.get(DiscoveryRunQuery, stale_query_id)
        assert refreshed_run is not None
        assert completed_query is not None
        assert stale_query is not None
        assert refreshed_run.status == "queued"
        assert completed_query.status == "completed"
        assert completed_query.accepted_count == 3
        assert stale_query.status == "waiting"
        assert stale_query.discovered_count == 0
        assert stale_query.openalex_count == 0
        assert stale_query.processing_count == 0
        assert stale_query.started_at is None


def test_mark_interrupted_runs_on_startup_marks_discovery_runs_failed_without_resuming(monkeypatch):
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        run.status = "running"
        query = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query is not None
        query.status = "ranking_relevance"
        query.processing_count = 9
        db.commit()
        run_id = run.id
        query_id = query.id

    interrupted = mark_interrupted_runs_on_startup()
    assert interrupted == [run_id]

    with SessionLocal() as db:
        refreshed_run = db.get(Run, run_id)
        refreshed_query = db.get(DiscoveryRunQuery, query_id)
        assert refreshed_run is not None
        assert refreshed_query is not None
        assert refreshed_run.status == "failed"
        assert "server restarted" in str(refreshed_run.error_message)
        assert refreshed_query.status == "failed"
        assert refreshed_query.processing_count == 0
        assert refreshed_query.checkpoint_state == "none"


def test_mark_interrupted_runs_on_startup_keeps_citation_resume_available(monkeypatch):
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        run.status = "running"
        query = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query is not None
        query.query_text = "citation expansion"
        query.status = "ranking_relevance"
        query.checkpoint_state = "running"
        query.processing_count = 22
        db.commit()
        run_id = run.id
        query_id = query.id

    interrupted = mark_interrupted_runs_on_startup()
    assert interrupted == [run_id]

    with SessionLocal() as db:
        refreshed_run = db.get(Run, run_id)
        refreshed_query = db.get(DiscoveryRunQuery, query_id)
        assert refreshed_run is not None
        assert refreshed_query is not None
        assert refreshed_run.status == "failed"
        assert "Resume citation expansion manually." in str(refreshed_run.error_message)
        assert refreshed_query.status == "failed"
        assert refreshed_query.checkpoint_state == "resumable"
        assert refreshed_query.processing_count == 0


def test_resume_stale_citation_runs_auto_recovers_snapshot_checkpoint(monkeypatch):
    enqueued: list[tuple[str, str]] = []
    bookmark_enqueued: list[str] = []
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    monkeypatch.setattr(
        "knowledge_miner.discovery.enqueue_citation_iteration_run",
        lambda run_id, *, source_run_id: enqueued.append((run_id, source_run_id)),
    )
    monkeypatch.setattr("knowledge_miner.discovery.enqueue_bookmark_seed_run", lambda run_id: bookmark_enqueued.append(run_id))
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        run.status = "running"
        run.current_iteration = 1
        run.expanded_candidates_total = 99
        run.citation_edges_total = 55
        run.accepted_total = 12
        run.new_accept_rate = 0.4
        query = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query is not None
        query.query_text = "citation expansion"
        query.status = "ranking_relevance"
        query.checkpoint_state = "running"
        query.discovered_count = 50
        query.openalex_count = 30
        query.semantic_scholar_count = 20
        query.brave_count = 0
        query.accepted_count = 5
        query.rejected_count = 10
        query.pending_count = 2
        query.processing_count = 33
        query.scope_processed_parents = 7
        query.scope_total_parents = 10
        query.updated_at = datetime.now(UTC) - timedelta(minutes=5)
        _save_citation_checkpoint(
            run,
            query,
            processed_parents=3,
            remaining_parent_ids=["parent-4", "parent-5"],
        )
        checkpoint = _load_checkpoint(run.id, query.id)
        assert checkpoint is not None
        query_state = checkpoint["query_state"]
        run_state = checkpoint["run_state"]
        query_state["discovered_count"] = 11
        query_state["openalex_count"] = 8
        query_state["semantic_scholar_count"] = 3
        query_state["accepted_count"] = 4
        query_state["rejected_count"] = 6
        query_state["pending_count"] = 1
        query_state["scope_processed_parents"] = 3
        run_state["expanded_candidates_total"] = 11
        run_state["citation_edges_total"] = 4
        run_state["accepted_total"] = 4
        run_state["new_accept_rate"] = 0.25
        checkpoint_path = _checkpoint_path(run.id, query.id)
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        db.commit()
        run_id = run.id
        query_id = query.id

    resumed = resume_stale_citation_runs()
    assert resumed == [run_id]
    assert enqueued == [(run_id, run_id)]
    assert bookmark_enqueued == []

    with SessionLocal() as db:
        refreshed_run = db.get(Run, run_id)
        refreshed_query = db.get(DiscoveryRunQuery, query_id)
        assert refreshed_run is not None
        assert refreshed_query is not None
        assert refreshed_run.status == "queued"
        assert refreshed_run.expanded_candidates_total == 11
        assert refreshed_run.citation_edges_total == 4
        assert refreshed_run.accepted_total == 4
        assert refreshed_query.status == "searching"
        assert refreshed_query.checkpoint_state == "resumable"
        assert refreshed_query.processing_count == 0
        assert refreshed_query.discovered_count == 11
        assert refreshed_query.openalex_count == 8
        assert refreshed_query.semantic_scholar_count == 3
        assert refreshed_query.accepted_count == 4
        assert refreshed_query.rejected_count == 6
        assert refreshed_query.pending_count == 1
        assert refreshed_query.scope_processed_parents == 3


def test_resume_stale_citation_runs_marks_legacy_checkpoint_manual_resume(monkeypatch):
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr("knowledge_miner.discovery.is_primary_instance", lambda: True)
    monkeypatch.setattr(
        "knowledge_miner.discovery.enqueue_citation_iteration_run",
        lambda run_id, *, source_run_id: enqueued.append((run_id, source_run_id)),
    )
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        run.status = "running"
        query = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query is not None
        query.query_text = "citation expansion"
        query.status = "ranking_relevance"
        query.checkpoint_state = "running"
        query.processing_count = 21
        query.updated_at = datetime.now(UTC) - timedelta(minutes=5)
        checkpoint_path = _checkpoint_path(run.id, query.id)
        checkpoint_path.write_text(
            json.dumps(
                {
                    "processed_parents": 2,
                    "remaining_parent_ids": ["parent-3"],
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        db.commit()
        run_id = run.id
        query_id = query.id

    resumed = resume_stale_citation_runs()
    assert resumed == []
    assert enqueued == []

    with SessionLocal() as db:
        refreshed_run = db.get(Run, run_id)
        refreshed_query = db.get(DiscoveryRunQuery, query_id)
        assert refreshed_run is not None
        assert refreshed_query is not None
        assert refreshed_run.status == "failed"
        assert refreshed_run.error_message == "recovery_requires_manual_resume_legacy_checkpoint"
        assert refreshed_query.status == "failed"
        assert refreshed_query.checkpoint_state == "resumable"
        assert refreshed_query.processing_count == 0
        assert refreshed_query.error_message == "recovery_requires_manual_resume_legacy_checkpoint"


def test_save_citation_checkpoint_persists_safe_snapshot():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        run.status = "running"
        run.current_iteration = 1
        run.expanded_candidates_total = 12
        run.citation_edges_total = 7
        run.accepted_total = 3
        run.new_accept_rate = 0.5
        query = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.run_id == run.id)).first()
        assert query is not None
        query.query_text = "citation expansion"
        query.status = "searching"
        query.checkpoint_state = "running"
        query.discovered_count = 12
        query.openalex_count = 9
        query.semantic_scholar_count = 3
        query.accepted_count = 3
        query.rejected_count = 4
        query.pending_count = 1
        query.processing_count = 99
        query.scope_processed_parents = 2
        query.scope_total_parents = 5

        _save_citation_checkpoint(run, query, processed_parents=2, remaining_parent_ids=["parent-3", "parent-4", "parent-5"])
        payload = _load_checkpoint(run.id, query.id)

    assert payload is not None
    assert payload["processed_parents"] == 2
    assert payload["remaining_parent_ids"] == ["parent-3", "parent-4", "parent-5"]
    assert payload["query_state"]["processing_count"] == 0
    assert payload["query_state"]["scope_processed_parents"] == 2
    assert payload["run_state"]["expanded_candidates_total"] == 12
    assert payload["run_state"]["citation_edges_total"] == 7


def test_session_citation_parent_ids_uses_session_scope_and_dedups():
    with SessionLocal() as db:
        first = create_run(db, ["upw"], max_iterations=1, session_id="session_scope", session_context="ctx")
        second = create_run(db, ["semi"], max_iterations=1, session_id="session_scope", session_context="ctx")
        src1 = Source(
            id="s1",
            run_id=first.id,
            title="CMP review",
            year=2021,
            url="https://example.org/cmp",
            doi="10.1000/cmp",
            abstract="a",
            journal="j",
            authors=[],
            citation_count=10,
            type="academic",
            source="openalex",
            source_native_id="oa1",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=5,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="needs_review",
            heuristic_score=5,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        src2 = Source(
            id="s2",
            run_id=second.id,
            title="CMP review",
            year=2021,
            url="https://example.org/cmp-new",
            doi="10.1000/cmp",
            abstract="b",
            journal="j",
            authors=[],
            citation_count=12,
            type="academic",
            source="openalex",
            source_native_id="oa2",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=8,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="needs_review",
            heuristic_score=8,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        src3 = Source(
            id="s3",
            run_id=first.id,
            title="Wastewater recovery",
            year=2020,
            url="https://example.org/recovery",
            doi=None,
            abstract="c",
            journal="j",
            authors=[],
            citation_count=5,
            type="academic",
            source="brave",
            source_native_id="b1",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=4,
            accepted=True,
            review_status="auto_accept",
            final_decision="auto_accept",
            decision_source="ai",
            heuristic_recommendation="auto_accept",
            heuristic_score=4,
            ai_decision="auto_accept",
            ai_confidence=0.9,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add_all([src1, src2, src3])
        db.commit()

        parent_ids = session_citation_parent_ids(db, target_run_id=second.id, session_id="session_scope")
        assert set(parent_ids) == {"s2", "s3"}


def test_session_citation_parent_ids_excludes_same_context_already_expanded_identity():
    with SessionLocal() as db:
        first = create_run(db, ["upw"], max_iterations=1, session_id="session_ctx_parents", session_context="ctx")
        second = create_run(db, ["semi"], max_iterations=1, session_id="session_ctx_parents", session_context="ctx")
        src1 = Source(
            id="s_ctx_old",
            run_id=first.id,
            title="CMP review",
            year=2021,
            url="https://example.org/cmp-old",
            doi="10.1000/cmp-same",
            abstract="a",
            journal="j",
            authors=[],
            citation_count=10,
            type="academic",
            source="openalex",
            source_native_id="oa_old",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=5,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="needs_review",
            heuristic_score=5,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        src2 = Source(
            id="s_ctx_new",
            run_id=second.id,
            title="CMP review",
            year=2021,
            url="https://example.org/cmp-new",
            doi="10.1000/cmp-same",
            abstract="b",
            journal="j",
            authors=[],
            citation_count=12,
            type="academic",
            source="openalex",
            source_native_id="oa_new",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=8,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="needs_review",
            heuristic_score=8,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add_all([src1, src2])
        db.add(
            CitationExpansionParent(
                run_id=first.id,
                parent_source_id=src1.id,
                session_id=first.session_id,
                session_context_key=first.session_context_key,
                query_id="query_old",
            )
        )
        db.commit()

        parent_ids = session_citation_parent_ids(db, target_run_id=second.id, session_id="session_ctx_parents")
        assert parent_ids == []


def test_session_citation_parent_ids_renews_all_accepted_after_context_change():
    with SessionLocal() as db:
        first = create_run(db, ["upw"], max_iterations=1, session_id="session_ctx_reset", session_context="ctx one")
        second = create_run(db, ["semi"], max_iterations=1, session_id="session_ctx_reset", session_context="ctx two")
        src1 = Source(
            id="s_ctx_reset_old",
            run_id=first.id,
            title="CMP review",
            year=2021,
            url="https://example.org/cmp-reset-old",
            doi="10.1000/cmp-reset",
            abstract="a",
            journal="j",
            authors=[],
            citation_count=10,
            type="academic",
            source="openalex",
            source_native_id="oa_reset_old",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=5,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="needs_review",
            heuristic_score=5,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        src2 = Source(
            id="s_ctx_reset_new",
            run_id=second.id,
            title="CMP review",
            year=2021,
            url="https://example.org/cmp-reset-new",
            doi="10.1000/cmp-reset",
            abstract="b",
            journal="j",
            authors=[],
            citation_count=12,
            type="academic",
            source="openalex",
            source_native_id="oa_reset_new",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=8,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="needs_review",
            heuristic_score=8,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add_all([src1, src2])
        db.add(
            CitationExpansionParent(
                run_id=first.id,
                parent_source_id=src1.id,
                session_id=first.session_id,
                session_context_key=first.session_context_key,
                query_id="query_old",
            )
        )
        db.commit()

        parent_ids = session_citation_parent_ids(db, target_run_id=second.id, session_id="session_ctx_reset")
        assert parent_ids == ["s_ctx_reset_new"]


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


def test_session_human_reviewed_duplicates_are_suppressed_across_context_changes():
    with SessionLocal() as db:
        first = create_run(db, ["upw"], max_iterations=1, session_id="session_human_dedup", session_context="ctx one")
        second = create_run(db, ["upw"], max_iterations=1, session_id="session_human_dedup", session_context="ctx two")
        existing = Source(
            id="doi:10.1000/human-dedup",
            run_id=first.id,
            title="Water recovery in semiconductor fabs",
            year=2021,
            url="https://example.org/human-dedup/first",
            doi="10.1000/human-dedup",
            abstract="first abstract",
            journal="Journal",
            authors=[],
            citation_count=3,
            type="academic",
            source="openalex",
            source_native_id="oa_human_dedup",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=5,
            accepted=False,
            review_status="human_reject",
            final_decision="human_reject",
            decision_source="human_review",
            heuristic_recommendation="needs_review",
            heuristic_score=5,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add(existing)
        db.commit()

        _ingest_candidates(
            db,
            second.id,
            1,
            [
                {
                    "title": "Water recovery in semiconductor fabs",
                    "year": 2021,
                    "url": "https://example.org/human-dedup/second",
                    "doi": "10.1000/human-dedup",
                    "abstract": "second abstract",
                    "source": "semantic_scholar",
                    "source_native_id": "ss_human_dedup",
                    "openalex_id": None,
                    "semantic_scholar_id": "ss_human_dedup",
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "query_expansion",
                    "parent_source_id": None,
                }
            ],
        )

        rows = db.scalars(select(Source).where(Source.doi == "10.1000/human-dedup").order_by(Source.created_at.asc())).all()
        assert len(rows) == 1
        assert rows[0].run_id == first.id
        assert len(rows[0].provenance_history) == 1


def test_session_auto_reviewed_duplicates_can_reenter_after_context_change():
    with SessionLocal() as db:
        first = create_run(db, ["upw"], max_iterations=1, session_id="session_auto_dedup", session_context="ctx one")
        second = create_run(db, ["upw"], max_iterations=1, session_id="session_auto_dedup", session_context="ctx two")
        existing = Source(
            id="doi:10.1000/auto-dedup",
            run_id=first.id,
            title="Membrane train for wastewater polishing",
            year=2020,
            url="https://example.org/auto-dedup/first",
            doi="10.1000/auto-dedup",
            abstract="first abstract",
            journal="Journal",
            authors=[],
            citation_count=4,
            type="academic",
            source="openalex",
            source_native_id="oa_auto_dedup",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=2,
            accepted=False,
            review_status="auto_reject",
            final_decision="auto_reject",
            decision_source="ai",
            heuristic_recommendation="auto_reject",
            heuristic_score=2,
            ai_decision="auto_reject",
            ai_confidence=0.2,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add(existing)
        db.commit()

        _ingest_candidates(
            db,
            second.id,
            1,
            [
                {
                    "title": "Membrane train for wastewater polishing",
                    "year": 2020,
                    "url": "https://example.org/auto-dedup/second",
                    "doi": "10.1000/auto-dedup",
                    "abstract": "second abstract",
                    "source": "semantic_scholar",
                    "source_native_id": "ss_auto_dedup",
                    "openalex_id": None,
                    "semantic_scholar_id": "ss_auto_dedup",
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "query_expansion",
                    "parent_source_id": None,
                }
            ],
        )

        rows = db.scalars(select(Source).where(Source.doi == "10.1000/auto-dedup").order_by(Source.created_at.asc())).all()
        assert len(rows) == 2
        assert {row.run_id for row in rows} == {first.id, second.id}


def test_ingest_candidates_nulls_out_of_range_historical_year():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        candidates = [
            {
                "title": "V. On Stokes's current function",
                "year": 1891,
                "url": "https://doi.org/10.1098/rspl.1890.0064",
                "doi": "10.1098/rspl.1890.0064",
                "abstract": "Historical citation candidate.",
                "journal": "Proceedings of the Royal Society of London",
                "authors": ["Ralph Allen Sampson"],
                "citation_count": 2,
                "source": "openalex",
                "source_native_id": "https://openalex.org/W2991042217",
                "openalex_id": "https://openalex.org/W2991042217",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "backward_citation",
                "parent_source_id": "doi:10.1063/1.4962304",
            }
        ]

        _ingest_candidates(db, run.id, 1, candidates)
        source = db.scalars(select(Source).where(Source.run_id == run.id)).first()

        assert source is not None
        assert source.year is None
        assert source.doi == "10.1098/rspl.1890.0064"


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


def test_ai_watchdog_timeout_falls_back_without_hanging():
    class HungAIFilter:
        timeout_seconds = 0.01

        def __init__(self) -> None:
            self._error_category = None

        def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
            time.sleep(1.2)
            return AIRelevanceResult(decision="auto_accept", confidence=0.99, reason="late")

        def pop_last_error_category(self):  # noqa: ANN201
            value = self._error_category
            self._error_category = None
            return value

        def mark_timeout(self) -> None:
            self._error_category = "timeout"

        def consume_runtime_warning(self):  # noqa: ANN201
            return None

    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        observability = RunObservability()
        started = time.perf_counter()
        _ingest_candidates(
            db,
            run.id,
            1,
            [
                {
                    "title": "Hung AI candidate",
                    "year": 2024,
                    "url": "https://example.org/hung-ai-candidate",
                    "doi": None,
                    "abstract": "ultrapure water semiconductor",
                    "source": "openalex",
                    "source_native_id": "oa_hung_ai",
                    "openalex_id": "oa_hung_ai",
                    "semantic_scholar_id": None,
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "seed_search",
                    "parent_source_id": None,
                }
            ],
            ai_filter=HungAIFilter(),
            ai_policy_no_ai=False,
            observability=observability,
        )
        elapsed = time.perf_counter() - started
        source = db.scalars(select(Source).where(Source.run_id == run.id).limit(1)).first()
        assert source is not None
        assert source.final_decision == "needs_review"
        assert source.decision_source == "fallback_heuristic"
        assert elapsed < 1.1
        counters = observability.snapshot()["counters"]
        assert counters.get("ai_timeout", 0) == 1


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


def test_ingest_candidates_updates_query_counts_incrementally():
    class StepwiseAIFilter:
        def __init__(self):
            self.calls = 0

        def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
            self.calls += 1
            if self.calls == 5:
                with SessionLocal() as other_db:
                    query = other_db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.id == "query_live")).first()
                    assert query is not None
                    assert query.accepted_count + query.rejected_count + query.pending_count > 0
                    assert query.processing_count < 6
            return AIRelevanceResult(decision="needs_review", confidence=0.8, reason="progress")

    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1)
        query = DiscoveryRunQuery(
            id="query_live",
            run_id=run.id,
            query_text="citation expansion",
            query_metadata={},
            position=99,
            status="ranking_relevance",
            discovered_count=6,
            openalex_count=6,
            brave_count=0,
            semantic_scholar_count=0,
            accepted_count=0,
            rejected_count=0,
            pending_count=0,
            processing_count=6,
        )
        db.add(query)
        db.commit()

        candidates = []
        for idx in range(6):
            candidates.append(
                {
                    "title": f"UPW process candidate {idx}",
                    "year": 2021,
                    "url": f"https://example.org/live-progress/{idx}",
                    "doi": None,
                    "abstract": "ultrapure water semiconductor",
                    "source": "openalex",
                    "source_native_id": f"oa_live_{idx}",
                    "openalex_id": f"oa_live_{idx}",
                    "semantic_scholar_id": None,
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "forward_citation",
                    "parent_source_id": "parent-1",
                }
            )

        _ingest_candidates(
            db,
            run.id,
            1,
            candidates,
            ai_filter=StepwiseAIFilter(),
            query_id="query_live",
            query_text="citation expansion",
        )

        refreshed = db.scalars(select(DiscoveryRunQuery).where(DiscoveryRunQuery.id == "query_live")).first()
        assert refreshed is not None
        assert refreshed.pending_count == 6
        assert refreshed.processing_count == 0


def test_citation_query_counts_accumulate_across_multiple_parent_batches():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1, session_id="session_counts", session_context="ctx")
        query = DiscoveryRunQuery(
            id="query_citation_counts",
            run_id=run.id,
            query_text="citation expansion",
            query_metadata={},
            position=99,
            status="ranking_relevance",
            discovered_count=0,
            openalex_count=0,
            brave_count=0,
            semantic_scholar_count=0,
            accepted_count=2,
            rejected_count=3,
            pending_count=1,
            processing_count=0,
        )
        db.add(query)
        db.commit()

        first_batch = [
            {
                "title": "Accepted candidate",
                "year": 2021,
                "url": "https://example.org/citation-accepted",
                "doi": None,
                "abstract": "ultrapure water semiconductor",
                "source": "openalex",
                "source_native_id": "oa_citation_accepted",
                "openalex_id": "oa_citation_accepted",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "forward_citation",
                "parent_source_id": "parent-a",
            },
            {
                "title": "Rejected candidate",
                "year": 2021,
                "url": "https://example.org/citation-rejected",
                "doi": None,
                "abstract": "",
                "source": "openalex",
                "source_native_id": "oa_citation_rejected",
                "openalex_id": "oa_citation_rejected",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "forward_citation",
                "parent_source_id": "parent-a",
            },
        ]
        second_batch = [
            {
                "title": "Pending candidate",
                "year": 2021,
                "url": "https://example.org/citation-pending",
                "doi": None,
                "abstract": "mid-confidence wastewater paper",
                "source": "openalex",
                "source_native_id": "oa_citation_pending",
                "openalex_id": "oa_citation_pending",
                "semantic_scholar_id": None,
                "patent_office": None,
                "patent_number": None,
                "type": "academic",
                "discovery_method": "forward_citation",
                "parent_source_id": "parent-b",
            }
        ]

        class ParentBatchAIFilter:
            def evaluate(self, *, title, abstract, base_score, base_decision):  # noqa: ANN001
                if "Accepted" in title:
                    return AIRelevanceResult(decision="auto_accept", confidence=0.95, reason="fit")
                if "Rejected" in title:
                    return AIRelevanceResult(decision="auto_reject", confidence=0.2, reason="poor fit")
                return AIRelevanceResult(decision="needs_review", confidence=0.6, reason="uncertain")

        stats1 = _ingest_candidates(
            db,
            run.id,
            1,
            first_batch,
            ai_filter=ParentBatchAIFilter(),
            query_id="query_citation_counts",
            query_text="citation expansion",
        )
        query_after_first = db.get(DiscoveryRunQuery, "query_citation_counts")
        assert query_after_first is not None
        query_after_first.accepted_count = 2 + stats1.accepted
        query_after_first.rejected_count = 3 + stats1.rejected
        query_after_first.pending_count = 1 + stats1.pending
        db.commit()

        _ingest_candidates(
            db,
            run.id,
            1,
            second_batch,
            ai_filter=ParentBatchAIFilter(),
            query_id="query_citation_counts",
            query_text="citation expansion",
        )
        refreshed = db.get(DiscoveryRunQuery, "query_citation_counts")
        assert refreshed is not None
        assert refreshed.accepted_count == 3
        assert refreshed.rejected_count == 4
        assert refreshed.pending_count == 2


def test_query_counts_do_not_credit_duplicates_from_other_queries():
    with SessionLocal() as db:
        run = create_run(db, ["upw"], max_iterations=1, session_id="session_dupe_counts", session_context="ctx")
        original_query = DiscoveryRunQuery(
            id="query_original",
            run_id=run.id,
            query_text="original query",
            query_metadata={},
            position=1,
            status="completed",
            discovered_count=1,
            openalex_count=1,
            brave_count=0,
            semantic_scholar_count=0,
            accepted_count=1,
            rejected_count=0,
            pending_count=0,
            processing_count=0,
        )
        duplicate_query = DiscoveryRunQuery(
            id="query_duplicate",
            run_id=run.id,
            query_text="duplicate query",
            query_metadata={},
            position=2,
            status="ranking_relevance",
            discovered_count=0,
            openalex_count=0,
            brave_count=0,
            semantic_scholar_count=0,
            accepted_count=0,
            rejected_count=0,
            pending_count=0,
            processing_count=0,
        )
        db.add_all([original_query, duplicate_query])
        db.add(
            Source(
                id="doi:10.1000/example::run:" + run.id,
                run_id=run.id,
                title="Reusable accepted paper",
                year=2024,
                url="https://example.org/reusable-accepted-paper",
                doi="10.1000/example",
                abstract="ultrapure water semiconductor",
                journal="Journal",
                authors=[],
                citation_count=0,
                type="academic",
                source="openalex",
                source_native_id="oa_existing",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=0.95,
                accepted=True,
                review_status="auto_accept",
                final_decision="auto_accept",
                decision_source="ai",
                heuristic_recommendation="accept",
                heuristic_score=0.95,
                ai_decision="auto_accept",
                ai_confidence=0.95,
                parent_source_id=None,
                provenance_history=[],
                query_id="query_original",
                query_step_number=1,
                query_source_number=1,
            )
        )
        db.commit()

        _ingest_candidates(
            db,
            run.id,
            1,
            [
                {
                    "title": "Reusable accepted paper",
                    "year": 2024,
                    "url": "https://example.org/reusable-accepted-paper",
                    "doi": "10.1000/example",
                    "abstract": "ultrapure water semiconductor",
                    "source": "openalex",
                    "source_native_id": "oa_existing",
                    "openalex_id": "oa_existing",
                    "semantic_scholar_id": None,
                    "patent_office": None,
                    "patent_number": None,
                    "type": "academic",
                    "discovery_method": "forward_citation",
                    "parent_source_id": "parent-duplicate",
                }
            ],
            query_id="query_duplicate",
            query_text="duplicate query",
        )

        refreshed = db.get(DiscoveryRunQuery, "query_duplicate")
        assert refreshed is not None
        assert refreshed.accepted_count == 0
        assert refreshed.rejected_count == 0
        assert refreshed.pending_count == 0
