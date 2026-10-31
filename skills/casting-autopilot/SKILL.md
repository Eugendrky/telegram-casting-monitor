---
name: casting-autopilot
description: Guide development and testing of a Telegram casting monitor that reads only administrator-approved public web-visible posts, extracts audition requirements, matches actors' profiles, avoids repeated notifications, handles inaccessible public groups, and maintains Russian-language diagnostics. Use when improving source collection, role relevance, user feedback, delivery, or monitoring regressions.
---

# Casting autopilot

1. First check source availability and `public_state`. Distinguish a successful scan with no matches from a Telegram group whose public web page exposes no posts. Do not claim access to member-only content via a public link.
2. Poll approved public web-visible sources only, honoring rate limits and cooldowns. Manual scans must not bypass backoff.
3. Preserve original username, post ID, date, text and permalink; use a default 48-hour freshness window when available.
4. Exclude courses, expired calls, closed selections, unrelated news and resumes. Missing role information is unknown, not an invented requirement.
5. Match each role's gender, age, city and height together. Use positive and negative Russian-language examples.
6. Archive candidates irrespective of currently matching users; re-evaluate after profile changes. Deduplicate per user/source/post and suppress exact-text reposts across approved channels while retaining distinct role texts.
7. Keep sent/pending/failed records durably. Respect retry bounds, never automatically resend successful deliveries. Collect explicit user feedback.
8. Provide Russian admin diagnostics for source availability, last scan, visible posts, matches and delivery outcomes. Inaccessible previews are not evidence that the channel contains no castings.
9. Test migrations, reposts, rate limits, missing group previews, role matching and retries before deployment.
10. This Skill guides development, not runtime execution or AI model training. Real model-based relevance scoring needs its own explicit integration and safety tests. Do not send Telegram credentials to an AI model.
