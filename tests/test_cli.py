"""
Contract under test: cli.py's daemon-mode functions — probe_target's
persistent-Detector reuse, evaluate_and_route_incident's incident
open/update lifecycle and empty-hops guard, and run_once's single-shot
alert/resolve branching.
"""
from unittest.mock import patch


class TestEvaluateAndLog:
    def test_metrics_dict_shape_written_to_heartbeat(self, cli_module):
        fake_result = {
            "summary": "OK ICMP: 0.0%",
            "is_alert": False,
            "metrics": {"loss_pct": 0.0, "latency_ms": 12.0, "jitter_ms": 1.0},
        }
        with patch.object(cli_module.__main__, "main_with_result", return_value=fake_result):
            _, is_alert, status_flag = cli_module._evaluate_and_log("8.8.8.8", ["8.8.8.8"])

        assert is_alert is False
        assert status_flag == "OK"
        row = cli_module.db.get_metrics_timeline(target="8.8.8.8", limit=1)[0]
        assert row[8] == "cli-once"


class TestRunOnce:
    def test_alert_triggers_incident_route(self, cli_module):
        fake_result = {
            "summary": "ALERT", "is_alert": True,
            "metrics": {"loss_pct": 100.0, "latency_ms": 0.0, "jitter_ms": 0.0},
            "csv": "",
        }
        with patch.object(cli_module.__main__, "main_with_result", return_value=fake_result), \
             patch.object(cli_module, "evaluate_and_route_incident") as mock_route:
            cli_module.run_once(["192.0.2.1"])
        mock_route.assert_called_once()

    def test_ok_after_prior_open_incident_resolves_it(self, cli_module):
        inc_id = cli_module.db.log_incident(
            "192.0.2.1", {"summary": "s", "bottleneck": {}, "hops": []}, source="cli"
        )
        fake_result = {
            "summary": "OK", "is_alert": False,
            "metrics": {"loss_pct": 0.0, "latency_ms": 1.0, "jitter_ms": 0.1},
            "csv": "",
        }
        with patch.object(cli_module.__main__, "main_with_result", return_value=fake_result):
            cli_module.run_once(["192.0.2.1"])
        assert cli_module.db.get_open_incident("192.0.2.1") is None
        row = cli_module.db.get_incident(inc_id)
        assert row[8] == 1


class TestProbeTargetPersistence:
    def test_same_target_reuses_one_detector_across_calls(self, cli_module):
        with patch("core.detect.icmp_probe", return_value=(True, 10.0)), \
             patch("core.detect.udp_probe", return_value=True):
            cli_module.probe_target("8.8.8.8", [], {}, 15.0)
            det_after_first = cli_module._get_detector("8.8.8.8")
            cli_module.probe_target("8.8.8.8", [], {}, 15.0)
            det_after_second = cli_module._get_detector("8.8.8.8")

        assert det_after_first is det_after_second
        assert len(det_after_second.icmp_loss_history) == 2

    def test_in_flight_guard_skips_overlapping_probe_for_same_target(self, cli_module):
        cli_module._in_flight.add("8.8.8.8")
        with patch("core.detect.icmp_probe") as mock_probe:
            cli_module.probe_target("8.8.8.8", [], {}, 15.0)
        mock_probe.assert_not_called()

    def test_alert_transition_opens_incident_and_ok_transition_resolves_it(self, cli_module):
        with patch("core.detect.icmp_probe", return_value=(False, None)), \
             patch("core.detect.udp_probe", return_value=False):
            for _ in range(30):
                cli_module.probe_target("192.0.2.1", [], {}, 0.0)

        with patch.object(cli_module, "evaluate_and_route_incident") as mock_route:
            with patch("core.detect.icmp_probe", return_value=(False, None)), \
                 patch("core.detect.udp_probe", return_value=False):
                cli_module.probe_target("192.0.2.1", [], {}, 0.0)
        mock_route.assert_called_once()


class TestEvaluateAndRouteIncident:
    def test_empty_hops_skips_writing_any_incident(self, cli_module):
        fake_result = {"hops": [], "summary": "No hops data"}
        with patch.object(cli_module.__main__, "main_with_result", return_value=fake_result):
            cli_module.evaluate_and_route_incident(["192.0.2.1"], "192.0.2.1")
        assert cli_module.db.get_open_incident("192.0.2.1") is None

    def test_no_existing_open_incident_creates_new_row(self, cli_module):
        fake_result = {
            "hops": [{"hop": 1, "host": "x", "loss": 50.0}],
            "summary": "trace", "bottleneck": {"hop": 1, "host": "x", "loss": 50.0},
        }
        with patch.object(cli_module.__main__, "main_with_result", return_value=fake_result):
            cli_module.evaluate_and_route_incident(["192.0.2.1"], "192.0.2.1")
        assert cli_module.db.get_open_incident("192.0.2.1") is not None

    def test_existing_open_incident_is_updated_not_duplicated(self, cli_module):
        first_id = cli_module.db.log_incident(
            "192.0.2.1", {"summary": "old", "bottleneck": {}, "hops": []}, source="cli"
        )
        fake_result = {
            "hops": [{"hop": 1, "host": "x", "loss": 50.0}],
            "summary": "new trace", "bottleneck": {"hop": 1, "host": "x", "loss": 50.0},
        }
        with patch.object(cli_module.__main__, "main_with_result", return_value=fake_result):
            cli_module.evaluate_and_route_incident(["192.0.2.1"], "192.0.2.1")

        open_now = cli_module.db.get_open_incident("192.0.2.1")
        assert open_now[0] == first_id
        assert cli_module.db.get_incident(first_id)[3] == "new trace"

    def test_snapshot_guard_prevents_reentrant_call_for_same_target(self, cli_module):
        cli_module._active_snapshots.add("192.0.2.1")
        with patch.object(cli_module.__main__, "main_with_result") as mock_main:
            cli_module.evaluate_and_route_incident(["192.0.2.1"], "192.0.2.1")
        mock_main.assert_not_called()