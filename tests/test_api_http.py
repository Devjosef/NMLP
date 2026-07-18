"""
HTTP-layer contract tests using FastAPI's TestClient. These exercise
api.py's actual route handlers, request validation, and response
shapes.
"""
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(api_module):
    with patch.object(api_module, "_ensure_worker") as mock_ensure:
        with TestClient(api_module.app) as c:
            yield c, api_module, mock_ensure


class TestDashboardAndArchivePages:
    def test_dashboard_root_returns_200(self, client):
        c, _, _ = client
        resp = c.get("/")
        assert resp.status_code == 200
        assert "NMPL" in resp.text

    def test_archive_page_returns_200_with_no_incidents(self, client):
        c, _, _ = client
        resp = c.get("/archive")
        assert resp.status_code == 200
        assert "No incidents match" in resp.text


class TestPartialMetrics:
    def test_valid_target_registers_and_calls_ensure_worker(self, client):
        c, api, mock_ensure = client
        resp = c.get("/partials/metrics?target=8.8.8.8")
        assert resp.status_code == 200
        assert "8.8.8.8" in api.db.get_active_targets()
        mock_ensure.assert_called_with("8.8.8.8")

    def test_junk_target_is_rejected_not_registered(self, client):
        c, api, _ = client
        resp = c.get("/partials/metrics?target=???")
        assert resp.status_code == 200
        assert "???" not in api.db.get_active_targets()

    def test_cap_reached_shows_placeholder_and_does_not_register(self, client):
        c, api, _ = client
        api.MAX_ACTIVE_TARGETS
        with patch.object(api, "MAX_ACTIVE_TARGETS", 1):
            c.get("/partials/metrics?target=8.8.8.8")
            resp = c.get("/partials/metrics?target=1.1.1.1")
        assert "limit reached" in resp.text.lower()
        assert "1.1.1.1" not in api.db.get_active_targets()


class TestPartialEngine:
    def test_no_target_no_incidents_returns_nominal(self, client):
        c, _, _ = client
        resp = c.get("/partials/engine")
        assert "NOMINAL" in resp.text

    def test_orphaned_incident_for_untracked_target_is_excluded_from_default_view(self, client):
        c, api, _ = client
        api.db.log_incident("192.0.2.1", {"summary": "ghost", "bottleneck": {}, "hops": []}, source="cli")
        resp = c.get("/partials/engine")
        assert "NOMINAL" in resp.text
        assert "ghost" not in resp.text


class TestDeleteTarget:
    def test_delete_resolves_open_incident_and_removes_target(self, client):
        c, api, _ = client
        api.db.register_active_target("192.0.2.1")
        inc_id = api.db.log_incident("192.0.2.1", {"summary": "s", "bottleneck": {}, "hops": []}, source="api")

        resp = c.delete("/targets/192.0.2.1")
        assert resp.status_code == 200
        assert "192.0.2.1" not in api.db.get_active_targets()
        assert api.db.get_incident(inc_id)[8] == 1

    def test_delete_invalid_target_returns_400(self, client):
        c, _, _ = client
        resp = c.delete("/targets/???")
        assert resp.status_code == 400


class TestRateLimiting:
    def test_rate_limit_returns_tr_fragment_for_htmx_caller(self, client):
        c, _, _ = client
        for _ in range(20):
            c.get("/partials/metrics?target=8.8.8.8", headers={"HX-Request": "true"})
        resp = c.get("/partials/metrics?target=8.8.8.8", headers={"HX-Request": "true"})

        assert resp.status_code == 429
        assert "<tr>" in resp.text

    def test_rate_limit_returns_json_for_non_htmx_caller(self, client):
        c, _, _ = client
        for _ in range(20):
            c.get("/partials/metrics?target=8.8.8.8")
        resp = c.get("/partials/metrics?target=8.8.8.8")

        assert resp.status_code == 429
        body = resp.json()
        assert "detail" in body

    def test_report_rate_limit_returns_span_fragment_not_tr(self, client):
        c, _, _ = client
        for _ in range(10):
            c.post("/report/snapshot", headers={"HX-Request": "true"})
        resp = c.post("/report/snapshot", headers={"HX-Request": "true"})

        assert resp.status_code == 429
        assert "<span" in resp.text
        assert "<tr>" not in resp.text


class TestDeleteIncidentHttp:
    def test_delete_open_incident_returns_400_and_does_not_delete(self, client):
        c, api, _ = client
        inc_id = api.db.log_incident("8.8.8.8", {"summary": "s", "bottleneck": {}, "hops": []})

        resp = c.delete(f"/incident/{inc_id}")
        assert resp.status_code == 400
        assert api.db.get_incident(inc_id) is not None

    def test_delete_resolved_incident_succeeds_and_row_is_gone(self, client):
        c, api, _ = client
        inc_id = api.db.log_incident("8.8.8.8", {"summary": "s", "bottleneck": {}, "hops": []})
        api.db.resolve_incident(inc_id)

        resp = c.delete(f"/incident/{inc_id}")
        assert resp.status_code == 200
        assert api.db.get_incident(inc_id) is None

    def test_delete_nonexistent_incident_returns_404(self, client):
        c, _, _ = client
        resp = c.delete("/incident/999999")
        assert resp.status_code == 404

    def test_delete_does_not_affect_other_incidents(self, client):
        c, api, _ = client
        keep_id = api.db.log_incident("1.1.1.1", {"summary": "keep me", "bottleneck": {}, "hops": []})
        api.db.resolve_incident(keep_id)
        gone_id = api.db.log_incident("8.8.8.8", {"summary": "delete me", "bottleneck": {}, "hops": []})
        api.db.resolve_incident(gone_id)

        c.delete(f"/incident/{gone_id}")
        assert api.db.get_incident(gone_id) is None
        assert api.db.get_incident(keep_id) is not None


class TestArchivePageAfterDelete:
    def test_deleted_incident_no_longer_appears_in_archive_listing(self, client):
        c, api, _ = client
        inc_id = api.db.log_incident("8.8.8.8", {"summary": "ephemeral", "bottleneck": {}, "hops": []})
        api.db.resolve_incident(inc_id)
        c.delete(f"/incident/{inc_id}")

        resp = c.get("/archive")
        assert "ephemeral" not in resp.text

    def test_archive_page_still_200_with_zero_incidents_after_deleting_the_only_one(self, client):
        c, api, _ = client
        inc_id = api.db.log_incident("8.8.8.8", {"summary": "s", "bottleneck": {}, "hops": []})
        api.db.resolve_incident(inc_id)
        c.delete(f"/incident/{inc_id}")

        resp = c.get("/archive")
        assert resp.status_code == 200
        assert "No incidents match" in resp.text