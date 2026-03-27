from __future__ import annotations

import importlib
import sys

import pytest


def _reload_config(monkeypatch, *, argv: list[str], database_url: str | None = None, pytest_runtime: bool = False):
    monkeypatch.setattr(sys, "argv", argv[:])
    if pytest_runtime:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_config_db_target.py::synthetic")
    else:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    if database_url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", database_url)
    sys.modules.pop("knowledge_miner.config", None)
    return importlib.import_module("knowledge_miner.config")


def test_server_runtime_defaults_to_live_db(monkeypatch):
    config = _reload_config(monkeypatch, argv=["uvicorn", "knowledge_miner.main:app"])
    assert config.classify_database_target(config.settings.database_url) == "live_app_db"
    assert config.expected_database_target_for_role() == "live_app_db"


def test_direct_python_runtime_requires_explicit_database_url(monkeypatch):
    with pytest.raises(RuntimeError, match="database_url_required_for_direct_python_runtime"):
        _reload_config(monkeypatch, argv=["python", "-c", "print('x')"])


def test_pytest_must_not_target_live_db(monkeypatch):
    with pytest.raises(RuntimeError, match="pytest_must_not_target_live_knowledge_miner_db"):
        _reload_config(
            monkeypatch,
            argv=["pytest", "-q"],
            database_url="sqlite:////home/garik/Documents/git/knowledge-miner/knowledge_miner.db",
            pytest_runtime=True,
        )


def test_database_target_warning_for_server_on_test_db(monkeypatch):
    config = _reload_config(
        monkeypatch,
        argv=["uvicorn", "knowledge_miner.main:app"],
        database_url="sqlite:////home/garik/Documents/git/knowledge-miner/test_knowledge_miner.db",
    )
    assert config.classify_database_target(config.settings.database_url) == "managed_test_db"
    assert config.database_target_warning(config.settings.database_url) == "server_not_using_live_app_database"
