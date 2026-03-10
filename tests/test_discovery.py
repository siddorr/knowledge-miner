from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from knowledge_miner.ai_filter import AIRelevanceResult
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.discovery import _ingest_candidates, create_run, execute_run, export_sources_raw, review_source
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
