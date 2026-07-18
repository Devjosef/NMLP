"""
Contract under test: 
icmp_probe(target, packet_size, timeout) -> (bool, float | None)
udp_probe(target, timeout) -> bool
"""
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from core import probes


def _fake_completed(stdout="", returncode=0):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


class TestIcmpProbe:
    def test_success_parses_latency_from_linux_style_output(self):
        stdout = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=13.4 ms"
        with patch.object(probes, "_SYSTEM", "linux"), \
             patch("subprocess.run", return_value=_fake_completed(stdout, 0)):
            success, latency = probes.icmp_probe("8.8.8.8")
        assert success is True
        assert latency == pytest.approx(13.4)

    def test_success_but_unparseable_output_returns_none_not_zero(self):
        stdout = "some totally different ping output format we've never seen"
        with patch.object(probes, "_SYSTEM", "linux"), \
             patch("subprocess.run", return_value=_fake_completed(stdout, 0)):
            success, latency = probes.icmp_probe("8.8.8.8")
        assert success is True
        assert latency is None

    def test_nonzero_returncode_is_failure_with_no_latency(self):
        with patch.object(probes, "_SYSTEM", "linux"), \
             patch("subprocess.run", return_value=_fake_completed("", 1)):
            success, latency = probes.icmp_probe("10.255.255.1")
        assert success is False
        assert latency is None

    def test_timeout_expired_is_failure_not_exception(self):
        with patch.object(probes, "_SYSTEM", "linux"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ping", timeout=3)):
            success, latency = probes.icmp_probe("10.255.255.1", timeout=1)
        assert success is False
        assert latency is None

    def test_windows_command_uses_dash_l_and_dash_w_not_dash_s_dash_cap_w(self):
        captured_cmd = {}

        def _capture(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            return _fake_completed("Reply from 8.8.8.8: time=10ms", 0)

        with patch.object(probes, "_SYSTEM", "windows"), \
             patch("subprocess.run", side_effect=_capture):
            probes.icmp_probe("8.8.8.8", packet_size=64, timeout=1)

        cmd = captured_cmd["cmd"]
        assert "-l" in cmd
        assert "-w" in cmd
        assert "-s" not in cmd
        assert "-W" not in cmd

    def test_darwin_timeout_is_milliseconds_linux_timeout_is_seconds(self):
        darwin_cmd, linux_cmd = {}, {}

        def _capture_darwin(cmd, **kwargs):
            darwin_cmd["cmd"] = cmd
            return _fake_completed("", 0)

        def _capture_linux(cmd, **kwargs):
            linux_cmd["cmd"] = cmd
            return _fake_completed("", 0)

        with patch.object(probes, "_SYSTEM", "darwin"), \
             patch("subprocess.run", side_effect=_capture_darwin):
            probes.icmp_probe("8.8.8.8", timeout=1)
        with patch.object(probes, "_SYSTEM", "linux"), \
             patch("subprocess.run", side_effect=_capture_linux):
            probes.icmp_probe("8.8.8.8", timeout=1)

        w_idx_darwin = darwin_cmd["cmd"].index("-W") + 1
        w_idx_linux = linux_cmd["cmd"].index("-W") + 1
        assert darwin_cmd["cmd"][w_idx_darwin] == "1000"
        assert linux_cmd["cmd"][w_idx_linux] == "1"


class TestUdpProbe:
    def test_timeout_returns_false_not_exception(self):
        import socket as socket_mod

        class _FakeSocket:
            def settimeout(self, t): pass
            def sendto(self, *a): pass
            def recvfrom(self, *a): raise socket_mod.timeout()
            def close(self): pass

        with patch("socket.socket", return_value=_FakeSocket()):
            assert probes.udp_probe("8.8.8.8") is False