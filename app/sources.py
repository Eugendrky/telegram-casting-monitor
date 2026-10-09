"""Source selection for the scanner, independent of the BotFather bot."""

from __future__ import annotations


def is_source_dialog(dialog: object) -> bool:
    return not dialog.is_user and (dialog.is_group or dialog.is_channel)


def source_allowed(dialog: object, scan_mode: str, mode: str, chosen: set[int]) -> bool:
    if not is_source_dialog(dialog):
        return False
    if mode == "selected":
        return dialog.id in chosen
    if mode == "all":
        return True
    if scan_mode == "title":
        from app.matcher import is_casting_title
        return is_casting_title(dialog.name or "")
    return True