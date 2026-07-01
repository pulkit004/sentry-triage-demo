"""
Database connection pool manager.

Provides a simple connection pooling layer for PostgreSQL connections
used across the application's data access layer.
"""

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg2


@dataclass
class PoolConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "app_db"
    user: str = "app_user"
    password: str = ""
    min_connections: int = 2
    max_connections: int = 10
    acquire_timeout: float = 30.0
    idle_timeout: float = 300.0
    max_lifetime: float = 3600.0


@dataclass
class PooledConnection:
    connection: object
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    in_use: bool = False


class ConnectionPool:
    """
    Thread-safe connection pool for PostgreSQL.

    Manages a pool of reusable database connections with configurable
    limits, timeouts, and health checking.
    """

    def __init__(self, config: PoolConfig):
        self.config = config
        self._pool: deque[PooledConnection] = deque()
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(config.max_connections)
        self._active_count = 0
        self._total_created = 0
        self._total_acquired = 0

    def _create_connection(self):
        """Create a new raw database connection."""
        conn = psycopg2.connect(
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            user=self.config.user,
            password=self.config.password,
        )
        conn.autocommit = False
        self._total_created += 1
        return conn

    def _is_connection_valid(self, pooled: PooledConnection) -> bool:
        """Check if a pooled connection is still usable."""
        now = time.time()
        if now - pooled.created_at > self.config.max_lifetime:
            return False
        if now - pooled.last_used > self.config.idle_timeout:
            return False
        try:
            cursor = pooled.connection.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return True
        except Exception:
            return False

    def acquire_connection(self):
        """
        Acquire a connection from the pool.

        Returns a raw database connection. The caller is responsible
        for returning it via release_connection().
        """
        acquired = self._semaphore.acquire(timeout=self.config.acquire_timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire connection within {self.config.acquire_timeout}s. "
                f"Pool exhausted: {self._active_count}/{self.config.max_connections} active."
            )

        with self._lock:
            # Try to reuse an existing idle connection
            while self._pool:
                pooled = self._pool.popleft()
                if self._is_connection_valid(pooled):
                    pooled.in_use = True
                    pooled.last_used = time.time()
                    self._active_count += 1
                    self._total_acquired += 1
                    return pooled.connection
                else:
                    # Discard invalid connection
                    try:
                        pooled.connection.close()
                    except Exception:
                        pass

            # Create new connection
            conn = self._create_connection()
            self._active_count += 1
            self._total_acquired += 1
            return conn

    def release_connection(self, connection):
        """Return a connection to the pool for reuse."""
        with self._lock:
            self._active_count -= 1
            try:
                connection.rollback()
                pooled = PooledConnection(connection=connection)
                self._pool.append(pooled)
            except Exception:
                try:
                    connection.close()
                except Exception:
                    pass
            finally:
                self._semaphore.release()

    @contextmanager
    def get_connection(self):
        """
        Context manager for acquiring a pooled connection.

        BUG: If an exception occurs between acquire and the yield,
        or if the caller's code raises an exception, the connection
        is never released back to the pool. Under sustained load with
        intermittent errors, this exhausts the pool completely.
        """
        conn = self.acquire_connection()
        # BUG: connection is acquired but if the code using it throws,
        # release_connection() is never called because there's no
        # try/finally wrapping the yield.
        yield conn
        # This line only executes if no exception was raised
        self.release_connection(conn)

    def get_stats(self) -> dict:
        """Return pool statistics for monitoring."""
        with self._lock:
            return {
                "active": self._active_count,
                "idle": len(self._pool),
                "max": self.config.max_connections,
                "total_created": self._total_created,
                "total_acquired": self._total_acquired,
                "utilization": self._active_count / self.config.max_connections,
            }

    def close_all(self):
        """Shutdown the pool and close all connections."""
        with self._lock:
            while self._pool:
                pooled = self._pool.popleft()
                try:
                    pooled.connection.close()
                except Exception:
                    pass
            self._active_count = 0


# Global pool instance
_pool: ConnectionPool | None = None


def get_pool(config: PoolConfig | None = None) -> ConnectionPool:
    """Get or create the global connection pool."""
    global _pool
    if _pool is None:
        if config is None:
            config = PoolConfig()
        _pool = ConnectionPool(config)
    return _pool


def execute_query(query: str, params: tuple = None):
    """Execute a query using the global pool."""
    pool = get_pool()
    with pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if cursor.description:
            results = cursor.fetchall()
            cursor.close()
            conn.commit()
            return results
        conn.commit()
        cursor.close()
        return None
