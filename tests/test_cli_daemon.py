"""Tests for cli.py's run_daemon scheduling loop."""

import threading
import time
from unittest.mock import patch



def _run_daemon_bounded(cli_module, targets, interval, max_wait=2.0, stop_after_calls=None):
    call_log = []
    stop_event = threading.Event()

    def _tracking_probe(target, *args, **kwargs):
        call_log.append((target, time.time()))
        if stop_after_calls is not None and len(call_log) >= stop_after_calls:
            stop_event.set()

    def _patched_probe_target(target, *args, **kwargs):
        _tracking_probe(target)

    with patch.object(cli_module, "probe_target", side_effect=_patched_probe_target):
        thread = threading.Thread(
            target=cli_module.run_daemon,
            args=(targets, interval, []),
            daemon=True,
        )
        thread.start()

        deadline = time.time() + max_wait
        while not stop_event.is_set() and time.time() < deadline:
            time.sleep(0.01)

    return call_log


class TestRunDaemonScheduling:
    def test_all_registered_targets_are_probed_each_tick(self, cli_module):
        cli_module.db.register_active_target("8.8.8.8")
        cli_module.db.register_active_target("1.1.1.1")

        call_log = _run_daemon_bounded(
            cli_module, ["8.8.8.8", "1.1.1.1"], interval=0.2, max_wait=1.0, stop_after_calls=4
        )
        probed_targets = {t for t, _ in call_log}
        assert probed_targets == {"8.8.8.8", "1.1.1.1"}, \
            "both registered targets must appear in the same or consecutive ticks"

    def test_tick_interval_is_respected_under_fast_probes(self, cli_module):
        cli_module.db.register_active_target("8.8.8.8")

        call_log = _run_daemon_bounded(
            cli_module, ["8.8.8.8"], interval=0.3, max_wait=1.5, stop_after_calls=3
        )
        assert len(call_log) >= 3
        gaps = [call_log[i + 1][1] - call_log[i][1] for i in range(len(call_log) - 1)]
        for gap in gaps:
            assert gap > 0.15, f"tick fired too soon after the previous one: {gap:.3f}s gap"

    def test_new_target_added_mid_run_is_picked_up_without_restart(self, cli_module):
        cli_module.db.register_active_target("8.8.8.8")

        call_log = []
        stop_event = threading.Event()

        def _tracking_probe(target, *args, **kwargs):
            call_log.append(target)
            if target == "1.1.1.1":
                stop_event.set()

        with patch.object(cli_module, "probe_target", side_effect=_tracking_probe):
            thread = threading.Thread(
                target=cli_module.run_daemon, args=(["8.8.8.8"], 0.2, []), daemon=True
            )
            thread.start()
            time.sleep(0.25)
            cli_module.db.register_active_target("1.1.1.1")

            deadline = time.time() + 1.5
            while not stop_event.is_set() and time.time() < deadline:
                time.sleep(0.01)

        assert "1.1.1.1" in call_log, \
            "a target registered mid-run must be picked up on a later tick without restarting the daemon"

    def test_slow_probe_does_not_produce_negative_or_zero_sleep(self, cli_module):
        """Validates floor sleep behavior, when probe runtime exceeds the scheduling interval."""
        cli_module.db.register_active_target("8.8.8.8")
        tick_timestamps = []

        def _slow_probe(target, *args, **kwargs):
            tick_timestamps.append(time.time())
            time.sleep(0.25)

        crashed = {"exc": None}

        def _run():
            try:
                with patch.object(cli_module, "probe_target", side_effect=_slow_probe):
                    cli_module.run_daemon(["8.8.8.8"], 0.1, [])
            except Exception as e:
                crashed["exc"] = e

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        time.sleep(0.9)

        assert crashed["exc"] is None, f"Daemon loop crashed during slow probes: {crashed['exc']}"
        assert len(tick_timestamps) >= 2, "Daemon loop starved, deadlocked, or failed to process multiple ticks."

        gaps = [tick_timestamps[i] - tick_timestamps[i - 1] for i in range(1, len(tick_timestamps))]
        for gap in gaps:
            assert gap >= 0.3, f"Daemon sleep tracking failed! Ticks fired too fast ({gap:.3f}s gap)"

    def test_one_targets_probe_exception_does_not_block_others_same_tick(self, cli_module):
        """Ensures single-target probe exceptions do not halt other target probes or crash the daemon loop."""
        cli_module.db.register_active_target("8.8.8.8")
        cli_module.db.register_active_target("1.1.1.1")

        call_log = []
        tick_count = {"ticks": 0}
        stop_event = threading.Event()

        def _maybe_raise(target, *args, **kwargs):
            call_log.append(target)
            if target == "8.8.8.8":
                raise RuntimeError("simulated probe crash")

        def _patched_probe_target(target, *args, **kwargs):
            if target == "1.1.1.1" and "8.8.8.8" in call_log:
                tick_count["ticks"] += 1
                if tick_count["ticks"] >= 2:
                    stop_event.set()
            _maybe_raise(target, *args, **kwargs)

        with patch.object(cli_module, "probe_target", side_effect=_patched_probe_target):
            thread = threading.Thread(
                target=cli_module.run_daemon, args=(["8.8.8.8", "1.1.1.1"], 0.1, []), daemon=True
            )
            thread.start()
            
            deadline = time.time() + 1.2
            while not stop_event.is_set() and time.time() < deadline:
                time.sleep(0.01)

        assert "1.1.1.1" in call_log, \
            "a crash in one target's probe_worker call must not prevent other targets from being probed"
        assert tick_count["ticks"] >= 2, (
            f"Daemon died after the first exception tick! Total ticks run: {tick_count['ticks']}."
        )

    def test_no_active_targets_does_not_error_and_still_sleeps(self, cli_module):
        crashed = {"exc": None}

        def _run():
            try:
                cli_module.run_daemon([], 0.1, [])
            except Exception as e:
                crashed["exc"] = e

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        time.sleep(0.5)
        assert crashed["exc"] is None