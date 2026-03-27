#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.models import DiscoveryRunQuery, Run, Source


def _num(value: object, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def _int_or_none(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def restore(artifact_path: Path, *, force: bool) -> tuple[str, int]:
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid sources_raw payload: expected object")
    sources = payload.get("sources") or []
    if not isinstance(sources, list):
        raise ValueError("Invalid sources_raw payload: 'sources' must be a list")

    run_id = str(payload.get("run_id") or artifact_path.parent.name)
    seed_queries = payload.get("provenance", {}).get("seed_queries") or ["restored from artifact"]
    if not isinstance(seed_queries, list) or not seed_queries:
        seed_queries = ["restored from artifact"]

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        existing = db.get(Run, run_id)
        if existing is not None and not force:
            raise ValueError(f"Run already exists: {run_id}. Use --force to replace.")
        if existing is not None and force:
            db.query(Source).filter(Source.run_id == run_id).delete(synchronize_session=False)
            db.query(DiscoveryRunQuery).filter(DiscoveryRunQuery.run_id == run_id).delete(synchronize_session=False)
            db.query(Run).filter(Run.id == run_id).delete(synchronize_session=False)
            db.commit()

        run = Run(
            id=run_id,
            status="completed",
            seed_queries=[str(q) for q in seed_queries],
            max_iterations=1,
            current_iteration=1,
            accepted_total=0,
            expanded_candidates_total=len(sources),
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning="restored_from_artifact",
            new_accept_rate=None,
            error_message=None,
        )
        db.add(run)
        db.add(
            DiscoveryRunQuery(
                id=f"run_query_restore_{run_id[-10:]}",
                run_id=run_id,
                query_text="artifact restore",
                position=1,
                status="completed",
                discovered_count=len(sources),
                openalex_count=0,
                brave_count=0,
                semantic_scholar_count=0,
                accepted_count=0,
                rejected_count=0,
                pending_count=0,
                processing_count=0,
                scope_total_parents=0,
                scope_processed_parents=0,
                checkpoint_state="none",
                error_message=None,
            )
        )

        accepted_total = 0
        for idx, item in enumerate(sources, start=1):
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id") or f"{run_id}:restored:{idx}")
            accepted = bool(item.get("accepted", False))
            review_status = str(item.get("review_status") or ("human_accept" if accepted else "needs_review"))
            final_decision = str(item.get("final_decision") or review_status)
            decision_source = str(item.get("decision_source") or "human_review")
            heuristic_recommendation = str(item.get("heuristic_recommendation") or ("auto_accept" if accepted else "needs_review"))
            heuristic_score = _num(item.get("heuristic_score"), 0.0)
            relevance_score = _num(item.get("relevance_score"), 0.0)
            if accepted:
                accepted_total += 1

            db.add(
                Source(
                    id=source_id,
                    run_id=run_id,
                    title=str(item.get("title") or source_id),
                    year=_int_or_none(item.get("year")),
                    url=str(item.get("url") or "") or None,
                    doi=str(item.get("doi") or "") or None,
                    abstract=str(item.get("abstract") or "") or None,
                    journal=str(item.get("journal") or "") or None,
                    authors=[str(a) for a in (item.get("authors") or [])],
                    citation_count=_int_or_none(item.get("citation_count")),
                    type=str(item.get("type") or "academic"),
                    source=str(item.get("source") or "restore"),
                    source_native_id=None,
                    patent_office=None,
                    patent_number=None,
                    iteration=max(1, _int_or_none(item.get("iteration")) or 1),
                    discovery_method=str(item.get("discovery_method") or "seed_search"),
                    relevance_score=Decimal(str(round(relevance_score, 2))),
                    accepted=accepted,
                    review_status=review_status,
                    final_decision=final_decision,
                    decision_source=decision_source,
                    heuristic_recommendation=heuristic_recommendation,
                    heuristic_score=Decimal(str(round(heuristic_score, 2))),
                    ai_decision=None,
                    ai_confidence=None,
                    parent_source_id=str(item.get("parent_source") or "") or None,
                    provenance_history=item.get("provenance_history") if isinstance(item.get("provenance_history"), list) else [],
                )
            )

        run.accepted_total = accepted_total
        query = db.get(DiscoveryRunQuery, f"run_query_restore_{run_id[-10:]}")
        if query is not None:
            query.accepted_count = accepted_total
            query.rejected_count = max(0, len(sources) - accepted_total)
        db.commit()
    return run_id, len(sources)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a discovery run from artifacts/*/sources_raw.json")
    parser.add_argument("artifact", type=Path, help="Path to sources_raw.json")
    parser.add_argument("--force", action="store_true", help="Replace existing run id if present")
    args = parser.parse_args()

    run_id, count = restore(args.artifact, force=args.force)
    print(f"restored run_id={run_id} sources={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
