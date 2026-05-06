##
## SORABOT, 2026
## token_store.py
## File description:
## The TokenStore class to securely store and manage encrypted tokens for each Discord user.
##
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.token_encryption import TokenEncryption

class TokenStore:
    """
    Store and manage encrypted tokens for each Discord user.
    """

    def __init__(self, store_dir: str | Path = "user_tokens"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(exist_ok=True)
        self.encryption = TokenEncryption()
        self._cache = {}  # {user_id: token_data}

    def save_token(
        self,
        user_id: str,
        service: str,
        token: str,
        scopes: Optional[list[str]] = None,
        expires_at: Optional[str] = None,
    ) -> bool:
        """Save an encrypted token for a user and service."""
        try:
            user_file = self.store_dir / f"{self._sanitize_user_id(user_id)}.json"
            data = self._load_file(user_file)
            encrypted_token = self.encryption.encrypt(token)

            if "services" not in data:
                data["services"] = {}

            data["services"][service] = {
                "token": encrypted_token,
                "created_at": datetime.now().isoformat(),
                "last_used": datetime.now().isoformat(),
                "scopes": scopes or [],
                "expires_at": expires_at,
            }

            with open(user_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            # Update cache
            self._cache[user_id] = data
            return True

        except Exception as e:
            print(f"Error saving token for {user_id}/{service}: {e}")
            return False

    def get_token(self, user_id: str, service: str) -> Optional[str]:
        """
        Get decrypted token for a user and service.
        """
        try:
            user_file = self.store_dir / f"{self._sanitize_user_id(user_id)}.json"

            if user_file not in [self.store_dir / f"{self._sanitize_user_id(uid)}.json" for uid in self._cache]:
                if not user_file.exists():
                    return None
                data = self._load_file(user_file)
                self._cache[user_id] = data
            else:
                data = self._cache.get(user_id, {})

            if "services" not in data or service not in data["services"]:
                return None

            service_data = data["services"][service]
            if service_data.get("expires_at"):
                expiry = datetime.fromisoformat(service_data["expires_at"])
                if datetime.now() > expiry:
                    return None

            encrypted_token = service_data.get("token")
            if not encrypted_token:
                return None

            decrypted_token = self.encryption.decrypt(encrypted_token)

            service_data["last_used"] = datetime.now().isoformat()
            with open(user_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return decrypted_token

        except Exception as e:
            print(f"Error retrieving token for {user_id}/{service}: {e}")
            return None

    def has_token(self, user_id: str, service: str) -> bool:
        """
        Check if user has a valid token for a service.
        """
        return self.get_token(user_id, service) is not None

    def delete_token(self, user_id: str, service: str) -> bool:
        """
        Delete a token for a user and service.
        """
        try:
            user_file = self.store_dir / f"{self._sanitize_user_id(user_id)}.json"

            if not user_file.exists():
                return False

            data = self._load_file(user_file)
            if "services" in data and service in data["services"]:
                del data["services"][service]
                with open(user_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                if user_id in self._cache:
                    del self._cache[user_id]
                return True
            return False

        except Exception as e:
            print(f"Error deleting token for {user_id}/{service}: {e}")
            return False

    def list_services(self, user_id: str) -> dict[str, dict]:
        """
        List all services and their metadata (without tokens) for a user.
        """
        try:
            user_file = self.store_dir / f"{self._sanitize_user_id(user_id)}.json"

            if not user_file.exists():
                return {}
            data = self._load_file(user_file)
            if "services" not in data:
                return {}
            result = {}
            for service, service_data in data["services"].items():
                result[service] = {
                    "created_at": service_data.get("created_at"),
                    "last_used": service_data.get("last_used"),
                    "scopes": service_data.get("scopes", []),
                    "expires_at": service_data.get("expires_at"),
                    "valid": self.has_token(user_id, service),
                }
            return result

        except Exception as e:
            print(f"Error listing services for {user_id}: {e}")
            return {}

    def _load_file(self, file_path: Path) -> dict:
        """
        Load JSON file, or return empty dict if not exists.
        """
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {"services": {}}
        return {"services": {}}

    @staticmethod
    def _sanitize_user_id(user_id: str) -> str:
        """Sanitize user ID for use as filename."""
        return str(user_id).replace(":", "_").replace("/", "_").replace("\\", "_").replace("..", "_")
