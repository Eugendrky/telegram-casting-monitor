from __future__ import annotations

from telethon.tl.custom import Message
from telethon.utils import get_peer_id


def message_link(message: Message) -> str:
    chat = message.chat
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message.id}"
    peer_id = get_peer_id(chat)
    internal = str(peer_id)
    if internal.startswith("-100"):
        internal = internal[4:]
    return f"https://t.me/c/{internal}/{message.id}"
