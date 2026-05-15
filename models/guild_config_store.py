##
## SORABOT, 2026
## guild_config_store.py
## File description:
## Encrypted SQLite storage for guild configuration.
##

from __future__ import annotations

import json
import sqlite3

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from models.token_encryption import TokenEncryption

class GuildConfigStore:
    """
    Store encrypted configuration payloads per Discord guild.
    """

    def __init__(self, db_path: str | Path = "data/guild_config.sqlite3"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.encryption = TokenEncryption()
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        """
        Establish a connection to the SQLite database.
        """
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        """
        Create the guild_config table if it doesn't exist.
        """
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id TEXT PRIMARY KEY,
                    encrypted_payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def get_guild_config(self, guild_id: str) -> dict[str, Any]:
        """
        Return the decrypted configuration for a guild.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT encrypted_payload FROM guild_config WHERE guild_id = ?",
                (str(guild_id),),
            ).fetchone()

        if not row:
            return {}

        try:
            decrypted_payload = self.encryption.decrypt(row["encrypted_payload"])
            payload = json.loads(decrypted_payload)
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            print(f"Error loading guild config for {guild_id}: {exc}")
            return {}

    def save_guild_config(self, guild_id: str, config: dict[str, Any]) -> bool:
        """
        Save the full configuration payload for a guild.
        """
        try:
            payload = json.dumps(config, ensure_ascii=False, sort_keys=True)
            encrypted_payload = self.encryption.encrypt(payload)
            updated_at = datetime.now(timezone.utc).isoformat()

            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO guild_config (guild_id, encrypted_payload, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        encrypted_payload = excluded.encrypted_payload,
                        updated_at = excluded.updated_at
                    """,
                    (str(guild_id), encrypted_payload, updated_at),
                )
                connection.commit()
            return True
        except Exception as exc:
            print(f"Error saving guild config for {guild_id}: {exc}")
            return False

    def update_setting(self, guild_id: str, key: str, value: Any) -> bool:
        """
        Update a single configuration value for a guild.
        """
        config = self.get_guild_config(guild_id)
        config[key] = value
        return self.save_guild_config(guild_id, config)

    def remove_setting(self, guild_id: str, key: str) -> bool:
        """
        Remove a configuration value for a guild.
        """
        config = self.get_guild_config(guild_id)
        if key not in config:
            return True
        config.pop(key, None)
        return self.save_guild_config(guild_id, config)

    def clear_guild_config(self, guild_id: str) -> bool:
        """
        Remove every configuration value for a guild.
        """
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM guild_config WHERE guild_id = ?",
                    (str(guild_id),),
                )
                connection.commit()
            return True
        except Exception as exc:
            print(f"Error clearing guild config for {guild_id}: {exc}")
            return False

    def get_setting(self, guild_id: str, key: str, default: Optional[Any] = None) -> Any:
        """
        Return a single setting value for a guild.
        """
        config = self.get_guild_config(guild_id)
        return config.get(key, default)
