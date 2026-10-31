# Casting Radar: public channel monitoring

Telegram Bot API delivers notifications and private profile settings. Selected public channels are fetched through their openly accessible `https://t.me/s/<username>` previews. No user account session, Telegram Desktop keys, or membership in those channels.

## What works

- Owner sends `/addsource @publicchannel` or `/addsource https://t.me/publicchannel`.
- The bot polls public previews sequentially, no more often than every 15 minutes; default 30 minutes. This is not a real-time feed.
- New text posts are parsed, deduplicated by channel and post ID and matched to registered user profiles.
- `/city`, `/age`, `/gender`, `/height`, `/pause`, `/resume`, `/sources`, `/follow`, `/unfollow`, `/castings`.
- `/publicstatus`: the owner's source status, last processed post and latest error.
- SQLite persists profiles, sources, post cursors, matches, and delivery statuses on a mounted volume.

## Limits and safeguards

- Supports **public channels with web-visible posts only**. Closed groups, invite links, protected content and private chats cannot be read with this approach.
- Availability and amount of public web history vary; missed posts are possible if a channel posts faster than the polling cadence.
- Telegram may rate-limit or change the webpage. On 429 the monitor pauses source reads and does not try to bypass restrictions. No proxy rotation, captcha bypass or fake client identity.
- Monitoring a channel starts only after the owner's explicit source command. No bulk discovery or crawling of subscriptions.
- Posts are parsed as published. Automatic AI interpretation is optional future work and not enabled by this runtime; current match uses deterministic profile filters.
- For privacy, only public posts should be added; regular Telegram members do not need to share account sessions or credentials.

## Railway configuration

`BOT_TOKEN`: token from BotFather.
`BOT_OWNER_ID`: Telegram ID of owner (send `/start` to retrieve it before adding sources).
`SQLITE_PATH=/data/castings.db`.
`PUBLIC_SCAN_INTERVAL_SECONDS=1800`.
`PUBLIC_LOOKBACK_HOURS=48`.

`Start Command: python -m app.bot_runtime`.
`Dockerfile` defaults to this command too. Only one service instance per bot token.
Attach a Railway persistent volume on `/data` to the bot service. Without it all state will be lost on replacement.

After deploy, open the bot and send `/start`. Set `BOT_OWNER_ID`, then add a public channel with `/addsource @channelname`. On `/publicstatus`, check that the first scan has completed, and verify end-to-end with a new public test post.

To run unit tests: `python -m unittest discover -s tests -v`.

The legacy Telethon implementation is still in repository files but **not used** by this entrypoint.
