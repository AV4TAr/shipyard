"""Thin wrapper around OpenRouter's OpenAI-compatible API."""

from __future__ import annotations

import json
import os
import urllib.request


class LLMClient:
    """Thin wrapper around OpenRouter's API (OpenAI-compatible)."""

    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "anthropic/claude-sonnet-4-20250514",
    ):
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY env var "
                "or pass api_key to LLMClient."
            )
        self._model = model

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str:
        """Send a chat completion request and return the response text."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.BASE_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        return body["choices"][0]["message"]["content"]
