"""Stable fingerprint for exact reposts, never fuzzy-match unrelated castings."""
from __future__ import annotations
import hashlib
import re
import unicodedata

_URL = re.compile(r"https?://[^\s<>]+", re.I)
_SPACE = re.compile(r"\s+")

def fingerprint(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().replace("ё","е")
    value = _URL.sub(" ", value)
    value = _SPACE.sub(" ", value).strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
