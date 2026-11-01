---
name: casting-quality-guardian
description: Audit and improve the Telegram casting monitor through bounded server-side diagnostics, approved-source visibility checks, casting match fixtures, notification delivery state and controlled GitHub/Railway release decisions. Use for bot health, matching regression, delivery and source troubleshooting.
---

# Casting Quality Guardian

1. Check Railway status, deployment commit and logs before interpreting missing castings. A source that exposes no Telegram posts is unavailable, not empty.
2. Use `app.quality_guardian.inspect(db)` for deterministic aggregate health snapshots. It must never access Telegram sessions, scan groups or send message texts to outside services.
3. Report enabled/healthy/unavailable/stale/unchecked sources, last confirmed success, archived candidates, sent/pending/failed/blocked delivery counts, and filter fixture results.
4. Run at most once per hour by default. Admin alerts require opt-in and persistent daily cooldown.
5. Audit regression fixtures for distinct roles, ages, genders, cities, expiry and training ads. Never infer unspecified role requirements.
6. Apply improvements only through a reviewed GitHub branch, tests and an explicit Railway release process. A Skill does not self-execute on a server.
7. Never implement self-updating code, silent account login, automated group joining, scraping beyond approved public sources, retries that ignore HTTP 429, or credentials in reports.
8. Require explicit permission and a separate security review for any account-session integration. The watchdog is useful independently and keeps that code disabled.
9. After every real deployment confirm its SHA, Railway status and runtime diagnostics. Clearly state what remains untested.
