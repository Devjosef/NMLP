"""Tests for api.py's background loops (_target_worker and _startup_scan_loop)."""

import asyncio
from unittest.mock import patch
import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fast_api_module(api_module):
    """Shrinks PROBE_INTERVAL and accelerates shutdown event wait for fast loop execution."""
    api_module._shutdown_event.clear()

    async def _safe_wait():
        while not api_module._shutdown_event.is_set():
            await asyncio.sleep(0.01)
        return True

    with patch.object(api_module, "PROBE_INTERVAL", 0.1), \
         patch.object(api_module._shutdown_event, "wait", new=_safe_wait):
        yield api_module


class TestTargetWorkerLoop:
    async def test_worker_probes_repeatedly_on_interval(self, fast_api_module):
        api = fast_api_module
        call_log = []

        def _tracking_probe(target):
            call_log.append(target)

        with patch.object(api, "_probe_and_log_sync", side_effect=_tracking_probe):
            task = asyncio.create_task(api._target_worker("8.8.8.8"))
            await asyncio.sleep(0.5)
            api._shutdown_event.set()
            await asyncio.wait_for(task, timeout=1.0)

        assert len(call_log) >= 3, f"expected multiple ticks, got {len(call_log)}"
        api._shutdown_event.clear()

    async def test_worker_stops_promptly_on_shutdown_not_after_full_sleep(self, fast_api_module):
        """Verifies shutdown exits immediately without waiting out the remaining interval sleep cycle."""
        api = fast_api_module
        with patch.object(api, "PROBE_INTERVAL", 10.0), \
             patch.object(api, "_probe_and_log_sync", return_value=None):
            task = asyncio.create_task(api._target_worker("8.8.8.8"))
            await asyncio.sleep(0.05)

            start = asyncio.get_event_loop().time()
            api._shutdown_event.set()
            await asyncio.wait_for(task, timeout=1.0)
            elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0, \
            f"shutdown took {elapsed:.2f}s -- must not wait out the full 10s interval"
        api._shutdown_event.clear()


class TestStartupScanLoop:
    async def test_new_active_target_gets_a_worker_without_restart(self, fast_api_module):
        api = fast_api_module
        
        async def _mock_worker(target):
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                pass

        original_wait_for = asyncio.wait_for

        async def _fast_wait_for(fut, timeout):
            if timeout == 5.0:
                return await original_wait_for(fut, timeout=0.01)
            return await original_wait_for(fut, timeout=timeout)

        with patch.object(api, "_target_worker", side_effect=_mock_worker), \
             patch("asyncio.wait_for", new=_fast_wait_for):
            scan_task = asyncio.create_task(api._startup_scan_loop())
            await asyncio.sleep(0.05)
            assert "8.8.8.8" not in api._worker_tasks

            api.db.register_active_target("8.8.8.8")
            await asyncio.sleep(0.1)

            api._shutdown_event.set()
            await original_wait_for(scan_task, timeout=1.0)

        assert "8.8.8.8" in api._worker_tasks
        api._shutdown_event.clear()

    async def test_orphaned_open_incident_gets_resolved_by_the_scan_loop_itself(self, fast_api_module):
        """Verifies background sweep automatically resolves orphaned open incidents without explicit HTTP trigger."""
        api = fast_api_module
        inc_id = api.db.log_incident("192.0.2.1", {"summary": "ghost", "bottleneck": {}, "hops": []}, source="api")

        original_wait_for = asyncio.wait_for

        async def _fast_wait_for(fut, timeout):
            if timeout == 5.0:
                return await original_wait_for(fut, timeout=0.01)
            return await original_wait_for(fut, timeout=timeout)

        with patch.object(api, "_target_worker", return_value=None), \
             patch("asyncio.wait_for", new=_fast_wait_for):
            scan_task = asyncio.create_task(api._startup_scan_loop())
            await asyncio.sleep(0.05)
            api._shutdown_event.set()
            await original_wait_for(scan_task, timeout=1.0)

        row = api.db.get_incident(inc_id)
        assert row[8] == 1, "orphaned incident must be auto-resolved by the scan loop's own sweep"
        api._shutdown_event.clear()

    async def test_scan_loop_survives_a_db_error_on_one_iteration(self, fast_api_module):
        api = fast_api_module
        call_count = {"n": 0}
        original_get_active = api.db.get_active_targets

        def _flaky_get_active():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated transient DB error")
            return original_get_active()

        original_wait_for = asyncio.wait_for

        async def _fast_wait_for(fut, timeout):
            if timeout == 5.0:
                return await original_wait_for(fut, timeout=0.01)
            return await original_wait_for(fut, timeout=timeout)

        with patch.object(api.db, "get_active_targets", side_effect=_flaky_get_active), \
             patch.object(api, "_target_worker", return_value=None), \
             patch("asyncio.wait_for", new=_fast_wait_for):
            scan_task = asyncio.create_task(api._startup_scan_loop())
            await asyncio.sleep(0.05)
            api._shutdown_event.set()
            await original_wait_for(scan_task, timeout=1.0)

        assert call_count["n"] >= 1, "loop must have attempted at least once despite the injected error"
        api._shutdown_event.clear()


class TestCancellationSafety:
    async def test_cancelling_worker_mid_probe_does_not_leave_target_stuck_in_flight(self, fast_api_module):
        api = fast_api_module

        def _slow_probe(target):
            import time as _t
            try:
                _t.sleep(0.3)
            finally:
                pass

        with patch.object(api, "_probe_and_log_sync", side_effect=_slow_probe):
            task = asyncio.create_task(api._target_worker("8.8.8.8"))
            await asyncio.sleep(0.05)
            
            task.cancel()
            
            with pytest.raises(asyncio.CancelledError):
                await task

            await asyncio.sleep(0.35)

        assert "8.8.8.8" not in api._in_flight, \
            "Target remained stuck in flight because the background thread was orphaned or failed to clean up."