"""Cautious, unauthenticated reading of public Telegram web previews.

Only explicit public channel usernames; no user sessions, private invites,
proxy rotation, challenge bypass, or scraping of Telegram account dialogs.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from bs4 import BeautifulSoup

LOG = logging.getLogger("casting.public")
CHANNEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
LINK = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{4,31})/?(?:\?.*)?$", re.I)
MAX_HTML_BYTES = 1_500_000


class PreviewError(Exception):
    pass


class PreviewRateLimited(PreviewError):
    def __init__(self, retry_after: int = 3600):
        self.retry_after = max(600, min(retry_after, 86400))
        super().__init__("public preview rate limited")


@dataclass(frozen=True)
class PublicPost:
    message_id: int
    text: str
    url: str
    timestamp: int | None


def normalize_channel(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("@"):
        value = value[1:]
    match = LINK.fullmatch(value)
    if match:
        value = match.group(1)
    if not CHANNEL.fullmatch(value):
        raise ValueError("Укажите публичный канал: @username или https://t.me/username")
    return value.lower()


def source_id(username: str) -> int:
    # Distinct range from Telegram's normal negative chat IDs; stable across restarts.
    raw = int.from_bytes(hashlib.sha256(username.lower().encode("ascii")).digest()[:7], "big")
    return -(4_000_000_000_000_000_000 + raw)


def parse_preview(markup: str, username: str) -> list[PublicPost]:
    username = normalize_channel(username)
    soup = BeautifulSoup(markup, "html.parser")
    result: dict[int, PublicPost] = {}
    matches = soup.select(".tgme_widget_message[data-post]")
    if not matches:
        raise PreviewError("Нет доступных публикаций в публичном веб-предпросмотре")
    for item in matches:
        ref = item.get("data-post") or ""
        name, sep, number = ref.partition("/")
        if not sep or name.casefold() != username or not number.isdigit():
            continue
        message_id = int(number)
        if message_id <= 0:
            continue
        text = item.select_one(".tgme_widget_message_text")
        if text is None:
            # Text absent in some media-only posts; do not manufacture a casting.
            continue
        body = text.get_text("\n", strip=True)
        if not body:
            continue
        time_node = item.select_one("time[datetime]")
        timestamp = None
        if time_node:
            try:
                stamp = datetime.fromisoformat(time_node["datetime"].replace("Z", "+00:00"))
                timestamp = int(stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp())
            except (ValueError, OverflowError):
                pass
        result[message_id] = PublicPost(
            message_id=message_id, text=body[:8000],
            url=f"https://t.me/{username}/{message_id}", timestamp=timestamp,
        )
    if not result:
        raise PreviewError("Публикации недоступны в веб-предпросмотре")
    return sorted(result.values(), key=lambda post: post.message_id)


def fetch_preview(username: str, timeout: int = 15) -> list[PublicPost]:
    username = normalize_channel(username)
    url = f"https://t.me/s/{username}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CastingRadar/1.0 (public channel preview)",
                 "Accept": "text/html"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = response.geturl()
            # Public Telegram preview only; no login, third-party redirects, or other hosts.
            from urllib.parse import urlsplit
            endpoint = urlsplit(final)
            if endpoint.scheme != "https" or endpoint.hostname not in ("t.me", "www.t.me"):
                raise PreviewError("Telegram перенаправил на другой сайт")
            if response.status != 200:
                raise PreviewError(f"HTTP {response.status}")
            data = response.read(MAX_HTML_BYTES + 1)
            if len(data) > MAX_HTML_BYTES:
                raise PreviewError("Слишком большая страница канала")
            return parse_preview(data.decode("utf-8", errors="replace"), username)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            delay = exc.headers.get("Retry-After", "") if exc.headers else ""
            raise PreviewRateLimited(int(delay) if delay.isdigit() else 3600) from exc
        raise PreviewError(f"Публичная страница Telegram вернула код {exc.code}") from exc
    except (OSError, UnicodeError) as exc:
        raise PreviewError(f"Сбой подключения: {type(exc).__name__}") from exc


async def monitor_public(db, receiver, *, interval_seconds: int = 1200,
                         lookback_hours: int = 48, fetch=fetch_preview):
    """Sequential polling, durable cursor, bounded web activity and safe error backoff."""
    from time import time
    interval_seconds = max(900, interval_seconds)
    LOG.info("Public scanning enabled: %s selected sources", len(db.public_sources()))
    while True:
        for chat_id, title, kind, enabled, joined, username in db.public_sources():
            if not (enabled and joined and username):
                continue
            if not db.should_scan_public(chat_id, interval_seconds):
                continue
            try:
                posts = await asyncio.to_thread(fetch, username)
            except PreviewRateLimited as exc:
                db.update_public_status(chat_id, error="Telegram временно ограничил запросы",
                                        retry_after=exc.retry_after)
                LOG.warning("Public preview rate-limited for %s. Pausing reads.", username)
                # Stop processing other sources for this cycle too.
                break
            except PreviewError as exc:
                db.update_public_status(chat_id, error=str(exc)[:170],
                                        retry_after=max(3600, interval_seconds * 2))
                LOG.warning("Public preview unavailable for %s: %s",username,exc)
                continue
            except Exception:
                db.update_public_status(chat_id,error="Не удалось получить страницу",
                                        retry_after=3600)
                LOG.exception("Unexpected public preview failure for %s", username)
                continue
            cursor = db.public_cursor(chat_id)
            previous_matched = db.count_source_matches(chat_id)
            new_posts = 0
            fresh_cutoff = int(time()) - lookback_hours * 3600
            for post in posts:
                is_new = post.message_id > cursor
                if is_new:
                    new_posts += 1
                # The recent visible window is re-evaluated at each scheduled scan,
                # so changing a profile does not permanently lose older posts.
                if post.timestamp is not None and post.timestamp < fresh_cutoff:
                    if is_new:
                        db.advance_public_cursor(chat_id,post.message_id)
                    continue
                if post.timestamp is None and not is_new:
                    continue
                receiver.handle_post({
                    "chat": {"id":chat_id, "title":title, "type":"channel", "username":username},
                    "message_id":post.message_id, "text":post.text, "date":post.timestamp,
                })
                if is_new:
                    db.advance_public_cursor(chat_id, post.message_id)
            matched = max(0,db.count_source_matches(chat_id)-previous_matched)
            db.set_scan_stats(chat_id,posts=len(posts),new_posts=new_posts,matched=matched)
            db.update_public_status(chat_id, error=None, retry_after=interval_seconds)
            LOG.info("Public source %s: visible=%s new=%s matched=%s",
                     username,len(posts),new_posts,matched)
            await asyncio.sleep(2)
        await asyncio.sleep(30)
