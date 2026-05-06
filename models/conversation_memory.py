##
## SORABOT, 2026
## conversation_memory.py
## File description:
## The ConversationMemory class to manage conversation history per user with automatic cleanup and persistence.
##

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

class ConversationMemory:
    """
    Manage conversation history per Discord user with automatic cleanup.
    Stores messages in memory and persists to disk for durability.
    """

    def __init__(self, memory_dir: str | Path = "conversation_memory", max_age_days: int = 7, max_messages_per_user: int = 50):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(exist_ok=True)
        self.max_age_days = max_age_days
        self.max_messages_per_user = max_messages_per_user
        self._in_memory_cache = {}  # {user_id: [(role, content, timestamp), ...]}

    def add_exchange(self, user_id: str, user_message: str, bot_response: str) -> None:
        """
        Store a user message and bot response in conversation history.
        """
        user_key = self._sanitize_user_id(user_id)
        timestamp = datetime.now().isoformat()

        if user_key not in self._in_memory_cache:
            self._in_memory_cache[user_key] = []
        self._in_memory_cache[user_key].append({
            "role": "user",
            "content": user_message,
            "timestamp": timestamp,
        })
        self._in_memory_cache[user_key].append({
            "role": "bot",
            "content": bot_response,
            "timestamp": timestamp,
        })


        if len(self._in_memory_cache[user_key]) > self.max_messages_per_user:
            self._in_memory_cache[user_key] = self._in_memory_cache[user_key][-self.max_messages_per_user:]
        self._save_to_file(user_key)

    def get_context(self, user_id: str, max_messages: int = 10) -> str:
        """
        Retrieve formatted conversation context for a user (last N messages).
        """
        user_key = self._sanitize_user_id(user_id)
        messages = self._load_messages(user_key)

        if not messages:
            return ""

        cutoff_time = datetime.now() - timedelta(days=self.max_age_days)
        recent_messages = [m for m in messages if self._parse_timestamp(m.get("timestamp")) > cutoff_time]
        recent_messages = recent_messages[-max_messages:]

        if not recent_messages:
            return ""

        lines = []
        for msg in recent_messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def get_conversation_history(self, user_id: str, max_messages: int = 20) -> list[dict]:
        """
        Return conversation history as list of messages for LLM context.
        """
        user_key = self._sanitize_user_id(user_id)
        messages = self._load_messages(user_key)

        if not messages:
            return []

        cutoff_time = datetime.now() - timedelta(days=self.max_age_days)
        recent = [m for m in messages if self._parse_timestamp(m.get("timestamp")) > cutoff_time]
        recent = recent[-max_messages:]
        return [
            {
                "role": m.get("role"),
                "content": m.get("content", ""),
            }
            for m in recent
        ]

    def clear_user_memory(self, user_id: str) -> None:
        """
        Clear all conversation history for a user
        """
        user_key = self._sanitize_user_id(user_id)

        if user_key in self._in_memory_cache:
            del self._in_memory_cache[user_key]

        file_path = self.memory_dir / f"{user_key}.json"
        if file_path.exists():
            file_path.unlink()

    def _sanitize_user_id(self, user_id: str) -> str:
        """
        Sanitize user ID for use as filename.
        """
        return str(user_id).replace(":", "_").replace("/", "_").replace("\\", "_")

    def _load_messages(self, user_key: str) -> list[dict]:
        """
        Load messages from cache or file.
        """
        if user_key in self._in_memory_cache:
            return self._in_memory_cache[user_key]

        file_path = self.memory_dir / f"{user_key}.json"
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    messages = json.load(f)
                    self._in_memory_cache[user_key] = messages
                    return messages
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_to_file(self, user_key: str) -> None:
        """
        Persist in-memory cache to file.
        """
        if user_key not in self._in_memory_cache:
            return

        file_path = self.memory_dir / f"{user_key}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self._in_memory_cache[user_key], f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Warning: Could not save conversation memory for {user_key}: {e}")

    @staticmethod
    def _parse_timestamp(timestamp_str: str) -> datetime:
        """
        Parse ISO format timestamp.
        """
        try:
            return datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            return datetime.now() - timedelta(days=999)
