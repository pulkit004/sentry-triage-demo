"""
Regression tests for lib.db.pool.

Key scenarios covered
---------------------
1. Connections are always released after normal use (context manager).
2. Connections are released even when the caller raises an exception.
3. The pool never leaks: after N operations the idle count returns to max_size.
4. PoolExhaustedError is raised (not a hang) when the pool is exhausted.
5. Module-level get_connection() has the same leak-safe guarantee.
6. Manual acquire/release works correctly.
7. Health report reflects correct active/idle counts.
8. Releasing a broken connection does not corrupt pool size.
"""

import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from lib.db.pool import (
    ConnectionPool,
    PoolExhaustedError,
    _default_pool,
    get_connection,
    init_pool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal fake connection that tracks close() calls."""

    _id_counter = 0
    _id_lock = threading.Lock()

    def __init__(self) -> None:
        with _FakeConn._id_lock:
            _FakeConn._id_counter += 1
            self._id = _FakeConn._id_counter
        self.closed = 0
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        self.closed = 1

    def execute(self, sql: str) -> None:  # noqa: D401
        if self.closed:
            raise RuntimeError("Connection is closed")

    def __repr__(self) -> str:
        return f"<FakeConn id={self._id} closed={self.closed}>"


def _make_pool(max_size: int = 5, timeout: float = 1.0) -> ConnectionPool:
    return ConnectionPool(
        factory=_FakeConn,
        max_size=max_size,
        timeout=timeout,
        health_check_interval=0,  # disable background thread in tests
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestConnectionReleasedOnNormalExit(unittest.TestCase):
    """Connections must be returned to the pool after normal use."""

    def test_context_manager_releases_on_success(self):
        pool = _make_pool()
        self.assertEqual(pool.idle_count, 5)

        with pool.connection() as conn:
            self.assertIsNotNone(conn)
            self.assertEqual(pool.active_count, 1)
            self.assertEqual(pool.idle_count, 4)

        # After exiting the context the connection must be back
        self.assertEqual(pool.active_count, 0)
        self.assertEqual(pool.idle_count, 5)

    def test_repeated_use_does_not_shrink_pool(self):
        pool = _make_pool()
        for _ in range(20):
            with pool.connection() as conn:
                conn.execute("SELECT 1")

        self.assertEqual(pool.idle_count, 5)
        self.assertEqual(pool.active_count, 0)


class TestConnectionReleasedOnException(unittest.TestCase):
    """Connections must be returned even when the caller raises."""

    def test_context_manager_releases_on_exception(self):
        pool = _make_pool()

        with self.assertRaises(ValueError):
            with pool.connection() as conn:  # noqa: F841
                raise ValueError("simulated error")

        # Pool must be fully replenished despite the exception
        self.assertEqual(pool.active_count, 0)
        self.assertEqual(pool.idle_count, 5)

    def test_context_manager_releases_on_runtime_error(self):
        pool = _make_pool()

        with self.assertRaises(RuntimeError):
            with pool.connection():
                raise RuntimeError("DB query failed")

        self.assertEqual(pool.active_count, 0)
        self.assertEqual(pool.idle_count, 5)

    def test_multiple_sequential_exceptions_no_leak(self):
        pool = _make_pool(max_size=3)

        for _ in range(9):  # 3x pool size
            try:
                with pool.connection():
                    raise Exception("boom")
            except Exception:  # noqa: BLE001
                pass

        self.assertEqual(pool.active_count, 0)
        self.assertEqual(pool.idle_count, 3)


class TestPoolNeverLeaks(unittest.TestCase):
    """After any sequence of operations the idle count must equal max_size."""

    def test_concurrent_access_no_leak(self):
        max_size = 5
        pool = _make_pool(max_size=max_size, timeout=5.0)
        errors: list = []

        def worker():
            try:
                with pool.connection() as conn:
                    time.sleep(0.01)  # simulate work
                    conn.execute("SELECT 1")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertFalse(errors, f"Worker errors: {errors}")
        self.assertEqual(pool.idle_count, max_size)
        self.assertEqual(pool.active_count, 0)

    def test_concurrent_access_with_exceptions_no_leak(self):
        max_size = 4
        pool = _make_pool(max_size=max_size, timeout=5.0)

        def failing_worker():
            try:
                with pool.connection():
                    time.sleep(0.005)
                    raise ValueError("intentional")
            except ValueError:
                pass

        threads = [threading.Thread(target=failing_worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(pool.idle_count, max_size)
        self.assertEqual(pool.active_count, 0)


class TestPoolExhaustion(unittest.TestCase):
    """PoolExhaustedError must be raised rather than blocking forever."""

    def test_exhausted_pool_raises_error(self):
        pool = _make_pool(max_size=2, timeout=0.2)

        # Check out all connections without releasing
        conn1 = pool.acquire()
        conn2 = pool.acquire()

        with self.assertRaises(PoolExhaustedError) as ctx:
            pool.acquire()  # should fail quickly

        self.assertIn("Pool exhausted", str(ctx.exception))

        # Clean up
        pool.release(conn1)
        pool.release(conn2)

    def test_exhausted_error_message_contains_counts(self):
        pool = _make_pool(max_size=1, timeout=0.1)
        conn = pool.acquire()

        try:
            with self.assertRaises(PoolExhaustedError) as ctx:
                pool.acquire()
            msg = str(ctx.exception)
            self.assertIn("1/1", msg)
        finally:
            pool.release(conn)


class TestModuleLevelGetConnection(unittest.TestCase):
    """Module-level get_connection() must have the same leak-safe guarantee."""

    def setUp(self):
        init_pool(factory=_FakeConn, max_size=5, timeout=1.0, health_check_interval=0)

    def tearDown(self):
        from lib.db import pool as pool_module
        if pool_module._default_pool is not None:
            pool_module._default_pool.close_all()

    def test_get_connection_releases_on_success(self):
        from lib.db import pool as pool_module
        p = pool_module._default_pool

        with get_connection() as conn:
            self.assertIsNotNone(conn)
            self.assertEqual(p.active_count, 1)

        self.assertEqual(p.active_count, 0)
        self.assertEqual(p.idle_count, 5)

    def test_get_connection_releases_on_exception(self):
        from lib.db import pool as pool_module
        p = pool_module._default_pool

        with self.assertRaises(RuntimeError):
            with get_connection():
                raise RuntimeError("query failed")

        self.assertEqual(p.active_count, 0)
        self.assertEqual(p.idle_count, 5)

    def test_get_connection_no_pool_raises_runtime_error(self):
        from lib.db import pool as pool_module
        # Temporarily clear the default pool
        original = pool_module._default_pool
        pool_module._default_pool = None
        try:
            with self.assertRaises(RuntimeError):
                with get_connection():
                    pass
        finally:
            pool_module._default_pool = original


class TestManualAcquireRelease(unittest.TestCase):
    """Manual acquire/release must keep pool consistent."""

    def test_acquire_and_release_restores_idle_count(self):
        pool = _make_pool(max_size=3)
        conn = pool.acquire()
        self.assertEqual(pool.active_count, 1)
        pool.release(conn)
        self.assertEqual(pool.active_count, 0)
        self.assertEqual(pool.idle_count, 3)

    def test_acquire_all_then_release_all(self):
        pool = _make_pool(max_size=3)
        conns = [pool.acquire() for _ in range(3)]
        self.assertEqual(pool.active_count, 3)
        self.assertEqual(pool.idle_count, 0)
        for c in conns:
            pool.release(c)
        self.assertEqual(pool.active_count, 0)
        self.assertEqual(pool.idle_count, 3)


class TestHealthReport(unittest.TestCase):
    """Health report must reflect real-time pool state."""

    def test_health_report_idle_pool(self):
        pool = _make_pool(max_size=4)
        report = pool.health_report()
        self.assertEqual(report["active"], 0)
        self.assertEqual(report["idle"], 4)
        self.assertEqual(report["max_size"], 4)
        self.assertEqual(report["utilization_pct"], 0.0)
        self.assertEqual(report["exhausted_for_seconds"], 0.0)

    def test_health_report_under_load(self):
        pool = _make_pool(max_size=4)
        conn1 = pool.acquire()
        conn2 = pool.acquire()

        report = pool.health_report()
        self.assertEqual(report["active"], 2)
        self.assertEqual(report["idle"], 2)
        self.assertEqual(report["utilization_pct"], 50.0)

        pool.release(conn1)
        pool.release(conn2)

    def test_health_report_exhausted_for_seconds_increases(self):
        pool = _make_pool(max_size=1, timeout=5.0)
        conn = pool.acquire()

        # Manually set exhausted_since to simulate a long exhaustion
        import time as _time
        pool._exhausted_since = _time.monotonic() - 10.0

        report = pool.health_report()
        self.assertGreaterEqual(report["exhausted_for_seconds"], 9.9)

        pool.release(conn)


class TestBrokenConnectionHandling(unittest.TestCase):
    """Releasing a broken connection must not corrupt pool size."""

    def test_broken_connection_does_not_leak_slot(self):
        pool = _make_pool(max_size=3)
        conn = pool.acquire()
        # Simulate a broken connection
        conn.closed = 1
        pool.release(conn)

        # Pool should still have 3 slots total
        self.assertEqual(pool.idle_count, 3)
        self.assertEqual(pool.active_count, 0)

    def test_broken_connection_is_replaced_on_next_acquire(self):
        pool = _make_pool(max_size=3)
        conn = pool.acquire()
        conn.closed = 1
        pool.release(conn)

        # Next acquire should produce a healthy connection
        new_conn = pool.acquire()
        self.assertEqual(new_conn.closed, 0)
        pool.release(new_conn)


class TestCloseAll(unittest.TestCase):
    def test_close_all_closes_idle_connections(self):
        pool = _make_pool(max_size=3)
        # Trigger lazy creation by acquiring and releasing
        for _ in range(3):
            with pool.connection():
                pass
        pool.close_all()
        self.assertEqual(pool.idle_count, 0)


if __name__ == "__main__":
    unittest.main()
