from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
import time
from pathlib import Path
import threading
from typing import Iterable
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .ai_filter import AIRelevanceFilter, describe_ai_filter_runtime
from .config import settings
from .connectors import Connector, RetryableProviderError, build_connectors
from .db import SessionLocal
from .dedup import canonical_id, canonicalize_url, is_fuzzy_duplicate
from .iteration import build_next_queries, extract_keywords
from .models import CitationEdge, Keyword, Run, Source
from .observability import RunObservability
from .retry import retry_call
from .scoring import decision_from_score, score_text


def create_run(db: Session, seed_queries: list[str], max_iterations: int) -> Run:
    ai_filter_active, ai_filter_warning = describe_ai_filter_runtime(
        use_ai_filter=settings.use_ai_filter,
        api_key=settings.ai_api_key,
    )
    run = Run(
        id=f"run_{uuid.uuid4().hex[:12]}",
        status="queued",
        seed_queries=seed_queries,
        max_iterations=max_iterations,
        current_iteration=0,
        accepted_total=0,
        expanded_candidates_total=0,
        citation_edges_total=0,
        ai_filter_active=ai_filter_active,
        ai_filter_warning=ai_filter_warning,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def enqueue_run(run_id: str) -> None:
    worker = threading.Thread(target=execute_run_by_id, args=(run_id,), daemon=True)
    worker.start()


def execute_run_by_id(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        execute_run(db, run)


def execute_run(db: Session, run: Run, connectors: list[Connector] | None = None) -> None:
    observability = RunObservability()
    try:
        run.status = "running"
        db.commit()
        if run.ai_filter_warning:
            observability.record_provider_call(
                run_id=run.id,
                iteration=0,
                provider="ai_filter",
                operation="runtime_warning",
                latency_ms=0.0,
                ok=False,
                error=run.ai_filter_warning,
            )

        queries = list(run.seed_queries)
        low_yield_streak = 0
        ai_filter = AIRelevanceFilter()

        active_connectors = connectors or build_connectors()
        connectors_by_name = {c.name: c for c in active_connectors}
        for iteration in range(1, run.max_iterations + 1):
            candidates = _collect_candidates(run.id, queries, iteration, active_connectors, observability=observability)
            new_accepted_unique = _ingest_candidates(
                db,
                run.id,
                iteration,
                candidates,
                ai_filter=ai_filter,
                observability=observability,
            )
            citation_candidates, citation_edges = _expand_citations_for_iteration(
                db,
                run.id,
                iteration,
                connectors_by_name,
                per_direction_limit=settings.citation_expansion_limit_per_direction,
                parent_cap=settings.citation_expansion_parent_cap_per_iteration,
                observability=observability,
            )
            if citation_candidates:
                new_accepted_unique += _ingest_candidates(
                    db,
                    run.id,
                    iteration,
                    citation_candidates,
                    ai_filter=ai_filter,
                    observability=observability,
                )
                run.expanded_candidates_total += len(citation_candidates)
            if citation_edges:
                persisted_edges = _persist_citation_edges(db, run.id, iteration, citation_edges)
                run.citation_edges_total += persisted_edges
            accepted_total = _count_accepted(db, run.id)

            _store_keywords_for_iteration(db, run.id, iteration)
            run.current_iteration = iteration
            run.accepted_total = accepted_total
            run.new_accept_rate = (new_accepted_unique / accepted_total) if accepted_total else 0.0
            db.commit()

            if accepted_total > 0 and run.new_accept_rate < 0.05:
                low_yield_streak += 1
            else:
                low_yield_streak = 0

            if low_yield_streak >= 2:
                break

            queries = _next_iteration_queries(db, run.id, iteration)

        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()
        observability.emit_run_summary(run_id=run.id, status=run.status, current_iteration=run.current_iteration)
    except Exception as exc:  # pragma: no cover - defensive failure path
        db.rollback()
        run.status = "failed"
        run.error_message = str(exc)
        run.updated_at = datetime.now(UTC)
        db.commit()
        observability.inc("api_errors")
        observability.emit_run_summary(run_id=run.id, status=run.status, current_iteration=run.current_iteration)
        raise


def review_source(db: Session, source: Source, decision: str) -> Source:
    normalized = decision.strip().lower()
    if normalized not in {"accept", "reject"}:
        raise ValueError("decision must be accept or reject")

    if normalized == "accept":
        source.accepted = True
        source.review_status = "human_accept"
    else:
        source.accepted = False
        source.review_status = "human_reject"
    source.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(source)
    return source


def export_sources_raw(db: Session, run_id: str) -> Path:
    sources = db.scalars(
        select(Source)
        .where(Source.run_id == run_id, Source.accepted.is_(True))
        .order_by(Source.accepted.desc(), Source.relevance_score.desc(), Source.year.desc().nullslast(), Source.id.asc())
    ).all()

    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": [
            {
                "id": s.id,
                "title": s.title,
                "year": s.year,
                "url": s.url,
                "doi": s.doi,
                "abstract": s.abstract,
                "type": s.type,
                "source": s.source,
                "iteration": s.iteration,
                "discovery_method": s.discovery_method,
                "relevance_score": float(s.relevance_score),
                "accepted": s.accepted,
                "review_status": s.review_status,
                "parent_source": s.parent_source_id,
                "provenance_history": s.provenance_history,
            }
            for s in sources
        ],
        "provenance": {
            "seed_queries": list(_load_run_seed_queries(db, run_id)),
            "apis_used": ["openalex", "semantic_scholar", "brave"],
        },
    }

    out_dir = Path(settings.artifacts_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sources_raw.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def _load_run_seed_queries(db: Session, run_id: str) -> list[str]:
    run = db.get(Run, run_id)
    if run is None:
        return []
    return list(run.seed_queries)


def _collect_candidates(
    run_id: str,
    queries: list[str],
    iteration: int,
    connectors: list[Connector],
    *,
    observability: RunObservability,
) -> list[dict]:
    candidates: list[dict] = []
    for query in queries[:10]:
        for connector in connectors:
            started = time.perf_counter()
            try:
                rows = retry_call(
                    lambda: connector.search(query, run_id=run_id, iteration=iteration),
                    attempts=3,
                    delays=(1.0, 2.0, 4.0),
                    should_retry=lambda exc: isinstance(exc, RetryableProviderError),
                )
                observability.record_provider_call(
                    run_id=run_id,
                    iteration=iteration,
                    provider=connector.name,
                    operation="search",
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    ok=True,
                )
            except Exception as exc:
                observability.inc("api_errors")
                observability.record_provider_call(
                    run_id=run_id,
                    iteration=iteration,
                    provider=connector.name,
                    operation="search",
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    ok=False,
                    error=str(exc),
                )
                continue
            observability.inc("fetched", len(rows))
            candidates.extend(rows)
    return candidates


def _expand_citations_for_iteration(
    db: Session,
    run_id: str,
    iteration: int,
    connectors_by_name: dict[str, Connector],
    *,
    per_direction_limit: int,
    parent_cap: int,
    observability: RunObservability,
) -> tuple[list[dict], list[tuple[str, str, str]]]:
    parents = db.scalars(
        select(Source)
        .where(Source.run_id == run_id, Source.iteration == iteration, Source.accepted.is_(True))
        .order_by(Source.relevance_score.desc(), Source.id.asc())
        .limit(max(0, parent_cap))
    ).all()

    expanded_candidates: list[dict] = []
    edge_triples: list[tuple[str, str, str]] = []
    for parent in parents:
        connector = connectors_by_name.get(parent.source)
        if connector is None:
            continue
        internal_limit = max(per_direction_limit * 3, per_direction_limit)
        started = time.perf_counter()
        try:
            backward, forward = retry_call(
                lambda: connector.expand_citations(parent, per_direction_limit=internal_limit, iteration=iteration),
                attempts=3,
                delays=(1.0, 2.0, 4.0),
                should_retry=lambda exc: isinstance(exc, RetryableProviderError),
            )
            observability.record_provider_call(
                run_id=run_id,
                iteration=iteration,
                provider=connector.name,
                operation="expand_citations",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                ok=True,
            )
        except Exception as exc:
            observability.inc("api_errors")
            observability.record_provider_call(
                run_id=run_id,
                iteration=iteration,
                provider=connector.name,
                operation="expand_citations",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                ok=False,
                error=str(exc),
            )
            continue

        ranked_backward = _rank_citation_candidates(parent, backward)[: max(0, per_direction_limit)]
        ranked_forward = _rank_citation_candidates(parent, forward)[: max(0, per_direction_limit)]
        observability.inc("fetched", len(ranked_backward) + len(ranked_forward))
        for c in ranked_backward:
            expanded_candidates.append(c)
            edge_triples.append((parent.id, _candidate_target_id(c), "cites"))
        for c in ranked_forward:
            expanded_candidates.append(c)
            edge_triples.append((parent.id, _candidate_target_id(c), "cited_by"))
    return expanded_candidates, edge_triples


def _candidate_target_id(candidate: dict) -> str:
    return canonical_id(
        doi=candidate.get("doi"),
        url=candidate.get("url"),
        title=candidate["title"],
        year=candidate.get("year"),
        openalex_id=(candidate.get("openalex_id") or candidate.get("source_native_id"))
        if candidate.get("source") == "openalex"
        else None,
        semantic_scholar_id=(candidate.get("semantic_scholar_id") or candidate.get("source_native_id"))
        if candidate.get("source") == "semantic_scholar"
        else None,
        patent_office=candidate.get("patent_office"),
        patent_number=candidate.get("patent_number"),
    )


def _rank_citation_candidates(parent: Source, candidates: list[dict]) -> list[dict]:
    parent_tokens = _citation_tokens(parent.title, parent.abstract)

    def sort_key(candidate: dict) -> tuple:
        title = str(candidate.get("title") or "")
        abstract = candidate.get("abstract")
        year = candidate.get("year") if isinstance(candidate.get("year"), int) else 0
        overlap = len(parent_tokens & _citation_tokens(title, abstract))
        has_abstract = int(bool(abstract))
        has_doi = int(bool(candidate.get("doi")))
        # Deterministic tie-break with canonical id and lowercased title.
        tie_id = _candidate_target_id(candidate)
        return (-has_abstract, -has_doi, -year, -overlap, title.lower(), tie_id)

    return sorted(candidates, key=sort_key)


def _citation_tokens(title: str | None, abstract: str | None) -> set[str]:
    text = f"{title or ''} {abstract or ''}".lower()
    tokens = {"".join(ch for ch in token if ch.isalnum()) for token in text.split()}
    return {t for t in tokens if len(t) >= 3}


def _persist_citation_edges(db: Session, run_id: str, iteration: int, edges: list[tuple[str, str, str]]) -> int:
    unique_edges = set(edges)
    for source_id, target_id, relationship_type in unique_edges:
        db.merge(
            CitationEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship_type,
                run_id=run_id,
                iteration=iteration,
            )
        )
    db.commit()
    return len(unique_edges)


