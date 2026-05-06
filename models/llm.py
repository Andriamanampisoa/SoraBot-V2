##
## SORABOT, 2026
## llm.py
## File description:
## The class LLMClient.
##

import os

from openai import OpenAI

class LLMClient:
    def __init__(self, model="openai/gpt-4o-mini"):
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        api_key = openrouter_key or openai_key

        if not api_key:
            raise RuntimeError(
                "OpenAI API key not found. Set OPENAI_API_KEY or OPENROUTER_API_KEY in your environment or .env"
            )

        client_kwargs = {"api_key": api_key}
        if openrouter_key:
            client_kwargs["base_url"] = "https://openrouter.ai/api/v1"

        self.client = OpenAI(**client_kwargs)
        self.model = model

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

    def chat(self, messages, temperature=0.2):
        """
        Send a message to the LLM and get the response.
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature
            )
            return response.choices[0].message.content

        except Exception as e:
            return f"Erreur LLM: {str(e)}"
