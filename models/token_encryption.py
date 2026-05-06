##
## SORABOT, 2026
## token_encryption.py
## File description:
## The TokenEncryption class to encrypt and decrypt sensitive tokens using Fernet symmetric encryption.
##

from __future__ import annotations

import os
from cryptography.fernet import Fernet, InvalidToken

class TokenEncryption:
    """
    Encrypt and decrypt sensitive tokens using Fernet.
    """

    def __init__(self):
        encryption_key = os.getenv("ENCRYPTION_KEY")
        if not encryption_key:
            raise ValueError("ENCRYPTION_KEY environment variable not set")
        try:
            self.cipher = Fernet(encryption_key.encode())
        except ValueError as e:
            raise ValueError(f"Invalid ENCRYPTION_KEY format: {e}")

    def encrypt(self, plain_text: str) -> str:
        """
        Encrypt a plain text token.
        """
        try:
            encrypted = self.cipher.encrypt(plain_text.encode())
            return encrypted.decode()
        except Exception as e:
            raise ValueError(f"Encryption failed: {e}")

    def decrypt(self, encrypted_text: str) -> str:
        """
        Decrypt an encrypted token.
        """
        try:
            decrypted = self.cipher.decrypt(encrypted_text.encode())
            return decrypted.decode()
        except InvalidToken:
            raise ValueError("Invalid encrypted token: cannot decrypt")
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")
