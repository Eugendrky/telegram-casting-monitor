"""Encrypted Telegram session codec: keys only from a protected environment variable."""
from __future__ import annotations
from cryptography.fernet import Fernet, InvalidToken

class SessionCipher:
    def __init__(self,key:str):
        if not key:
            raise ValueError("SESSION_ENCRYPTION_KEY is required")
        try:
            self.cipher=Fernet(key.encode("ascii"))
        except (ValueError,TypeError) as exc:
            raise ValueError("Invalid session encryption key") from exc

    def seal(self,session:str)->bytes:
        if not isinstance(session,str) or not session:
            raise ValueError("Session is empty")
        return self.cipher.encrypt(session.encode("utf-8"))

    def open(self,encrypted:bytes)->str:
        try:
            return self.cipher.decrypt(encrypted).decode("utf-8")
        except (InvalidToken,UnicodeError,TypeError) as exc:
            raise ValueError("Failed to decrypt session") from exc
