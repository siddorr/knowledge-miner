#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from knowledge_miner.acquisition import (
    build_manual_downloads_payload,
    create_acquisition_run,
    execute_acquisition_run,
)
from knowledge_miner.ai_filter import AIRelevanceFilter
from knowledge_miner.connectors import BraveConnector, MockConnector, OpenAlexConnector, SemanticScholarConnector
from knowledge_miner.db import SessionLocal
from knowledge_miner.discovery import (
    _candidate_target_id,
    _count_accepted,
    _expand_citations_for_iteration,
    _ingest_candidates,
    _persist_citation_edges,
    create_run,
    export_sources_raw,
)
from knowledge_miner.models import AcquisitionItem, Artifact, CitationEdge, Source
from knowledge_miner.observability import RunObservability


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the manual live logic test workflow.")
    parser.add_argument("--query", default="ultrapure water semiconductor")
    parser.add_argument("--per-provider", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--citations-per-direction", type=int, default=5)
    parser.add_argument("--output-dir", default="artifacts/manual_logic_test")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--allow-fallback", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-ai", action="store_true")
    parser.add_argument("--include-semantic-scholar", action=argparse.BooleanOptionalAction, default=None)
    return parser.parse_args()


@dataclass
class ProviderSpec:
    name: str
    source_type: str
    live_connector: Any
    fallback_connector: MockConnector
    enabled: bool
    reason: str | None = None


@dataclass
class ProviderResult:
    name: str
    enabled: bool
    live_count: int
    fallback_count: int
    used_fallback: bool
    warning: str | None


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _provider_specs(include_semantic_scholar: bool | None) -> list[ProviderSpec]:
    from knowledge_miner.config import settings

    include_s2 = settings.use_semantic_scholar if include_semantic_scholar is None else include_semantic_scholar
    return [
        ProviderSpec(
            name="openalex",
            source_type="academic",
            live_connector=OpenAlexConnector(),
            fallback_connector=MockConnector("openalex", "academic"),
            enabled=True,
        ),
        ProviderSpec(
            name="semantic_scholar",
            source_type="academic",
            live_connector=SemanticScholarConnector(),
            fallback_connector=MockConnector("semantic_scholar", "academic"),
            enabled=include_s2,
            reason=None if include_s2 else "disabled",
        ),
        ProviderSpec(
            name="brave",
            source_type="web",
            live_connector=BraveConnector(),
            fallback_connector=MockConnector("brave", "web"),
            enabled=True,
        ),
    ]


def _annotate_candidates(rows: list[dict], provider_mode: str) -> list[dict]:
    annotated: list[dict] = []
    for row in rows:
        item = dict(row)
        item["provider_mode"] = provider_mode
        annotated.append(item)
    return annotated


def _candidate_registry_key(candidate: dict) -> tuple[str, str | None]:
    return str(candidate.get("source") or ""), candidate.get("source_native_id")


def _search_provider(
    spec: ProviderSpec,
    *,
    query: str,
    run_id: str,
    iteration: int,
    per_provider: int,
    allow_fallback: bool,
    warnings: list[str],
    provenance_modes: dict[tuple[str, str | None], set[str]],
) -> tuple[list[dict], ProviderResult]:
    if not spec.enabled:
        return [], ProviderResult(spec.name, False, 0, 0, False, spec.reason)

    live_rows: list[dict]
    warning: str | None = None
    try:
        live_rows = spec.live_connector.search(query, run_id=run_id, iteration=iteration)
    except Exception as exc:  # pragma: no cover - defensive for real live runs
        live_rows = []
        warning = f"live search failed: {exc}"

    rows = _annotate_candidates(live_rows[:per_provider], "live")
    for row in rows:
        provenance_modes.setdefault(_candidate_registry_key(row), set()).add("live")

    fallback_count = 0
    if len(rows) < per_provider and allow_fallback:
        needed = per_provider - len(rows)
        fallback_rows = _annotate_candidates(
            spec.fallback_connector.search(query, run_id=run_id, iteration=iteration)[:needed],
            "fallback",
        )
        fallback_count = len(fallback_rows)
        rows.extend(fallback_rows)
        for row in fallback_rows:
            provenance_modes.setdefault(_candidate_registry_key(row), set()).add("fallback")
        if fallback_count:
            shortage = f"{spec.name}: supplemented {fallback_count} fallback rows because live returned {len(live_rows)} usable rows"
            warnings.append(shortage)
            warning = shortage if warning is None else f"{warning}; {shortage}"

    return rows, ProviderResult(
        name=spec.name,
        enabled=True,
        live_count=min(len(live_rows), per_provider),
        fallback_count=fallback_count,
        used_fallback=fallback_count > 0,
        warning=warning,
    )


