"""Only explicitly selected Telegram groups and channels may be scanned."""

from __future__ import annotations


def is_source_dialog(dialog: object) -> bool:
    return not dialog.is_user and (dialog.is_group or dialog.is_channel)


def source_allowed(dialog: object, scan_mode: str, mode: str, chosen: set[int]) -> bool:
    # Legacy mode parameters are kept for callers, but never authorize bulk scanning.
    return is_source_dialog(dialog) and dialog.id in chosen
