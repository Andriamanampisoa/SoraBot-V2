##
## SORABOT, 2026
## llm.py
## File description:
## The class LLMClient.
##

import os

from openai import OpenAI

class LLMClient:
    def __init__(self, model="openai/gpt-4o-mini", api_key: str | None = None, base_url: str | None = None):
        """
        LLM client wrapper.

        - If `api_key` is provided, it will be used. Otherwise fallback to environment variables.
        - If `base_url` is provided, it will be passed to the OpenAI client (used for OpenRouter).
        """
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        resolved_key = api_key or openrouter_key or openai_key

        self.model = model
        self.client = None

        if not resolved_key:
            return

        client_kwargs = {"api_key": resolved_key}

        if base_url:
            client_kwargs["base_url"] = base_url
        elif openrouter_key or (api_key and api_key.startswith("sk-or-")):
            client_kwargs["base_url"] = "https://openrouter.ai/api/v1"

        self.client = OpenAI(**client_kwargs)

    def set_model(self, model):
        """
        Set the LLM model.
        """
        self.model = model

    def get_model(self):
        """
        Get the current LLM model.
        """
        return self.model

    def get_client(self):
        """
        Get the OpenAI client.
        """
        return self.client

    def is_configured(self) -> bool:
        """
        Return True when an API key is configured.
        """
        return self.client is not None

    def chat(self, messages, temperature=0.2):
        """
        Send a message to the LLM and get the response.
        """
        if self.client is None:
            return "OpenRouter API key is not configured."

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature
            )
            return response.choices[0].message.content

        except Exception as e:
            return f"Erreur LLM: {str(e)}"
