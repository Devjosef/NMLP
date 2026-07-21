"""
Contract under test: StorageManager -- write/read roundtrips, the
busy_timeout PRAGMA, source-column provenance, DatabaseLockedError
distinguishability, and the ephemeral-metrics retention trigger.
"""
import sqlite3
import pytest


class TestSchemaAndPragma:
    def test_init_storage_creates_all_tables(self, storage_manager):
        mgr, _, db_path = storage_manager
        assert mgr.db_file == str(db_path)

        with sqlite3.connect(str(db_path)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"metrics_timeline", "network_incidents", "active_targets"} <= tables

    def test_source_column_exists_on_both_tables(self, storage_manager):
        ...
        mgr, _, db_path = storage_manager
        assert mgr is not None
        
        with sqlite3.connect(str(db_path)) as conn:
            metrics_cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics_timeline)")}
            incident_cols = {r[1] for r in conn.execute("PRAGMA table_info(network_incidents)")}
        assert "source" in metrics_cols
        assert "source" in incident_cols

    def test_busy_timeout_pragma_is_applied_on_connect(self, storage_manager):
        mgr, _, _ = storage_manager
        conn = mgr._connect()
        actual = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert actual == 2000


class TestHeartbeatRoundtrip:
    def test_log_heartbeat_then_read_back_with_source(self, storage_manager):
        mgr, _, db_path = storage_manager
        mgr.log_heartbeat(
            "8.8.8.8", "OK ICMP: 0.0%", "OK",
            {"latency_ms": 12.3, "loss_pct": 0.0, "jitter_ms": 1.1},
            source="api"
        )
        
        with sqlite3.connect(str(db_path)) as conn:
            db_row = conn.execute(
                "SELECT target, status_flag, latency_ms, source FROM metrics_timeline WHERE target='8.8.8.8'"
            ).fetchone()
            
        assert db_row is not None
        assert db_row[0] == "8.8.8.8"
        assert db_row[1] == "OK"
        assert db_row[2] == pytest.approx(12.3)
        assert db_row[3] == "api"

        rows = mgr.get_metrics_timeline(target="8.8.8.8", limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row[2] == "8.8.8.8"
        assert row[4] == "OK"
        assert row[5] == pytest.approx(12.3)
        assert row[8] == "api"


class TestIncidentLifecycle:
    def test_log_incident_then_get_open_incident(self, storage_manager):
        mgr, _, _ = storage_manager
        payload = {"summary": "trace summary", "bottleneck": {"hop": 4, "host": "x", "loss": 60.0}, "hops": []}
        inc_id = mgr.log_incident("192.0.2.1", payload, source="cli")
        open_inc = mgr.get_open_incident("192.0.2.1")
        assert open_inc is not None
        assert open_inc[0] == inc_id
        assert open_inc[4] == 4

    def test_update_incident_coalesces_source_when_not_provided(self, storage_manager):
        mgr, _, _ = storage_manager
        payload = {"summary": "s1", "bottleneck": {}, "hops": []}
        inc_id = mgr.log_incident("192.0.2.1", payload, source="cli")

        mgr.update_incident(inc_id, {"summary": "s2", "bottleneck": {}, "hops": []}, source=None)
        row = mgr.get_incident(inc_id)
        assert row[3] == "s2"
        assert row[10] == "cli"

    def test_resolve_incident_sets_flag_and_timestamp(self, storage_manager):
        mgr, _, _ = storage_manager
        inc_id = mgr.log_incident("192.0.2.1", {"summary": "s", "bottleneck": {}, "hops": []})
        mgr.resolve_incident(inc_id)
        row = mgr.get_incident(inc_id)
        assert row[8] == 1
        assert row[9] is not None


class TestEphemeralRetention:
    def test_metrics_beyond_retention_cap_are_trimmed(self, storage_manager):
        mgr, _, db_path = storage_manager
        for i in range(12):
            # Converted f-string to static string to eliminate unnecessary formatting execution
            mgr.log_heartbeat("target", "OK", "OK",
                               {"latency_ms": 1.0, "loss_pct": 0.0, "jitter_ms": 0.0})
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM metrics_timeline").fetchone()[0]
        assert count == 5


class TestLockContention:
    def test_operational_error_containing_locked_is_wrapped(self, storage_manager, monkeypatch):
        mgr, module, _ = storage_manager

        def _boom(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(mgr, "_connect", _boom)
        with pytest.raises(module.DatabaseLockedError):
            mgr.log_heartbeat("8.8.8.8", "OK", "OK",
                               {"latency_ms": 0.0, "loss_pct": 0.0, "jitter_ms": 0.0})

    def test_unrelated_operational_error_is_not_wrapped(self, storage_manager, monkeypatch):
        mgr, _, _ = storage_manager

        def _boom(*_args, **_kwargs):
            raise sqlite3.OperationalError("no such table: bogus")

        monkeypatch.setattr(mgr, "_connect", _boom)
        with pytest.raises(sqlite3.OperationalError):
            mgr.log_heartbeat("8.8.8.8", "OK", "OK",
                               {"latency_ms": 0.0, "loss_pct": 0.0, "jitter_ms": 0.0})


class TestDeleteIncidentStorage:
    def test_delete_incident_removes_row_from_db(self, storage_manager):
        mgr, _, _ = storage_manager
        inc_id = mgr.log_incident("8.8.8.8", {"summary": "s", "bottleneck": {}, "hops": []})
        mgr.resolve_incident(inc_id)

        deleted = mgr.delete_incident(inc_id)
        assert deleted is True
        assert mgr.get_incident(inc_id) is None

    def test_delete_nonexistent_incident_returns_false(self, storage_manager):
        mgr, _, _ = storage_manager
        assert mgr.delete_incident(99999) is False