def _rank_source(source: Source) -> tuple:
    ai_first = 1 if source.decision_source == "ai" else 0
    ai_conf = float(source.ai_confidence) if source.ai_confidence is not None else -1.0
    rel = float(source.relevance_score)
    heuristic = float(source.heuristic_score)
    accepted = 1 if source.accepted else 0
    return (-accepted, -ai_first, -ai_conf, -rel, -heuristic, source.id)


def _select_top_academic_sources(db, run_id: str, top_k: int) -> list[Source]:
    rows = db.scalars(
        select(Source).where(Source.run_id == run_id, Source.type == "academic").order_by(Source.id.asc())
    ).all()
    accepted = sorted([row for row in rows if row.accepted], key=_rank_source)
    if len(accepted) >= top_k:
        return accepted[:top_k]
    review = sorted([row for row in rows if row.review_status == "needs_review" and row.id not in {s.id for s in accepted}], key=_rank_source)
    return (accepted + review)[:top_k]


def _build_live_connector_map(specs: list[ProviderSpec]) -> dict[str, Any]:
    return {spec.name: spec.live_connector for spec in specs if spec.enabled}


def _build_fallback_connector_map(specs: list[ProviderSpec]) -> dict[str, MockConnector]:
    return {spec.name: spec.fallback_connector for spec in specs if spec.enabled}


def _expand_for_selected_sources(
    selected_sources: list[Source],
    *,
    iteration: int,
    per_direction_limit: int,
    live_connectors: dict[str, Any],
    fallback_connectors: dict[str, MockConnector],
    provenance_modes: dict[tuple[str, str | None], set[str]],
    warnings: list[str],
) -> tuple[list[dict], list[tuple[str, str, str]], dict[str, dict[str, int]]]:
    all_candidates: list[dict] = []
    all_edges: list[tuple[str, str, str]] = []
    stats: dict[str, dict[str, int]] = {}
    for source in selected_sources:
        connector = live_connectors.get(source.source)
        backward: list[dict] = []
        forward: list[dict] = []
        mode = "live"
        if connector is not None:
            try:
                backward, forward = connector.expand_citations(source, per_direction_limit=per_direction_limit, iteration=iteration)
            except Exception as exc:  # pragma: no cover - defensive for real live runs
                warnings.append(f"{source.id}: live citation expansion failed: {exc}")
        if not backward and not forward:
            fallback = fallback_connectors.get(source.source)
            if fallback is not None:
                mode = "fallback"
                backward, forward = fallback.expand_citations(source, per_direction_limit=per_direction_limit, iteration=iteration)

        backward = _annotate_candidates(backward[:per_direction_limit], mode)
        forward = _annotate_candidates(forward[:per_direction_limit], mode)
        stats[source.id] = {"backward": len(backward), "forward": len(forward)}

        for candidate in backward + forward:
            provenance_modes.setdefault(_candidate_registry_key(candidate), set()).add(mode)
        all_candidates.extend(backward)
        all_candidates.extend(forward)
        all_edges.extend((source.id, _candidate_target_id(candidate), "cites") for candidate in backward)
        all_edges.extend((source.id, _candidate_target_id(candidate), "cited_by") for candidate in forward)
    return all_candidates, all_edges, stats


def _source_provider_mode(source: Source, provenance_modes: dict[tuple[str, str | None], set[str]]) -> str:
    modes: set[str] = set()
    for event in source.provenance_history or []:
        modes.update(provenance_modes.get((event.get("provider"), event.get("source_native_id")), set()))
    if not modes:
        return "unknown"
    if modes == {"live"}:
        return "live"
    if modes == {"fallback"}:
        return "fallback"
    return "mixed"


