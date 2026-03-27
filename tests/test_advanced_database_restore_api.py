from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, engine
from knowledge_miner.main import app


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def test_advanced_database_backups_route_lists_candidates(monkeypatch):
    monkeypatch.setattr(
        "knowledge_miner.main.database_readiness",
        lambda: {"sqlite_file_path": "/tmp/knowledge_miner.db"},
    )
    monkeypatch.setattr(
        "knowledge_miner.main.list_sqlite_backup_candidates",
        lambda: [
            {
                "name": "knowledge_miner.db.before_restore_manual",
                "path": "/tmp/knowledge_miner.db.before_restore_manual",
                "size_bytes": 123,
                "mtime": 1700000000,
                "kind": "legacy",
                "managed": False,
            }
        ],
    )
    monkeypatch.setattr("knowledge_miner.main.sqlite_backup_dir", lambda: None)
    client = TestClient(app)
    response = client.get("/v1/advanced/database-backups", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["database_target"] == "/tmp/knowledge_miner.db"
    assert body["backup_dir"] is None
    assert body["retention_count"] >= 0
    assert body["total"] == 1
    assert body["items"][0]["name"] == "knowledge_miner.db.before_restore_manual"
    assert body["items"][0]["kind"] == "legacy"


def test_advanced_database_backup_create_route_returns_backup_summary(monkeypatch):
    monkeypatch.setattr(
        "knowledge_miner.main.create_sqlite_backup",
        lambda kind: {
            "name": "knowledge_miner.db.manual_20260325_020000",
            "path": "/tmp/db_backups/knowledge_miner.db.manual_20260325_020000",
            "size_bytes": 456,
            "mtime": 1700001000,
            "kind": kind,
            "managed": True,
        },
    )
    monkeypatch.setattr("knowledge_miner.main.prune_sqlite_backups", lambda: {"pruned_auto_backups": 0})
    monkeypatch.setattr(
        "knowledge_miner.main.database_readiness",
        lambda: {"sqlite_file_path": "/tmp/knowledge_miner.db"},
    )
    monkeypatch.setattr("knowledge_miner.main.sqlite_backup_dir", lambda: __import__("pathlib").Path("/tmp/db_backups"))
    client = TestClient(app)
    response = client.post("/v1/advanced/database-backups", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["backup"]["kind"] == "manual"
    assert body["backup"]["managed"] is True
    assert body["backup_dir"] == "/tmp/db_backups"


def test_advanced_database_restore_route_requires_confirmation_match(monkeypatch):
    called = {"count": 0}

    def _unexpected_restore(_backup_name: str):
        called["count"] += 1
        return {}

    monkeypatch.setattr("knowledge_miner.main.restore_sqlite_backup", _unexpected_restore)
    client = TestClient(app)
    response = client.post(
        "/v1/advanced/database-restore",
        json={
            "backup_name": "knowledge_miner.db.before_restore_manual",
            "confirm_backup_name": "knowledge_miner.db.bak.20260324_085242",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "database_restore_confirmation_mismatch" in response.text
    assert called["count"] == 0


def test_advanced_database_restore_route_returns_restore_summary(monkeypatch):
    monkeypatch.setattr(
        "knowledge_miner.main.restore_sqlite_backup",
        lambda backup_name: {
            "restored_backup_name": backup_name,
            "restored_from": f"/tmp/{backup_name}",
            "snapshot_path": "/tmp/knowledge_miner.db.pre_restore_20260325_020000",
            "database_target": "/tmp/knowledge_miner.db",
            "database_inode": 111,
            "database_mtime": 222,
            "repaired_query_rows": 3,
            "superseded_runs": 1,
            "superseded_query_rows": 6,
        },
    )
    client = TestClient(app)
    response = client.post(
        "/v1/advanced/database-restore",
        json={
            "backup_name": "knowledge_miner.db.before_restore_manual",
            "confirm_backup_name": "knowledge_miner.db.before_restore_manual",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["restored_backup_name"] == "knowledge_miner.db.before_restore_manual"
    assert body["snapshot_path"].endswith(".pre_restore_20260325_020000")
    assert body["repaired_query_rows"] == 3
