from __future__ import annotations

from pathlib import Path
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
REQUIRED_TABLES = ("runs", "sources", "acquisition_runs", "parse_runs")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sqlite_file_path(database_url: str) -> str | None:
    raw = database_url.strip()
    if not raw.lower().startswith("sqlite:///"):
        return None
    if raw.startswith("sqlite:///:memory:"):
        return ":memory:"
    return str(Path(raw[len("sqlite:///") :]).resolve())


def sqlite_file_metadata(database_url: str) -> dict:
    path = sqlite_file_path(database_url)
    if not path or path == ":memory:":
        return {"path": path, "inode": None, "mtime": None}
    target = Path(path)
    if not target.exists():
        return {"path": path, "inode": None, "mtime": None}
    stat = target.stat()
    return {"path": path, "inode": int(stat.st_ino), "mtime": int(stat.st_mtime)}


def database_readiness() -> dict:
    file_meta = sqlite_file_metadata(settings.database_url)
    try:
        names = set(inspect(engine).get_table_names())
    except SQLAlchemyError as exc:
        return {
            "ready": False,
            "missing_tables": list(REQUIRED_TABLES),
            "error": f"{exc.__class__.__name__}: {exc}",
            "required_tables": list(REQUIRED_TABLES),
            "database_url": settings.database_url,
            "sqlite_file_path": file_meta["path"],
            "sqlite_file_inode": file_meta["inode"],
            "sqlite_file_mtime": file_meta["mtime"],
        }
    missing = [name for name in REQUIRED_TABLES if name not in names]
    return {
        "ready": not missing,
        "missing_tables": missing,
        "error": None if not missing else "schema_missing",
        "required_tables": list(REQUIRED_TABLES),
        "database_url": settings.database_url,
        "sqlite_file_path": file_meta["path"],
        "sqlite_file_inode": file_meta["inode"],
        "sqlite_file_mtime": file_meta["mtime"],
    }