def _ingest_candidates(
    db: Session,
    run_id: str,
    iteration: int,
    candidates: Iterable[dict],
    *,
    ai_filter: AIRelevanceFilter | None = None,
    observability: RunObservability | None = None,
) -> int:
    new_accepted_unique = 0
    pending_source_ids: set[str] = set()
    for c in candidates:
        canonical_sid = canonical_id(
            doi=c.get("doi"),
            url=c.get("url"),
            title=c["title"],
            year=c.get("year"),
            openalex_id=(c.get("openalex_id") or c.get("source_native_id")) if c.get("source") == "openalex" else None,
            semantic_scholar_id=(c.get("semantic_scholar_id") or c.get("source_native_id"))
            if c.get("source") == "semantic_scholar"
            else None,
            patent_office=c.get("patent_office"),
            patent_number=c.get("patent_number"),
        )
        sid = _run_scoped_source_id(db, run_id, canonical_sid)

        if sid in pending_source_ids:
            if observability is not None:
                observability.inc("dedup")
            continue

        existing = _find_existing_source(db, run_id, c, canonical_sid)
        if existing is not None:
            _merge_source(existing, c, iteration=iteration)
            db.add(existing)
            if observability is not None:
                observability.inc("dedup")
            continue

        score = score_text(c["title"], c.get("abstract"))
        accepted, review_status = decision_from_score(score)
        ai_decision = None
        ai_confidence = None
        if ai_filter is not None:
            ai_result = ai_filter.evaluate(
                title=c["title"],
                abstract=c.get("abstract"),
                base_score=score,
                base_decision=review_status,
            )
            if ai_result is not None:
                if ai_result.confidence >= settings.ai_min_confidence_override:
                    review_status = ai_result.decision
                    accepted = review_status == "auto_accept"
                    ai_decision = ai_result.decision
                    ai_confidence = ai_result.confidence
        if accepted:
            new_accepted_unique += 1
            if observability is not None:
                observability.inc("accepted")
        elif observability is not None:
            observability.inc("rejected")

        source = Source(
            id=sid,
            run_id=run_id,
            title=c["title"],
            year=c.get("year"),
            url=c.get("url"),
            doi=c.get("doi"),
            abstract=c.get("abstract"),
            type=c["type"],
            source=c["source"],
            source_native_id=c.get("source_native_id"),
            patent_office=c.get("patent_office"),
            patent_number=c.get("patent_number"),
            iteration=iteration,
            discovery_method=c["discovery_method"],
            relevance_score=score,
            accepted=accepted,
            review_status=review_status,
            ai_decision=ai_decision,
            ai_confidence=ai_confidence,
            parent_source_id=c.get("parent_source_id"),
            provenance_history=[_provenance_event(c, iteration)],
        )
        db.add(source)
        pending_source_ids.add(sid)
    db.commit()
    return new_accepted_unique


