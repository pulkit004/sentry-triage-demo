"""
Trigger all 5 demo bugs to generate real Sentry events.

Usage:
    SENTRY_DSN=https://xxx@o123.ingest.sentry.io/456 python scripts/trigger_errors.py

This fires errors that create unresolved issues in Sentry with:
- Bugs 1-3: full stacktraces (rich context)
- Bugs 4-5: minimal context (sparse)
"""

import os
import sys
import time

import sentry_sdk

DSN = os.environ.get("SENTRY_DSN")
if not DSN:
    print("ERROR: Set SENTRY_DSN environment variable")
    print("  export SENTRY_DSN=https://xxx@o123.ingest.sentry.io/456")
    sys.exit(1)

sentry_sdk.init(
    dsn=DSN,
    environment="production",
    traces_sample_rate=1.0,
    release="sentry-triage-demo@1.0.0",
)


def trigger_bug_1_null_reference():
    """BUG 1: TypeError - null reference in UserProfile (HIGH, rich context)"""
    print("[1/5] Triggering null reference bug...")

    class FakeUser:
        name = None
        email = "user@example.com"
        joinedAt = "2024-01-15"

    try:
        user = FakeUser()
        # Simulates: user.name.split(' ')[0] in app/components/UserProfile.jsx
        # Python equivalent to trigger the same error shape
        first_name = user.name.split(" ")[0]
    except (TypeError, AttributeError) as e:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("component", "UserProfile")
            scope.set_context("user_data", {
                "user_id": "usr_12345",
                "has_name": False,
                "email": "user@example.com",
            })
            scope.set_extra("function", "renderHeader")
            scope.set_extra("file", "app/components/UserProfile.jsx")
            scope.set_extra("line", 45)
            sentry_sdk.capture_exception(e)
    print("  -> Captured: TypeError in UserProfile.renderHeader")


def trigger_bug_2_unhandled_async():
    """BUG 2: UnhandledPromiseRejection in orders handler (CRITICAL, rich context)"""
    print("[2/5] Triggering unhandled async bug...")

    import requests

    try:
        # Simulates: axios.post to unreachable payment service
        response = requests.post(
            "http://localhost:99999/charge",
            json={"customerId": "cust_456", "amount": 99.99},
            timeout=0.1,
        )
    except Exception as e:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("handler", "createOrder")
            scope.set_tag("service", "payment-service")
            scope.level = "fatal"
            scope.set_context("request", {
                "method": "POST",
                "url": "http://payments-service:3001/charge",
                "body": {"customerId": "cust_456", "amount": 99.99},
            })
            scope.set_extra("function", "createOrder")
            scope.set_extra("file", "app/api/handlers/orders.ts")
            scope.set_extra("line", 28)
            exc = ConnectionError(
                "UnhandledPromiseRejection: connect ECONNREFUSED 127.0.0.1:3001 - "
                "Payment service unavailable in app/api/handlers/orders.ts"
            )
            sentry_sdk.capture_exception(exc)
    print("  -> Captured: UnhandledPromiseRejection in orders.createOrder")


def trigger_bug_3_pool_exhaustion():
    """BUG 3: Connection pool exhaustion (HIGH, rich context, should escalate)"""
    print("[3/5] Triggering pool exhaustion bug...")

    try:
        # Simulates: pool.acquire_connection() timing out
        raise TimeoutError(
            "Could not acquire connection within 30.0s. "
            "Pool exhausted: 10/10 active. "
            "Connections leaked due to missing finally block in get_connection()"
        )
    except TimeoutError as e:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("component", "ConnectionPool")
            scope.set_tag("category", "infrastructure")
            scope.level = "error"
            scope.set_context("pool_stats", {
                "active": 10,
                "idle": 0,
                "max": 10,
                "total_created": 847,
                "total_acquired": 12453,
                "utilization": 1.0,
            })
            scope.set_extra("function", "acquire_connection")
            scope.set_extra("file", "lib/db/pool.py")
            scope.set_extra("line", 112)
            sentry_sdk.capture_exception(e)
    print("  -> Captured: TimeoutError in ConnectionPool.acquire_connection")


def trigger_bug_4_type_error():
    """BUG 4: Type coercion error in parser (MEDIUM, sparse context)"""
    print("[4/5] Triggering type error bug (sparse context)...")

    # Sparse context: capture as message with minimal info, no full stacktrace
    sentry_sdk.capture_message(
        "TypeError: Cannot convert undefined to object - "
        "transformRecords failed processing webhook payload in src/utils/parser.js",
        level="error",
    )
    print("  -> Captured: TypeError message (no stacktrace)")


def trigger_bug_5_deprecated_api():
    """BUG 5: Deprecated API warning in logger (LOW, sparse context)"""
    print("[5/5] Triggering deprecated API warning (sparse context)...")

    # Sparse context: just a warning message
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("category", "deprecation")
        scope.level = "warning"
        sentry_sdk.capture_message(
            "DeprecationWarning: console.warn.bind() pattern is deprecated in Node.js >=20. "
            "Use structured logging API instead. Triggered in src/config/logger.ts",
            level="warning",
        )
    print("  -> Captured: DeprecationWarning message (no stacktrace)")


def main():
    print("=" * 60)
    print("Sentry Triage Demo - Error Trigger Script")
    print(f"DSN: {DSN[:40]}...")
    print("=" * 60)
    print()

    trigger_bug_1_null_reference()
    time.sleep(0.5)

    trigger_bug_2_unhandled_async()
    time.sleep(0.5)

    trigger_bug_3_pool_exhaustion()
    time.sleep(0.5)

    trigger_bug_4_type_error()
    time.sleep(0.5)

    trigger_bug_5_deprecated_api()

    print()
    print("Flushing events to Sentry...")
    sentry_sdk.flush(timeout=10)
    print()
    print("Done! Check your Sentry dashboard for 5 new issues:")
    print("  - 3 with full stacktraces (bugs 1-3)")
    print("  - 2 with sparse context (bugs 4-5)")
    print()
    print("Run this script multiple times to increase event counts.")


if __name__ == "__main__":
    main()
