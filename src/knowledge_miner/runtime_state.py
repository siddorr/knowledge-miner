from __future__ import annotations

from dataclasses import dataclass
import fcntl
import logging
import os
from pathlib import Path


_log = logging.getLogger("knowledge_miner")
_instance_lock_fd: int | None = None
_is_primary_instance = True


@dataclass(frozen=True)
class CleanupResult:
    enabled: bool
    removed_paths: tuple[str, ...]

    @property
    def removed_count(self) -> int:
        return len(self.removed_paths)


def _locks_dir(base_dir: str | Path) -> Path:
    return Path(base_dir) / "locks"


def _stops_dir(base_dir: str | Path) -> Path:
    return Path(base_dir) / "stops"


def cleanup_runtime_state(*, base_dir: str | Path, enabled: bool) -> CleanupResult:
    locks_dir = _locks_dir(base_dir)
    if not enabled:
        return CleanupResult(enabled=False, removed_paths=tuple())
    removed: list[str] = []
    if locks_dir.exists():
        for lock_path in sorted(locks_dir.glob("*.lock")):
            try:
                lock_path.unlink()
                removed.append(str(lock_path))
            except FileNotFoundError:
                continue
    return CleanupResult(enabled=True, removed_paths=tuple(removed))


def acquire_instance_lock(*, base_dir: str | Path) -> bool:
    global _instance_lock_fd, _is_primary_instance
    lock_file = Path(base_dir) / "instance.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        _is_primary_instance = False
        return False
    _instance_lock_fd = fd
    _is_primary_instance = True
    return True


def is_primary_instance() -> bool:
    return _is_primary_instance


def acquire_run_lock(*, base_dir: str | Path, phase: str, run_id: str) -> Path | None:
    locks_dir = _locks_dir(base_dir)
    locks_dir.mkdir(parents=True, exist_ok=True)
    safe_run_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in run_id)
    path = locks_dir / f"{phase}_{safe_run_id}.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
        return path
    except FileExistsError:
        return None


def release_run_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _stop_path(*, base_dir: str | Path, phase: str, run_id: str) -> Path:
    stops_dir = _stops_dir(base_dir)
    stops_dir.mkdir(parents=True, exist_ok=True)
    safe_run_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in run_id)
    return stops_dir / f"{phase}_{safe_run_id}.stop"


def request_run_stop(*, base_dir: str | Path, phase: str, run_id: str) -> Path:
    path = _stop_path(base_dir=base_dir, phase=phase, run_id=run_id)
    path.write_text("stop_requested\n", encoding="utf-8")
    return path


def clear_run_stop_request(*, base_dir: str | Path, phase: str, run_id: str) -> None:
    path = _stop_path(base_dir=base_dir, phase=phase, run_id=run_id)
    path.unlink(missing_ok=True)


def is_run_stop_requested(*, base_dir: str | Path, phase: str, run_id: str) -> bool:
    return _stop_path(base_dir=base_dir, phase=phase, run_id=run_id).exists()


def log_cleanup_result(result: CleanupResult) -> None:
    if not result.enabled:
        _log.info("Runtime cleanup disabled (CLEAN_ON_STARTUP=false).")
        return
    if result.removed_count == 0:
        _log.info("Runtime cleanup completed: no stale lock files found.")
        return
    _log.info("Runtime cleanup removed %s stale lock file(s).", result.removed_count)
