"""Tests that rate-limited hops present caveated observations, instead of operator blame assertions."""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from core.analyzer import analyze_path


@pytest.fixture
def client(api_module):
    with patch.object(api_module, "_ensure_worker"):
        with TestClient(api_module.app) as c:
            yield c, api_module


def _build_rate_limited_incident_payload():
    """Builds a trace with a mid-path loss spike and clean final hop (likely_rate_limited)."""
    hops = [
        {
            "hop": 1, "host": "192.168.1.1", "loss": 0.0, "sent": 10,
            "last": 4.8, "avg": 5.0, "best": 4.1, "worst": 6.2,
            "enrichment": {"status": "unavailable", "org": None, "asn": None, "country": None, "source": None},
        },
        {
            "hop": 2, "host": "4.4.4.4", "loss": 60.0, "sent": 10,
            "last": 18.5, "avg": 20.0, "best": 15.2, "worst": 30.1,
            "enrichment": {
                "status": "confirmed", "org": "Level3 Communications",
                "asn": "3356", "country": "US", "source": "cymru",
            },
        },
        {
            "hop": 3, "host": "8.8.8.8", "loss": 0.0, "sent": 10,
            "last": 14.0, "avg": 15.0, "best": 12.5, "worst": 22.0,
            "enrichment": {
                "status": "confirmed", "org": "Google LLC",
                "asn": "15169", "country": "US", "source": "rdap",
            },
        },
    ]

    analysis = analyze_path(hops)
    assert analysis["likely_rate_limited"] is True, "test fixture must trigger rate-limited classification"
    assert analysis["bottleneck"]["host"] == "4.4.4.4", "test fixture must have a real bottleneck host"

    summary_lines = [
        "Traceroute to 8.8.8.8:",
        "OK Hop 1: 0.0% -> 192.168.1.1 (avg: 5.0 ms)",
        "BAD Hop 2: 60.0% -> 4.4.4.4 (avg: 20.0 ms)",
        "OK Hop 3: 0.0% -> 8.8.8.8 (avg: 15.0 ms)",
        "",
        "Analysis:",
        f"Total Hops: {analysis['total_hops']}",
        f"Total Loss (path average, not delivered-packet loss): {analysis['total_loss']:.1f}%",
        f"Average Latency: {analysis['average_latency']:.1f} ms",
        f"Bottleneck: Hop {analysis['bottleneck']['hop']} ({analysis['bottleneck']['host']}) with {analysis['bottleneck']['loss']:.1f}% loss",
        f"Next: {analysis['suggestion']}",
    ]

    return {
        "summary": "\n".join(summary_lines),
        "bottleneck": analysis["bottleneck"],
        "hops": hops,
        "analyzer": analysis,
    }


class TestReportDoesNotAssertBlameOnCosmeticLoss:
    def test_bottleneck_operator_line_is_absent_when_likely_rate_limited(self, client):
        c, api = client
        payload = _build_rate_limited_incident_payload()
        inc_id = api.db.log_incident("8.8.8.8", payload, source="api")

        resp = c.post(f"/report/{inc_id}")
        assert resp.status_code == 200
        text = resp.text

        assert "Bottleneck Operator:" not in text, \
            "a likely-rate-limited hop must never be reported with the same phrasing as a confirmed fault"

    def test_report_instead_shows_caveated_observation_naming_the_org(self, client):
        c, api = client
        payload = _build_rate_limited_incident_payload()
        inc_id = api.db.log_incident("8.8.8.8", payload, source="api")

        resp = c.post(f"/report/{inc_id}")
        text = resp.text

        assert "Level3 Communications" in text, "the org should still be surfaced -- just not as an assertion of blame"
        assert "confirmed via cymru" in text
        assert "not asserted as the cause" in text.lower()

    def test_total_loss_line_is_present_and_labeled_as_path_average(self, client):
        c, api = client
        payload = _build_rate_limited_incident_payload()
        inc_id = api.db.log_incident("8.8.8.8", payload, source="api")

        resp = c.post(f"/report/{inc_id}")
        text = resp.text

        assert "Total Loss" in text
        assert "path average" in text.lower(), \
            "Total Loss must be labeled as a path average, not implied to be end-to-end delivered loss"

    def test_json_payload_carries_likely_rate_limited_flag(self, client):
        c, api = client
        payload = _build_rate_limited_incident_payload()
        inc_id = api.db.log_incident("8.8.8.8", payload, source="api")

        resp = c.post(f"/report/{inc_id}")
        text = resp.text

        assert '"likely_rate_limited": true' in text


class TestReportStillAssertsBlameOnGenuineFault:
    def test_bottleneck_operator_line_is_present_when_loss_is_sustained(self, client):
        """Verifies that sustained loss persisting to the destination still asserts operator blame."""
        c, api = client
        hops = [
            {
                "hop": 1, "host": "192.168.1.1", "loss": 0.0, "sent": 10,
                "last": 4.8, "avg": 5.0, "best": 4.1, "worst": 6.2,
                "enrichment": {"status": "unavailable", "org": None, "asn": None, "country": None, "source": None},
            },
            {
                "hop": 2, "host": "4.4.4.4", "loss": 55.0, "sent": 10,
                "last": 18.5, "avg": 20.0, "best": 15.2, "worst": 30.1,
                "enrichment": {
                    "status": "confirmed", "org": "Level3 Communications",
                    "asn": "3356", "country": "US", "source": "cymru",
                },
            },
            {
                "hop": 3, "host": "8.8.8.8", "loss": 50.0, "sent": 10,
                "last": 14.0, "avg": 15.0, "best": 12.5, "worst": 22.0,
                "enrichment": {
                    "status": "confirmed", "org": "Google LLC",
                    "asn": "15169", "country": "US", "source": "rdap",
                },
            },
        ]
        analysis = analyze_path(hops)
        assert analysis["likely_rate_limited"] is False, "loss persisting to the destination must NOT classify as rate-limited"

        payload = {
            "summary": "Traceroute to 8.8.8.8:\nsustained loss case",
            "bottleneck": analysis["bottleneck"],
            "hops": hops,
            "analyzer": analysis,
        }
        inc_id = api.db.log_incident("8.8.8.8", payload, source="api")

        resp = c.post(f"/report/{inc_id}")
        text = resp.text

        assert "Bottleneck Operator: Level3 Communications" in text