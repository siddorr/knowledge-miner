from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import settings


_CONFIGURED = False


def configure_logging() -> Path:
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return _log_path()

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_file_path = _log_path()
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    file_handler = RotatingFileHandler(
        filename=log_file_path,
        maxBytes=max(1024, int(settings.log_max_bytes)),
        backupCount=max(1, int(settings.log_backup_count)),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Apply handlers to root logger so all project/runtime logs persist.
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    # Keep these loggers unified through root handlers.
    for name in ("knowledge_miner", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(level)
        logger.propagate = True

    _CONFIGURED = True
    return log_file_path


def _log_path() -> Path:
    return Path(settings.log_dir) / settings.log_file
