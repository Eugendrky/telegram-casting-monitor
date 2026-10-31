---
name: casting-autopilot
description: Guide development and validation of a Telegram casting monitor that fetches explicitly approved public web-visible channel posts, matches them against actors' profiles, delivers matching casting announcements once per user and keeps diagnostics. Use when working on casting extraction, relevance filtering, source scanning, retries, or feedback-driven quality checks.
---

# Casting autopilot

1. Fetch only administrator-approved, publicly viewable Telegram sources. Do not assume third-party private chats or public groups with inaccessible previews can be read.
2. Poll sequentially with backoff; provide a rate-limited manual-scan button. Never bypass Telegram restrictions or source cooldowns.
3. Store the source, post ID, publication date, link and text. Consider the configured recent-post window (48 hours by default).
4. Exclude closed castings, expired deadlines, courses, irrelevant news and actor resumes. Match city, age, gender and height only to explicitly stated requirements; do not mix different roles.
5. Archive suitable posts even if nobody currently matches, then re-evaluate after profile updates. Deduplicate by (user, source, post ID), keeping sent history across restarts and manual scans.
6. Send matches in Russian with source links. Store feedback from «Подходит» / «Не подходит». Use feedback as evaluation evidence; do not claim the model retrains itself.
7. Maintain admin diagnostics: last scan time, posts found, matched and error. Test rate limits, unavailable previews, changed profiles and delivery failures.
8. A SKILL.md guides developers and assistants working on this codebase. The runtime scans and matches using Python. Real AI analysis requires an explicit model API and deployment configuration.
