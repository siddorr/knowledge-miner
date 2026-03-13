from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.models import Source


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "manual_live_logic_test.py"
SPEC = importlib.util.spec_from_file_location("manual_live_logic_test", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules["manual_live_logic_test"] = MODULE
SPEC.loader.exec_module(MODULE)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_select_top_academic_sources_prefers_ai_accepted():
    run_id = "run_logic_test"
    rows = [
        Source(
            id="src_a",
            run_id=run_id,
            title="AI accepted strong",
            year=2024,
            url="https://example.org/a",
            doi=None,
            abstract="a",
            type="academic",
            source="openalex",
            source_native_id="a",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=7.0,
            accepted=True,
            review_status="auto_accept",
            final_decision="auto_accept",
            decision_source="ai",
            heuristic_recommendation="auto_accept",
            heuristic_score=5.0,
            ai_decision="auto_accept",
            ai_confidence=0.95,
            parent_source_id=None,
            provenance_history=[],
        ),
        Source(
            id="src_b",
            run_id=run_id,
            title="Heuristic accepted",
            year=2023,
            url="https://example.org/b",
            doi=None,
            abstract="b",
            type="academic",
            source="openalex",
            source_native_id="b",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=8.0,
            accepted=True,
            review_status="auto_accept",
            final_decision="auto_accept",
            decision_source="fallback_heuristic",
            heuristic_recommendation="auto_accept",
            heuristic_score=8.0,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        ),
        Source(
            id="src_c",
            run_id=run_id,
            title="Needs review academic",
            year=2022,
            url="https://example.org/c",
            doi=None,
            abstract="c",
            type="academic",
            source="openalex",
            source_native_id="c",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=6.0,
            accepted=False,
            review_status="needs_review",
            final_decision="needs_review",
            decision_source="fallback_heuristic",
            heuristic_recommendation="needs_review",
            heuristic_score=6.0,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        ),
    ]
    with SessionLocal() as db:
        for row in rows:
            db.add(row)
        db.commit()

    with SessionLocal() as db:
        selected = MODULE._select_top_academic_sources(db, run_id, 2)
    assert [row.id for row in selected] == ["src_a", "src_b"]


def test_source_provider_mode_supports_mixed_provenance():
    source = Source(
        id="src_mode",
        run_id="run_mode",
        title="Mode source",
        year=2024,
        url="https://example.org/mode",
        doi=None,
        abstract="x",
        type="academic",
        source="openalex",
        source_native_id="oa_mode",
        patent_office=None,
        patent_number=None,
        iteration=1,
        discovery_method="seed_search",
        relevance_score=4.0,
        accepted=False,
        review_status="needs_review",
        final_decision="needs_review",
        decision_source="fallback_heuristic",
        heuristic_recommendation="needs_review",
        heuristic_score=4.0,
        ai_decision=None,
        ai_confidence=None,
        parent_source_id=None,
        provenance_history=[
            {"provider": "openalex", "source_native_id": "oa_mode"},
            {"provider": "openalex", "source_native_id": "oa_mode"},
        ],
    )
    provenance_modes = {("openalex", "oa_mode"): {"live", "fallback"}}
    assert MODULE._source_provider_mode(source, provenance_modes) == "mixed"
