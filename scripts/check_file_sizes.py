#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "file_size_guardrails.json"
SCAN_DIRS = ("src", "tests")


@dataclass
class Finding:
    level: str
    path: str
    lines: int
    limit: int
    reason: str = ""


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        return sum(1 for _ in fh)


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "js_warn": int(raw.get("js_warn", 800)),
        "js_fail": int(raw.get("js_fail", 1200)),
        "py_warn": int(raw.get("py_warn", 700)),
        "py_fail": int(raw.get("py_fail", 1000)),
        "exceptions": {str(k): str(v) for k, v in (raw.get("exceptions") or {}).items()},
    }


def evaluate(config: dict | None = None) -> tuple[list[Finding], list[Finding]]:
    cfg = config or load_config()
    warnings: list[Finding] = []
    failures: list[Finding] = []
    exceptions: dict[str, str] = cfg["exceptions"]

    for scan_dir in SCAN_DIRS:
        for path in (ROOT / scan_dir).rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            suffix = path.suffix.lower()
            if suffix not in (".py", ".js", ".ts"):
                continue
            lines = _line_count(path)
            if suffix in (".js", ".ts"):
                warn_limit = cfg["js_warn"]
                fail_limit = cfg["js_fail"]
            else:
                warn_limit = cfg["py_warn"]
                fail_limit = cfg["py_fail"]

            if lines > warn_limit:
                warnings.append(Finding("WARN", rel, lines, warn_limit))
            if lines > fail_limit:
                reason = exceptions.get(rel, "")
                if reason:
                    warnings.append(Finding("WARN", rel, lines, fail_limit, f"exception: {reason}"))
                else:
                    failures.append(Finding("FAIL", rel, lines, fail_limit))
    return warnings, failures


def main() -> int:
    warnings, failures = evaluate()
    for item in warnings:
        suffix = f" ({item.reason})" if item.reason else ""
        print(f"[{item.level}] {item.path}: {item.lines} lines > {item.limit}{suffix}")
    for item in failures:
        print(f"[{item.level}] {item.path}: {item.lines} lines > {item.limit}")
    if failures:
        print(f"\nFile size guardrails failed: {len(failures)} violation(s).")
        return 1
    print("\nFile size guardrails passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