def _run_scoped_source_id(db: Session, run_id: str, canonical_sid: str) -> str:
    existing = db.get(Source, canonical_sid)
    if existing is None or existing.run_id == run_id:
        return canonical_sid
    # Keep canonical ID when possible; add run scope only for cross-run PK conflicts.
    return f"{canonical_sid}::run:{run_id}"


def _find_existing_source(db: Session, run_id: str, candidate: dict, candidate_id: str) -> Source | None:
    direct = db.get(Source, candidate_id)
    if direct is not None and direct.run_id == run_id:
        return direct

    doi = candidate.get("doi")
    url = candidate.get("url")
    native_id = candidate.get("source_native_id")

    if doi:
        row = db.scalars(select(Source).where(Source.run_id == run_id, Source.doi == doi)).first()
        if row is not None:
            return row

    if url:
        normalized_url = canonicalize_url(url)
        row = db.scalars(
            select(Source).where(
                Source.run_id == run_id,
                Source.url.is_not(None),
            )
        ).all()
        for existing in row:
            if existing.url and canonicalize_url(existing.url) == normalized_url:
                return existing

    if native_id:
        row = db.scalars(
            select(Source).where(
                Source.run_id == run_id,
                Source.source == candidate.get("source"),
                Source.source_native_id == native_id,
            )
        ).first()
        if row is not None:
            return row

    if candidate.get("year") is None:
        fuzzy_stmt = select(Source).where(Source.run_id == run_id)
    else:
        year = int(candidate["year"])
        fuzzy_stmt = select(Source).where(
            and_(Source.run_id == run_id, or_(Source.year.is_(None), Source.year.between(year - 1, year + 1)))
        )
    fuzzy_rows = db.scalars(fuzzy_stmt).all()
    for existing in fuzzy_rows:
        if is_fuzzy_duplicate(
            title_a=existing.title,
            year_a=existing.year,
            title_b=candidate["title"],
            year_b=candidate.get("year"),
            threshold=0.92,
        ):
            return existing
    return None