def _write_final_sources_csv(
    db,
    *,
    run_id: str,
    acq_run_id: str | None,
    output_path: Path,
    provenance_modes: dict[tuple[str, str | None], set[str]],
) -> None:
    items_by_source: dict[str, AcquisitionItem] = {}
    artifacts_by_source: dict[str, Artifact] = {}
    if acq_run_id:
        items = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == acq_run_id)).all()
        artifacts = db.scalars(select(Artifact).where(Artifact.acq_run_id == acq_run_id)).all()
        items_by_source = {item.source_id: item for item in items}
        artifacts_by_source = {artifact.source_id: artifact for artifact in artifacts}

    rows = db.scalars(
        select(Source).where(Source.run_id == run_id).order_by(Source.accepted.desc(), Source.relevance_score.desc(), Source.id.asc())
    ).all()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "run_id",
                "source_id",
                "title",
                "year",
                "provider",
                "provider_mode",
                "type",
                "doi",
                "url",
                "discovery_method",
                "parent_source_id",
                "iteration",
                "relevance_score",
                "heuristic_score",
                "final_decision",
                "decision_source",
                "accepted",
                "review_status",
                "ai_confidence",
                "pdf_status",
                "selected_pdf_url",
                "download_error",
                "provenance_count",
            ]
        )
        for source in rows:
            item = items_by_source.get(source.id)
            artifact = artifacts_by_source.get(source.id)
            pdf_status = artifact.kind if artifact else (item.status if item else "")
            selected_url = item.selected_url if item else ""
            error = item.last_error if item else ""
            writer.writerow(
                [
                    run_id,
                    source.id,
                    source.title,
                    source.year or "",
                    source.source,
                    _source_provider_mode(source, provenance_modes),
                    source.type,
                    source.doi or "",
                    source.url or "",
                    source.discovery_method,
                    source.parent_source_id or "",
                    source.iteration,
                    float(source.relevance_score),
                    float(source.heuristic_score),
                    source.final_decision,
                    source.decision_source,
                    int(bool(source.accepted)),
                    source.review_status,
                    "" if source.ai_confidence is None else float(source.ai_confidence),
                    pdf_status,
                    selected_url or "",
                    error or "",
                    len(source.provenance_history or []),
                ]
            )


def _write_manual_downloads_csv(acq_run_id: str, payload: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "item_id",
                "source_id",
                "status",
                "attempt_count",
                "last_error",
                "title",
                "doi",
                "source_url",
                "selected_url",
                "manual_url_candidates",
                "reason_code",
                "legal_candidates",
            ]
        )
        for item in payload["items"]:
            writer.writerow(
                [
                    item["item_id"],
                    item["source_id"],
                    item["status"],
                    item["attempt_count"],
                    item["last_error"] or "",
                    item["title"],
                    item["doi"] or "",
                    item["source_url"] or "",
                    item["selected_url"] or "",
                    " | ".join(item["manual_url_candidates"]),
                    item.get("reason_code") or "",
                    " | ".join(
                        f"{candidate.get('candidate_rank')}:{candidate.get('candidate_source')}:{candidate.get('candidate_url')}"
                        for candidate in item.get("legal_candidates", [])
                    ),
                ]
            )


def _summary_provider_counts(results: list[ProviderResult]) -> dict[str, dict[str, Any]]:
    return {
        result.name: {
            "enabled": result.enabled,
            "live_count": result.live_count,
            "fallback_count": result.fallback_count,
            "used_fallback": result.used_fallback,
            "warning": result.warning,
        }
        for result in results
    }


