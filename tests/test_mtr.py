"""
Contract under test: parse_mtr_output(stdout: str) -> List[Dict]
Each hop dict must have hop/host/loss (always numeric) and
last/avg/best/worst (float OR None).
"""
import subprocess
from unittest.mock import patch
import pytest

from core import mtr


SAMPLE_MTR_OUTPUT = """Start: 2026-07-17T10:00:00+0000
HOST: myhost                     Loss%   Snt   Last   Avg  Best  Wrst
  1.|-- 192.168.1.1               0.0%    10    3.9  20.9   3.2 112.6
  2.|-- 2.248.5.1                 0.0%    10    4.7  26.9   3.3  97.9
  3.|-- 81.228.85.239             0.0%    10    5.7  17.9   3.0 118.5
  4.|-- ???                     100.0%    10    0.0   0.0   0.0   0.0
"""


class TestParseMtrOutput:
    def test_normal_hops_parsed_with_numeric_fields(self):
        hops = mtr.parse_mtr_output(SAMPLE_MTR_OUTPUT)
        assert len(hops) == 4
        first = hops[0]
        assert first["hop"] == 1
        assert first["host"] == "192.168.1.1"
        assert first["loss"] == 0.0
        assert first["avg"] == pytest.approx(20.9)

    def test_unresponsive_hop_gets_100_loss_and_none_timing(self):
        hops = mtr.parse_mtr_output(SAMPLE_MTR_OUTPUT)
        dead_hop = hops[-1]
        assert dead_hop["host"] == "???"
        assert dead_hop["loss"] == 100.0
        assert dead_hop["last"] is None
        assert dead_hop["avg"] is None
        assert dead_hop["best"] is None
        assert dead_hop["worst"] is None

    def test_responsive_hop_with_unparseable_timing_field_logs_and_returns_none(self, caplog):
        drifted = (
            "Start: 2026-07-17T10:00:00+0000\n"
            "HOST: myhost                     Loss%   Snt   Last   Avg  Best  Wrst\n"
            "  1.|-- 192.168.1.1               0.0%    10  GARBAGE  20.9   3.2 112.6\n"
        )
        with caplog.at_level("WARNING"):
            hops = mtr.parse_mtr_output(drifted)
        assert len(hops) == 1
        assert hops[0]["last"] is None
        assert hops[0]["avg"] == pytest.approx(20.9)
        assert any("format drift" in rec.message for rec in caplog.records)

    def test_malformed_line_is_skipped_not_fatal(self):
        malformed = (
            "Start: 2026-07-17T10:00:00+0000\n"
            "HOST: myhost                     Loss%   Snt   Last   Avg  Best  Wrst\n"
            "  1.|-- 192.168.1.1               0.0%    10    3.9  20.9   3.2 112.6\n"
            "this line is complete garbage and matches nothing\n"
            "  2.|-- 2.248.5.1                 0.0%    10    4.7  26.9   3.3  97.9\n"
        )
        hops = mtr.parse_mtr_output(malformed)
        assert [h["hop"] for h in hops] == [1, 2]

    def test_empty_output_returns_empty_list(self):
        assert mtr.parse_mtr_output("") == []


class TestRunMtr:
    def test_binary_missing_returns_empty_list_no_exception(self):
        with patch.object(mtr, "is_mtr_available", return_value=False):
            result = mtr.run_mtr("8.8.8.8")
        assert result == []

    def test_subprocess_timeout_returns_empty_list(self):
        with patch.object(mtr, "is_mtr_available", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="mtr", timeout=120)):
            result = mtr.run_mtr("8.8.8.8")
        assert result == []