def _merge_source(target: Source, incoming: dict, *, iteration: int) -> None:
    # Keep the more complete record when dedup identifies the same source.
    if not target.abstract and incoming.get("abstract"):
        target.abstract = incoming["abstract"]
    if not target.doi and incoming.get("doi"):
        target.doi = incoming["doi"]
    if not target.url and incoming.get("url"):
        target.url = incoming["url"]
    if target.year is None and incoming.get("year") is not None:
        target.year = incoming["year"]
    if not target.source_native_id and incoming.get("source_native_id"):
        target.source_native_id = incoming["source_native_id"]
    if not target.patent_office and incoming.get("patent_office"):
        target.patent_office = incoming["patent_office"]
    if not target.patent_number and incoming.get("patent_number"):
        target.patent_number = incoming["patent_number"]
    history = list(target.provenance_history or [])
    history.append(_provenance_event(incoming, iteration))
    target.provenance_history = history
    target.updated_at = datetime.now(UTC)


def _provenance_event(candidate: dict, iteration: int) -> dict:
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "iteration": iteration,
        "discovery_method": candidate.get("discovery_method"),
        "parent_source_id": candidate.get("parent_source_id"),
        "provider": candidate.get("source"),
        "source_native_id": candidate.get("source_native_id"),
    }


def _count_accepted(db: Session, run_id: str) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Source).where(Source.run_id == run_id, Source.accepted.is_(True)))
        or 0
    )


def _store_keywords_for_iteration(db: Session, run_id: str, iteration: int) -> None:
    accepted_texts = db.scalars(
        select(Source.abstract).where(Source.run_id == run_id, Source.accepted.is_(True), Source.abstract.is_not(None))
    ).all()
    keywords = extract_keywords(list(accepted_texts), top_k=20)
    freqs = Counter(keywords)
    for kw, freq in freqs.items():
        db.merge(Keyword(run_id=run_id, iteration=iteration, keyword=kw, frequency=freq))
    db.commit()


def _next_iteration_queries(db: Session, run_id: str, iteration: int) -> list[str]:
    rows = db.scalars(
        select(Keyword.keyword)
        .where(Keyword.run_id == run_id, Keyword.iteration == iteration)
        .order_by(Keyword.frequency.desc(), Keyword.keyword.asc())
        .limit(20)
    ).all()
    queries = build_next_queries(rows, max_queries=10)
    if not queries:
        return ["ultrapure water semiconductor process control"]
    return queries
