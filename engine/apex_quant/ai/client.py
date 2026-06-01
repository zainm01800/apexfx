"""LLM client for the Phase 3 narrow-AI layer.

``AppAILLM`` reuses the existing APEX ``/api/ai`` proxy (Gemini primary, Groq
fallback) rather than re-implementing key handling. It degrades to ``None`` on
any failure, so the research pipeline always falls back to its programmatic
proposer and never hard-fails. ``FakeLLM`` makes the whole pipeline testable
offline and deterministic.

Nothing returned by these clients is ever executed. Outputs are parsed into
constrained, validatable hypotheses (see ai/hypothesis.py) - so even a
prompt-injected headline can, at worst, propose a hypothesis that then fails
validation.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable

from apex_quant.config import AiConfig, get_config


class LLMClient(ABC):
    @abstractmethod
    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200,
                 temperature: float = 0.5) -> str | None:
        """Return the model's text, or None if unavailable."""

    @property
    def available(self) -> bool:
        return True


class AppAILLM(LLMClient):
    def __init__(self, cfg: AiConfig | None = None, timeout: float = 60.0):
        self.cfg = cfg or get_config().ai
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.cfg.app_url)

    def complete(self, prompt, system="", max_tokens=1200, temperature=0.5) -> str | None:
        if not self.cfg.app_url:
            return None
        try:
            import httpx

            payload = {"prompt": prompt, "max_tokens": max_tokens, "temperature": temperature}
            if system:
                payload["system"] = system
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(f"{self.cfg.app_url.rstrip('/')}/api/ai", json=payload)
                if res.status_code != 200:
                    return None
                return res.json().get("text")
        except Exception:
            return None


class FakeLLM(LLMClient):
    """Deterministic client for tests/offline. ``responder`` is a list (consumed
    in order, last value repeats) or a callable(prompt, system) -> str."""

    def __init__(self, responder: list[str] | Callable[[str, str], str]):
        self._responder = responder
        self._i = 0

    def complete(self, prompt, system="", max_tokens=1200, temperature=0.5) -> str | None:
        if callable(self._responder):
            return self._responder(prompt, system)
        if not self._responder:
            return None
        val = self._responder[min(self._i, len(self._responder) - 1)]
        self._i += 1
        return val


def extract_json(text: str | None):
    """Pull the first JSON object/array out of an LLM response (tolerant of prose
    and ```json fences). Returns the parsed value or None."""
    if not text:
        return None
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p and p[0] in "[{":
                t = p
                break
    for open_c, close_c in (("[", "]"), ("{", "}")):
        try:
            s, e = t.index(open_c), t.rindex(close_c) + 1
            return json.loads(t[s:e])
        except (ValueError, json.JSONDecodeError):
            continue
    return None
