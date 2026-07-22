import importlib
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def fresh_storage_module(tmp_path, monkeypatch):
    """Provides an isolated storage module reloaded with test environment variables."""
    db_path = tmp_path / "test_nmpl.db"
    monkeypatch.setenv("NMPL_DB_FILE", str(db_path))
    monkeypatch.setenv("NMPL_EPHEMERAL_METRICS", "true")
    monkeypatch.setenv("NMPL_METRICS_RETENTION", "5")
    monkeypatch.setenv("NMPL_DB_BUSY_TIMEOUT_MS", "2000")

    if "core.storage" in sys.modules:
        module = importlib.reload(sys.modules["core.storage"])
    else:
        module = importlib.import_module("core.storage")

    yield module, db_path


@pytest.fixture
def storage_manager(fresh_storage_module):
    module, db_path = fresh_storage_module
    mgr = module.StorageManager()
    mgr.init_storage()
    return mgr, module, db_path


@pytest.fixture
def cli_module(tmp_path):
    """Provides isolated CLI module state and database instance per test."""
    if "cli" in sys.modules:
        cli = sys.modules["cli"]
    else:
        cli = importlib.import_module("cli")

    db_path = tmp_path / "test_cli.db"
    cli.db.db_file = str(db_path)
    cli.db.init_storage()

    cli._detectors.clear()
    cli._active_snapshots.clear()

    yield cli

    cli._detectors.clear()
    cli._active_snapshots.clear()


@pytest.fixture
def api_module(tmp_path):
    """Provides isolated API module state, rate limits, and database instance per test."""
    if "api" in sys.modules:
        api = sys.modules["api"]
    else:
        api = importlib.import_module("api")

    db_path = tmp_path / "test_api.db"
    api.db.db_file = str(db_path)
    api.db.init_storage()

    api._detectors.clear()
    api._worker_tasks.clear()
    api._in_flight.clear()
    api._active_snapshots.clear()
    api._last_incident_times.clear()
    api.limiter.reset()

    yield api

    api._detectors.clear()
    api._worker_tasks.clear()
    api._in_flight.clear()
    api._active_snapshots.clear()
    api._last_incident_times.clear()