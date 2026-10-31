---
name: casting-monitor-engineer
description: Review and maintain the telegram-casting-monitor repository, including Telethon source selection, incremental cursors, authorization failures, Railway deployment, monitoring safety, and regression tests. Use when improving or debugging the casting Telegram monitor.
---

# Casting monitor engineer

1. Read `app/main.py`, `app/bot.py`, `app/store.py`, `app/client.py`, `app/sources.py`, configuration and tests before changing scanner behavior.
2. Never enable collection from all account dialogs. Selected sources are an allowlist and an empty list means zero reads.
3. Never impersonate an official Telegram app or substitute shared API credentials. Never bypass Telegram restrictions, retry FloodWait aggressively, or spawn multiple clients for the same session.
4. Honor `SCAN_ENABLED=false` as the default. Make network work opt-in and bounded.
5. Keep per-source cursors durable, advanced only after a message is processed; if notification fails, retain the cursor for reprocessing.
6. Use low-rate sequential reads. Stop scanning when authorization or rate-limit errors occur. Log error types, not phone numbers, API keys, or session strings.
7. Run unittest and Python compilation before recommending deployment. Document test results, commit SHA, and any unverified runtime behavior.
8. AI may only review candidate announcement text. Never grant it Telegram credentials, Telegram actions, or authority to rewrite scanning constraints.
