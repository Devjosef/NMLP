"""
Contract under test: Detector.probe(target) / .status() / .is_alert /
.baseline_established / .current_loss_pct() / .current_latency_ms() /
.current_jitter_ms()
"""
from unittest.mock import patch
import pytest

from core.detect import Detector


def _run_probes(det, target, n, icmp_ok=True, icmp_latency=10.0, udp_ok=True):
    with patch("core.detect.icmp_probe", return_value=(icmp_ok, icmp_latency)), \
         patch("core.detect.udp_probe", return_value=udp_ok):
        for _ in range(n):
            det.probe(target)


class TestWarmup:
    def test_status_string_during_warmup(self):
        det = Detector(baseline_window=30)
        _run_probes(det, "8.8.8.8", 5)
        assert "Learning" in det.status()
        assert det.baseline_established is False

    def test_baseline_established_exactly_at_window_size(self):
        det = Detector(baseline_window=30)
        _run_probes(det, "8.8.8.8", 29)
        assert det.baseline_established is False
        _run_probes(det, "8.8.8.8", 1)
        det.status()
        assert det.baseline_established is True


class TestSteadyState:
    def test_ok_status_when_consistently_healthy(self):
        det = Detector(baseline_window=30)
        _run_probes(det, "8.8.8.8", 30, icmp_ok=True, udp_ok=True)
        assert det.status().startswith("OK")
        assert det.is_alert is False

    def test_absolute_ceiling_alerts_even_when_broken_since_before_warmup(self):
        det = Detector(baseline_window=30)
        _run_probes(det, "192.0.2.1", 30, icmp_ok=False, udp_ok=False)
        status = det.status()
        assert det.is_alert is True
        assert "absolute ceiling" in status.lower()

    def test_icmp_rate_limited_suppresses_alert_when_udp_still_ok(self):
        det = Detector(baseline_window=30)
        _run_probes(det, "8.8.8.8", 30, icmp_ok=False, udp_ok=True)
        status = det.status()
        assert "rate-limited" in status.lower()
        assert det.is_alert is False


class TestUdpClassification:
    def test_udp_filtered_when_icmp_healthy_but_udp_consistently_fails(self):
        det = Detector(baseline_window=30)
        with patch("core.detect.icmp_probe", return_value=(True, 10.0)), \
             patch("core.detect.udp_probe", return_value=False):
            for _ in range(30):
                det.probe("1.1.1.1")
        status = det.status()
        assert "udp filtered" in status.lower()
        assert "target unreachable" not in status.lower()

    def test_udp_unreachable_when_both_icmp_and_udp_fail(self):
        det = Detector(baseline_window=30)
        _run_probes(det, "192.0.2.1", 30, icmp_ok=False, udp_ok=False)
        status = det.status()
        assert "target unreachable" in status.lower()


class TestLatencySentinel:
    def test_none_latency_is_not_recorded_in_latency_history(self):
        det = Detector(baseline_window=30, recent_window=10)
        with patch("core.detect.icmp_probe", return_value=(True, None)), \
             patch("core.detect.udp_probe", return_value=True):
            for _ in range(10):
                det.probe("8.8.8.8")
        assert det.current_latency_ms() == 0.0
        assert det.current_jitter_ms() == 0.0

    def test_real_latency_averages_the_two_probe_sizes(self):
        det = Detector(baseline_window=30, recent_window=10)
        call_count = {"n": 0}

        def _side_effect(target, packet_size=64, timeout=1):
            call_count["n"] += 1
            return (True, 10.0) if packet_size == 64 else (True, 20.0)

        with patch("core.detect.icmp_probe", side_effect=_side_effect), \
             patch("core.detect.udp_probe", return_value=True):
            det.probe("8.8.8.8")
        assert det.current_latency_ms() == pytest.approx(15.0)