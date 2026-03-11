from __future__ import annotations

from collections import Counter, defaultdict
import json
import logging


class RunObservability:
    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._histograms: dict[str, Counter[str]] = defaultdict(Counter)
        self._log = logging.getLogger("knowledge_miner")

    def inc(self, name: str, value: int = 1) -> None:
        self._counters[name] += value

    def record_provider_call(
        self,
        *,
        run_id: str,
        iteration: int,
        provider: str,
        operation: str,
        latency_ms: float,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self._observe_latency(provider=provider, operation=operation, latency_ms=latency_ms)
        payload = {
            "event": "provider_call",
            "run_id": run_id,
            "iteration": iteration,
            "provider": provider,
            "operation": operation,
            "latency_ms": round(latency_ms, 3),
            "ok": ok,
        }
        if error:
            payload["error"] = error
        self._log.info(json.dumps(payload, sort_keys=True))

    def emit_run_summary(self, *, run_id: str, status: str, current_iteration: int) -> None:
        payload = {
            "event": "run_summary",
            "run_id": run_id,
            "status": status,
            "current_iteration": current_iteration,
            "counters": dict(self._counters),
            "latency_histograms": {k: dict(v) for k, v in self._histograms.items()},
        }
        self._log.info(json.dumps(payload, sort_keys=True))

    def snapshot(self) -> dict:
        return {
            "counters": dict(self._counters),
            "latency_histograms": {k: dict(v) for k, v in self._histograms.items()},
        }

    def _observe_latency(self, *, provider: str, operation: str, latency_ms: float) -> None:
        key = f"{provider}:{operation}"
        bucket = _latency_bucket_label(latency_ms)
        self._histograms[key][bucket] += 1


class AcquisitionObservability:
    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._histograms: dict[str, Counter[str]] = defaultdict(Counter)
        self._log = logging.getLogger("knowledge_miner")

    def inc(self, name: str, value: int = 1) -> None:
        self._counters[name] += value

    def record_download(
        self,
        *,
        acq_run_id: str,
        source_id: str,
        domain: str,
        latency_ms: float,
        status: str,
        error: str | None = None,
    ) -> None:
        key = f"{domain}:download"
        self._histograms[key][_latency_bucket_label(latency_ms)] += 1
        payload = {
            "event": "acquisition_download",
            "acq_run_id": acq_run_id,
            "source_id": source_id,
            "domain": domain,
            "latency_ms": round(latency_ms, 3),
            "status": status,
        }
        if error:
            payload["error"] = error
        self._log.info(json.dumps(payload, sort_keys=True))

    def emit_summary(self, *, acq_run_id: str, status: str) -> None:
        payload = {
            "event": "acquisition_summary",
            "acq_run_id": acq_run_id,
            "status": status,
            "counters": dict(self._counters),
            "latency_histograms": {k: dict(v) for k, v in self._histograms.items()},
        }
        self._log.info(json.dumps(payload, sort_keys=True))


class ParseObservability:
    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._histograms: dict[str, Counter[str]] = defaultdict(Counter)
        self._log = logging.getLogger("knowledge_miner")

    def inc(self, name: str, value: int = 1) -> None:
        self._counters[name] += value

    def record_document(
        self,
        *,
        parse_run_id: str,
        document_id: str,
        artifact_id: str,
        latency_ms: float,
        status: str,
        parser_used: str | None = None,
        chunks: int | None = None,
        error: str | None = None,
    ) -> None:
        self._histograms["parse:document"][_latency_bucket_label(latency_ms)] += 1
        payload = {
            "event": "parse_document",
            "parse_run_id": parse_run_id,
            "document_id": document_id,
            "artifact_id": artifact_id,
            "latency_ms": round(latency_ms, 3),
            "status": status,
        }
        if parser_used is not None:
            payload["parser_used"] = parser_used
        if chunks is not None:
            payload["chunks"] = chunks
        if error:
            payload["error"] = error
        self._log.info(json.dumps(payload, sort_keys=True))

    def record_indexing(
        self,
        *,
        parse_run_id: str,
        latency_ms: float,
        status: str,
        indexed_documents: int,
        indexed_chunks: int,
        error: str | None = None,
    ) -> None:
        self._histograms["parse:index"][_latency_bucket_label(latency_ms)] += 1
        payload = {
            "event": "parse_index",
            "parse_run_id": parse_run_id,
            "latency_ms": round(latency_ms, 3),
            "status": status,
            "indexed_documents": indexed_documents,
            "indexed_chunks": indexed_chunks,
        }
        if error:
            payload["error"] = error
        self._log.info(json.dumps(payload, sort_keys=True))

    def emit_summary(self, *, parse_run_id: str, status: str) -> None:
        payload = {
            "event": "parse_summary",
            "parse_run_id": parse_run_id,
            "status": status,
            "counters": dict(self._counters),
            "latency_histograms": {k: dict(v) for k, v in self._histograms.items()},
        }
        self._log.info(json.dumps(payload, sort_keys=True))


def _latency_bucket_label(latency_ms: float) -> str:
    if latency_ms <= 100:
        return "le_100ms"
    if latency_ms <= 300:
        return "le_300ms"
    if latency_ms <= 1000:
        return "le_1000ms"
    if latency_ms <= 3000:
        return "le_3000ms"
    if latency_ms <= 10000:
        return "le_10000ms"
    return "gt_10000ms"
