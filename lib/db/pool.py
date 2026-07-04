"""
Database connection pool with leak-safe connection management.

Usage (recommended - context manager):
    with get_connection() as conn:
        conn.execute(...)

Usage (manual - legacy):
    conn = pool.acquire()
    try:
        conn.execute(...)
    finally:
        pool.release(conn)
"""

import logging
import threading
import time
from contextlib import contextmanager
from queue import Empty, Full, LifoQueue
from typing import Any, Callable, Generator, Optional

logger = logging.getLogger(__name__)

# Sentinel used to detect that a slot was never filled
_EMPTY = object()


class PoolExhaustedError(Exception):
    """Raised when a connection cannot be acquired within the timeout."""


class ConnectionPool:
    """
    A thread-safe, bounded connection pool.

    Parameters
    ----------
    factory:
        Callable that creates and returns a new raw connection.
    max_size:
        Maximum number of connections the pool will hold.
    timeout:
        Seconds to wait for a free connection before raising PoolExhaustedError.
    max_idle:
        Seconds a connection can sit idle before being closed and replaced.
    health_check_interval:
        Seconds between background health/leak-detection scans (0 = disabled).
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        max_size: int = 10,
        timeout: float = 30.0,
        max_idle: float = 300.0,
        health_check_interval: float = 60.0,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")

        self._factory = factory
        self._max_size = max_size
        self._timeout = timeout
        self._max_idle = max_idle
        self._health_check_interval = health_check_interval

        # Available connections (wrapped in _Slot so we can track idle time)
        self._pool: LifoQueue = LifoQueue(maxsize=max_size)

        # Tracks the total number of connections that exist (in-use + idle)
        self._lock = threading.Lock()
        self._total_created: int = 0
        self._active_count: int = 0

        # Timestamp of the last time the pool was fully exhausted
        self._exhausted_since: Optional[float] = None
        self._exhausted_lock = threading.Lock()

        # Pre-fill the pool
        for _ in range(max_size):
            slot = self._make_slot()
            self._pool.put_nowait(slot)

        # Background health-check thread
        if health_check_interval > 0:
            self._stop_monitor = threading.Event()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="ConnectionPool-monitor",
                daemon=True,
            )
            self._monitor_thread.start()
        else:
            self._stop_monitor = None  # type: ignore[assignment]
            self._monitor_thread = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_slot(self) -> "_Slot":
        with self._lock:
            self._total_created += 1
        return _Slot(connection=_EMPTY, created_at=0.0, last_used_at=0.0)

    def _open_connection(self) -> Any:
        """Create a new raw connection via the factory."""
        try:
            conn = self._factory()
            logger.debug("ConnectionPool: opened new connection %s", id(conn))
            return conn
        except Exception as exc:
            logger.error("ConnectionPool: factory raised %s", exc)
            raise

    def _close_connection(self, conn: Any) -> None:
        """Best-effort close of a raw connection."""
        if conn is _EMPTY:
            return
        try:
            conn.close()
            logger.debug("ConnectionPool: closed connection %s", id(conn))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ConnectionPool: error closing connection: %s", exc)

    def _is_healthy(self, conn: Any) -> bool:
        """Return False if the connection appears broken."""
        if conn is _EMPTY:
            return False
        # If the connection exposes a standard 'closed' attribute (psycopg2, etc.)
        if getattr(conn, "closed", 0):
            return False
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> Any:
        """
        Acquire a connection from the pool.

        Blocks up to *timeout* seconds.  Raises PoolExhaustedError if no
        connection becomes available in time.

        IMPORTANT: callers **must** pair every acquire() with a release().
        Prefer using the :meth:`connection` context manager instead.
        """
        deadline = time.monotonic() + self._timeout
        remaining = self._timeout

        while remaining > 0:
            try:
                slot: _Slot = self._pool.get(timeout=min(remaining, 1.0))
            except Empty:
                remaining = deadline - time.monotonic()
                with self._exhausted_lock:
                    if self._exhausted_since is None:
                        self._exhausted_since = time.monotonic()
                        logger.warning(
                            "ConnectionPool: pool fully exhausted "
                            "(%d/%d active). Waiting for a free connection.",
                            self._active_count,
                            self._max_size,
                        )
                continue

            with self._exhausted_lock:
                self._exhausted_since = None

            # Ensure the slot holds a live connection
            if not self._is_healthy(slot.connection):
                self._close_connection(slot.connection)
                slot.connection = self._open_connection()
                slot.created_at = time.monotonic()

            slot.last_used_at = time.monotonic()
            with self._lock:
                self._active_count += 1

            logger.debug(
                "ConnectionPool: acquired connection %s (%d/%d active)",
                id(slot.connection),
                self._active_count,
                self._max_size,
            )
            return slot.connection

        raise PoolExhaustedError(
            f"Could not acquire connection within {self._timeout}s. "
            f"Pool exhausted: {self._active_count}/{self._max_size} active."
        )

    def release(self, conn: Any) -> None:
        """
        Return a connection to the pool.

        If the connection is broken it will be discarded and replaced lazily
        on the next acquire().
        """
        with self._lock:
            self._active_count = max(0, self._active_count - 1)

        if not self._is_healthy(conn):
            logger.warning(
                "ConnectionPool: releasing broken connection %s; it will be "
                "replaced on next acquire.",
                id(conn),
            )
            # Put back an empty slot so pool size stays consistent
            slot = _Slot(connection=_EMPTY, created_at=0.0, last_used_at=time.monotonic())
        else:
            slot = _Slot(
                connection=conn,
                created_at=0.0,
                last_used_at=time.monotonic(),
            )

        try:
            self._pool.put_nowait(slot)
        except Full:
            # Should never happen in normal usage, but close the connection
            # rather than leak it if it does.
            logger.error(
                "ConnectionPool: pool is already full when releasing %s; "
                "closing connection to prevent leak.",
                id(conn),
            )
            self._close_connection(conn)

        logger.debug(
            "ConnectionPool: released connection %s (%d/%d active)",
            id(conn),
            self._active_count,
            self._max_size,
        )

    @contextmanager
    def connection(self) -> Generator[Any, None, None]:
        """
        Context manager that acquires a connection and **guarantees** it is
        released back to the pool, even if an exception is raised.

        Example::

            with pool.connection() as conn:
                conn.execute("SELECT 1")
        """
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    # ------------------------------------------------------------------
    # Health / monitoring
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """Number of connections currently checked out."""
        with self._lock:
            return self._active_count

    @property
    def idle_count(self) -> int:
        """Number of connections currently sitting idle in the pool."""
        return self._pool.qsize()

    @property
    def max_size(self) -> int:
        return self._max_size

    def health_report(self) -> dict:
        """
        Return a snapshot of pool health metrics.

        Returns
        -------
        dict with keys:
            active   - connections currently in use
            idle     - connections available in the pool
            max_size - pool capacity
            utilization_pct - percentage of pool in use
            exhausted_for_seconds - seconds the pool has been fully exhausted
                                    (0.0 if not currently exhausted)
        """
        with self._exhausted_lock:
            exhausted_since = self._exhausted_since

        exhausted_for = (
            time.monotonic() - exhausted_since
            if exhausted_since is not None
            else 0.0
        )
        active = self.active_count
        return {
            "active": active,
            "idle": self.idle_count,
            "max_size": self._max_size,
            "utilization_pct": round(active / self._max_size * 100, 1),
            "exhausted_for_seconds": round(exhausted_for, 2),
        }

    def _monitor_loop(self) -> None:
        """Background thread: periodically log health and detect leaks."""
        while not self._stop_monitor.wait(timeout=self._health_check_interval):
            report = self.health_report()
            level = logging.DEBUG
            if report["utilization_pct"] >= 90:
                level = logging.WARNING
            if report["exhausted_for_seconds"] > self._health_check_interval:
                level = logging.ERROR
                logger.log(
                    level,
                    "ConnectionPool LEAK DETECTED: pool has been fully "
                    "exhausted for %.1f seconds. active=%d max=%d",
                    report["exhausted_for_seconds"],
                    report["active"],
                    report["max_size"],
                )
            else:
                logger.log(
                    level,
                    "ConnectionPool health: active=%d idle=%d max=%d "
                    "utilization=%.1f%% exhausted_for=%.1fs",
                    report["active"],
                    report["idle"],
                    report["max_size"],
                    report["utilization_pct"],
                    report["exhausted_for_seconds"],
                )

    def close_all(self) -> None:
        """
        Drain and close every idle connection.  Active connections are not
        affected but will encounter errors on next use.
        """
        if self._stop_monitor is not None:
            self._stop_monitor.set()

        while True:
            try:
                slot = self._pool.get_nowait()
                self._close_connection(slot.connection)
            except Empty:
                break

        logger.info("ConnectionPool: all idle connections closed.")

    def __del__(self) -> None:
        try:
            self.close_all()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Simple value-object to carry per-connection metadata inside the pool queue
# ---------------------------------------------------------------------------

class _Slot:
    __slots__ = ("connection", "created_at", "last_used_at")

    def __init__(self, connection: Any, created_at: float, last_used_at: float) -> None:
        self.connection = connection
        self.created_at = created_at
        self.last_used_at = last_used_at


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_default_pool: Optional[ConnectionPool] = None
_default_pool_lock = threading.Lock()


def init_pool(
    factory: Callable[[], Any],
    max_size: int = 10,
    timeout: float = 30.0,
    max_idle: float = 300.0,
    health_check_interval: float = 60.0,
) -> ConnectionPool:
    """
    Initialise (or replace) the module-level default pool.

    Call this once at application startup before any call to
    :func:`get_connection`.
    """
    global _default_pool  # noqa: PLW0603
    with _default_pool_lock:
        if _default_pool is not None:
            _default_pool.close_all()
        _default_pool = ConnectionPool(
            factory=factory,
            max_size=max_size,
            timeout=timeout,
            max_idle=max_idle,
            health_check_interval=health_check_interval,
        )
    return _default_pool


def get_pool() -> ConnectionPool:
    """Return the module-level default pool, raising if not initialised."""
    if _default_pool is None:
        raise RuntimeError(
            "No connection pool has been initialised. "
            "Call lib.db.pool.init_pool() before get_connection()."
        )
    return _default_pool


@contextmanager
def get_connection() -> Generator[Any, None, None]:
    """
    Module-level context manager that acquires a connection from the default
    pool and **guarantees** it is released back even when an exception occurs.

    This is the primary public API for application code.

    Example::

        from lib.db.pool import get_connection

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")

    Raises
    ------
    PoolExhaustedError
        If no connection becomes available within the configured timeout.
    RuntimeError
        If the pool has not been initialised via :func:`init_pool`.
    """
    pool = get_pool()
    conn = pool.acquire()          # can raise PoolExhaustedError
    try:
        yield conn
    finally:
        # Guaranteed to run even if the caller raises an exception,
        # preventing connection leaks that exhaust the pool.
        pool.release(conn)
