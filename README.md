# Sentry Triage Demo App

Demo application with intentional bugs for testing the [sentry-triage](https://github.com/pulkitverma/sentry-triage) autonomous remediation agent.

## Bugs

| # | File | Type | Severity | Context |
|---|------|------|----------|---------|
| 1 | `app/components/UserProfile.jsx` | Null reference | HIGH | Full stacktrace |
| 2 | `app/api/handlers/orders.ts` | Unhandled async | CRITICAL | Full stacktrace |
| 3 | `lib/db/pool.py` | Pool exhaustion | HIGH | Full stacktrace (escalates) |
| 4 | `src/utils/parser.js` | Type coercion | MEDIUM | Sparse (message only) |
| 5 | `src/config/logger.ts` | Deprecated API | LOW | Sparse (warning only) |

## Setup

```bash
pip install sentry-sdk requests
export SENTRY_DSN=https://xxx@o123.ingest.sentry.io/456
python scripts/trigger_errors.py
```

## Purpose

This repo exists so the sentry-triage agent can:
1. Fetch issues from Sentry (matching file paths as `culprit`)
2. Search this repo's code for context
3. Generate fix plans and open PRs