def _validate_ai(require_ai: bool) -> tuple[AIRelevanceFilter | None, bool, str | None]:
    from knowledge_miner.config import settings

    ai_available = bool(settings.ai_api_key)
    if require_ai and not ai_available:
        raise SystemExit("AI is required for this test but AI_API_KEY/OPENAI_API_KEY is not configured.")
    ai_filter = None
    warning = None
    if ai_available:
        ai_filter = AIRelevanceFilter(
            enabled=True,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            base_url=settings.ai_base_url,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    else:
        warning = "AI unavailable; heuristic fallback will be used."
    return ai_filter, ai_available, warning


def main() -> int:
    args = parse_args()
    stamp = _now_stamp()
    output_dir = Path(args.output_dir) / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    ai_filter, ai_available, ai_warning = _validate_ai(args.require_ai)
    if ai_warning:
        warnings.append(ai_warning)

    specs = _provider_specs(args.include_semantic_scholar)
    live_connectors = _build_live_connector_map(specs)
    fallback_connectors = _build_fallback_connector_map(specs)
    provenance_modes: dict[tuple[str, str | None], set[str]] = {}
    provider_results: list[ProviderResult] = []

    with SessionLocal() as db:
        run = create_run(db, [args.query], max_iterations=2, ai_filter_enabled=ai_available)
        run.status = "running"
        db.commit()
        db.refresh(run)

        iteration_one_candidates: list[dict] = []
        for spec in specs:
            provider_rows, provider_result = _search_provider(
                spec,
                query=args.query,
                run_id=run.id,
                iteration=1,
                per_provider=args.per_provider,
                allow_fallback=args.allow_fallback,
                warnings=warnings,
                provenance_modes=provenance_modes,
            )
            provider_results.append(provider_result)
            iteration_one_candidates.extend(provider_rows)

        observability = RunObservability()
        _ingest_candidates(
            db,
            run.id,
            1,
            iteration_one_candidates,
            ai_filter=ai_filter,
            ai_policy_no_ai=not ai_available,
            observability=observability,
        )
        run.current_iteration = 1
        run.accepted_total = _count_accepted(db, run.id)
        run.updated_at = datetime.now(UTC)
        db.commit()

        selected_sources = _select_top_academic_sources(db, run.id, args.top_k)
        if len(selected_sources) < args.top_k:
            run.status = "failed"
            run.error_message = f"Only {len(selected_sources)} academic documents available for citation expansion."
            db.commit()
            raise SystemExit(run.error_message)

        citation_candidates, citation_edges, citation_stats = _expand_for_selected_sources(
            selected_sources,
            iteration=2,
            per_direction_limit=args.citations_per_direction,
            live_connectors=live_connectors,
            fallback_connectors=fallback_connectors,
            provenance_modes=provenance_modes,
            warnings=warnings,
        )

        if citation_candidates:
            _ingest_candidates(
                db,
                run.id,
                2,
                citation_candidates,
                ai_filter=ai_filter,
                ai_policy_no_ai=not ai_available,
                observability=observability,
            )
            run.expanded_candidates_total = len(citation_candidates)
        if citation_edges:
            run.citation_edges_total = _persist_citation_edges(db, run.id, 2, citation_edges)
        run.current_iteration = 2
        run.accepted_total = _count_accepted(db, run.id)
        run.status = "completed"
        run.updated_at = datetime.now(UTC)
        db.commit()

        acq_run = create_acquisition_run(db, run.id, retry_failed_only=False)
        execute_acquisition_run(db, acq_run)
        db.refresh(acq_run)

        final_sources_csv = output_dir / "final_sources.csv"
        _write_final_sources_csv(
            db,
            run_id=run.id,
            acq_run_id=acq_run.id,
            output_path=final_sources_csv,
            provenance_modes=provenance_modes,
        )

        manual_csv_path: Path | None = None
        manual_payload: dict | None = None
        if acq_run.partial_total or acq_run.failed_total:
            manual_payload = build_manual_downloads_payload(db, acq_run.id, limit=100_000, offset=0)
            manual_csv_path = output_dir / f"manual_downloads_{acq_run.id}.csv"
            _write_manual_downloads_csv(acq_run.id, manual_payload, manual_csv_path)

        sources_raw_path = export_sources_raw(db, run.id)
        total_sources = db.query(Source).filter(Source.run_id == run.id).count()
        citation_edge_count = db.query(CitationEdge).filter(CitationEdge.run_id == run.id).count()
        artifacts = db.query(Artifact).filter(Artifact.acq_run_id == acq_run.id).count()

        summary = {
            "query": args.query,
            "run_id": run.id,
            "acq_run_id": acq_run.id,
            "providers": _summary_provider_counts(provider_results),
            "ai_available": ai_available,
            "warnings": warnings,
            "selected_top_ids": [source.id for source in selected_sources],
            "citation_stats": citation_stats,
            "total_sources": total_sources,
            "accepted_total": run.accepted_total,
            "expanded_candidates_total": run.expanded_candidates_total,
            "citation_edges_total": citation_edge_count,
            "acquisition": {
                "status": acq_run.status,
                "downloaded_total": acq_run.downloaded_total,
                "partial_total": acq_run.partial_total,
                "failed_total": acq_run.failed_total,
                "skipped_total": acq_run.skipped_total,
                "artifact_count": artifacts,
            },
            "artifacts": {
                "final_sources_csv": str(final_sources_csv),
                "manual_downloads_csv": str(manual_csv_path) if manual_csv_path else None,
                "sources_raw_json": str(sources_raw_path),
            },
            "pass": True,
        }

        summary_path = output_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Run ID: {summary['run_id']}")
    print(f"Acquisition Run ID: {summary['acq_run_id']}")
    for name, payload in summary["providers"].items():
        print(
            f"Provider {name}: enabled={payload['enabled']} live={payload['live_count']} "
            f"fallback={payload['fallback_count']}"
        )
    print(f"Selected top academic sources: {', '.join(summary['selected_top_ids'])}")
    print(
        "Acquisition totals: "
        f"downloaded={summary['acquisition']['downloaded_total']} "
        f"partial={summary['acquisition']['partial_total']} "
        f"failed={summary['acquisition']['failed_total']} "
        f"skipped={summary['acquisition']['skipped_total']}"
    )
    print(f"Final sources CSV: {summary['artifacts']['final_sources_csv']}")
    if summary["artifacts"]["manual_downloads_csv"]:
        print(f"Manual downloads CSV: {summary['artifacts']['manual_downloads_csv']}")
    print(f"Summary JSON: {summary_path}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
