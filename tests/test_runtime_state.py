from __future__ import annotations

import errno
from pathlib import Path

from knowledge_miner import runtime_state
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


def test_acquire_instance_lock_allows_reload_worker_when_parent_holds_lock(tmp_path, monkeypatch):
    def fake_flock(fd, flags):
        raise OSError(errno.EWOULDBLOCK, "locked")

    monkeypatch.setattr(runtime_state.fcntl, "flock", fake_flock)
    monkeypatch.setattr(runtime_state, "_read_proc_cmdline", lambda pid: "python -m uvicorn app --reload" if pid == 100 else "python -c ... --multiprocessing-fork")
    monkeypatch.setattr(runtime_state.os, "getpid", lambda: 200)
    monkeypatch.setattr(runtime_state.os, "getppid", lambda: 100)

    assert runtime_state.acquire_instance_lock(base_dir=tmp_path / "runtime") is True
    assert runtime_state.is_primary_instance() is True
