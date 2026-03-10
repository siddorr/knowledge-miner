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
