---
name: casting-source-diagnostics
description: Diagnose Telegram casting source collection failures and distinguish inaccessible public previews from zero relevant auditions. Use for reviewing selected group collection, scan intervals, backoff, source freshness, and historical coverage.
---

# Source diagnostics

1. Start with Railway logs and the `public_state` SQLite summary; verify source approval, enabled status, username, last attempt, last success, and last error.
2. Confirm the declared 48-hour freshness window; do not claim full coverage when a preview provides fewer posts than published, when only the last 40/80 messages are fetched, or when content is private.
3. Respect minimum 15-minute scan bounds, normal 30-minute interval, 60-minute failure delay, Telegram 429/FLOOD_WAIT backoff and durable cursors. A manual button cannot force restricted sources.
4. Use only administrator-approved public web-visible sources. No account session, login QR, private group enumeration, hidden access or automatic joining.
5. Check whether each preserved publication has username, source message ID, timestamp, permalink, body and stable fingerprint.
6. Keep unavailable sources visible in Russian diagnostics. Do not produce a fake 'no casting' result.
7. For changes, add unit tests for missing HTML, empty pages, HTTP 429, duplicate reposts, truncated history and offline mode; ship through review, not auto-update.
