"""
Contract under test: analyze_path(hops: List[Dict]) -> Dict
"""
import pytest
from core.analyzer import analyze_path, CRITICAL_LOSS_THRESHOLD, ELEVATED_LOSS_THRESHOLD


def _hop(hop, host, loss, avg=10.0, worst=20.0, last=10.0, best=5.0, sent=10):
    return {"hop": hop, "host": host, "loss": loss, "sent": sent,
            "last": last, "avg": avg, "best": best, "worst": worst}


class TestAnalyzePath:
    def test_empty_hops_returns_error_shape(self):
        result = analyze_path([])
        assert result["status"] == "error"
        assert result["bottleneck"] is None

    def test_healthy_path_all_zero_loss(self):
        hops = [_hop(1, "192.168.1.1", 0.0), _hop(2, "8.8.8.8", 0.0)]
        result = analyze_path(hops)
        assert result["suggestion"] == "Path healthy"
        assert result["elevated"] is False
        assert result["likely_rate_limited"] is False

    def test_rate_limited_pattern_high_mid_loss_clean_final_hop(self):
        hops = [
            _hop(1, "192.168.1.1", 0.0),
            _hop(2, "10.0.0.1", 60.0),
            _hop(3, "8.8.8.8", 0.0),
        ]
        result = analyze_path(hops)
        assert result["likely_rate_limited"] is True
        assert "rate-limited" in result["suggestion"].lower()

    def test_critical_with_forward_loss_inheritance(self):
        hops = [
            _hop(1, "192.168.1.1", 0.0),
            _hop(2, "10.0.0.1", 60.0),
            _hop(3, "10.0.0.2", 40.0),
            _hop(4, "10.0.0.3", 55.0),
        ]
        result = analyze_path(hops)
        assert result["forwardloss_inherited"] is True
        assert "critical" in result["suggestion"].lower()

    def test_elevated_tier_between_thresholds(self):
        mid_loss = (ELEVATED_LOSS_THRESHOLD + CRITICAL_LOSS_THRESHOLD) / 2
        hops = [_hop(1, "192.168.1.1", 0.0), _hop(2, "10.0.0.1", mid_loss)]
        result = analyze_path(hops)
        assert result["elevated"] is True
        assert "elevated" in result["suggestion"].lower()

    def test_none_latency_fields_are_filtered_not_fatal(self):
        hops = [
            _hop(1, "192.168.1.1", 0.0, avg=10.0, worst=20.0),
            {**_hop(2, "???", 100.0), "avg": None, "worst": None, "last": None, "best": None},
        ]
        result = analyze_path(hops)
        assert result["average_latency"] == pytest.approx(10.0)
        assert result["worst_latency"] == pytest.approx(20.0)