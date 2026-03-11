from __future__ import annotations

from pathlib import Path

from knowledge_miner.runtime_state import cleanup_runtime_state


def test_cleanup_runtime_state_removes_stale_locks(tmp_path):
    locks_dir = tmp_path / "runtime" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    stale = locks_dir / "discovery_run_1.lock"
    stale.write_text("", encoding="utf-8")

    result = cleanup_runtime_state(base_dir=tmp_path / "runtime", enabled=True)
    assert result.enabled is True
    assert result.removed_count == 1
    assert not stale.exists()


def test_cleanup_runtime_state_noop_when_no_locks(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    result = cleanup_runtime_state(base_dir=runtime_dir, enabled=True)
    assert result.enabled is True
    assert result.removed_count == 0


def test_cleanup_runtime_state_disabled(tmp_path):
    locks_dir = tmp_path / "runtime" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    stale = locks_dir / "parse_run_1.lock"
    stale.write_text("", encoding="utf-8")

    result = cleanup_runtime_state(base_dir=tmp_path / "runtime", enabled=False)
    assert result.enabled is False
    assert result.removed_count == 0
    assert stale.exists()
