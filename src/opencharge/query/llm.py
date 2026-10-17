"""The LLM behind an interface, so the provider can be swapped (brief §2).

The interface is deliberately narrow: send a system prompt and a user message, get back a
string that should be a JSON object. Everything that matters (what that JSON may contain)
is enforced by the Pydantic models in interpret.py, not by trusting the provider.
"""

import os
from typing import Protocol

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> str:
        """Return the model's reply, which the caller will validate as JSON."""
        ...


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_GROQ_MODEL) -> None:
        from groq import Groq  # imported here so tests and other providers don't need it

        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set; see .env.example")
        self._client = Groq(api_key=key)
        self.model = model

    def complete_json(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            # JSON mode: the provider guarantees syntactically valid JSON, nothing more.
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=600,
        )
        return response.choices[0].message.content or ""
