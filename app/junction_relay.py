"""Junction Bot relay: accept posts only from a bound group and verified sender."""
from __future__ import annotations
import re
from urllib.parse import urlparse
from app.public_channels import normalize_channel, source_id

LINK=re.compile(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{4,31})/(\d+)(?:\b|[/?#])",re.I)

def origin(message,username):
    """Return original post id and timestamp only when provenance is explicit."""
    forwarded=message.get("forward_origin") or {}
    if forwarded.get("type")=="channel":
        chat=forwarded.get("chat") or {}
        if chat.get("username","").lower()==username and isinstance(forwarded.get("message_id"),int):
            return forwarded["message_id"],int(forwarded.get("date") or message.get("date") or 0)
    raw=(message.get("text") or message.get("caption") or "")
    for match in LINK.finditer(raw):
        if match.group(1).lower()==username:
            return int(match.group(2)),int(message.get("date") or 0)
    return None

def normalized(message, binding):
    """Refuse ambiguous copied posts, bot impersonation and unknown originals."""
    source_chat_id,title,username,bot_id=binding
    if (message.get("from") or {}).get("id")!=bot_id:
        return None
    if not (message.get("from") or {}).get("is_bot"):
        return None
    if source_chat_id!=source_id(username):
        return None
    value=origin(message,username)
    if value is None:
        return None
    mid,ts=value
    body=(message.get("text") or message.get("caption") or "").strip()
    if not body or mid<=0 or ts<=0:
        return None
    return {"chat":{"id":source_chat_id,"title":title,"username":username,
                    "type":"channel"},"message_id":mid,"date":ts,"text":body,
            "from":{"is_bot":False}}
