"""DeepSeekLLM — direct DeepSeek API client for the APEX narrow-AI layer.

DeepSeek exposes an OpenAI-compatible API, so this client posts to
``https://api.deepseek.com/chat/completions`` using a plain ``httpx`` call.
No extra SDK is required; ``httpx`` is already in requirements.txt.

Priority chain when the pipeline looks for an LLM:
  1. ``DeepSeekLLM`` when ``config.ai.deepseek_api_key`` is set (new).
  2. ``AppAILLM``    when ``config.ai.app_url``          is set (legacy).
  3. ``None``        → programmatic proposer + debate-skip.

Nothing returned by these clients is ever executed. All outputs are parsed
into constrained, validatable hypotheses (ai/hypothesis.py) — so even a
prompt-injected response can, at worst, propose a hypothesis that then
fails CPCV/DSR/PBO validation.
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


# ---------------------------------------------------------------------------
#  DeepSeek (direct API — preferred when key is set)
# ---------------------------------------------------------------------------
class DeepSeekLLM(LLMClient):
    """Direct DeepSeek API client using the OpenAI-compatible endpoint.

    Parameters
    ----------
    cfg :
        Engine ``AiConfig``. Reads ``deepseek_api_key``, ``deepseek_model``,
        and ``deepseek_base_url``.
    timeout :
        HTTP timeout in seconds (DeepSeek can be slow under load).
    """

    def __init__(self, cfg: AiConfig | None = None, timeout: float = 90.0) -> None:
        self.cfg = cfg or get_config().ai
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.cfg.deepseek_api_key)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200,
                 temperature: float = 0.5) -> str | None:
        if not self.cfg.deepseek_api_key:
            return None
        try:
            import httpx

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.cfg.deepseek_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }
            headers = {
                "Authorization": f"Bearer {self.cfg.deepseek_api_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.cfg.deepseek_base_url.rstrip('/')}/chat/completions"

            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json=payload, headers=headers)
                if res.status_code != 200:
                    print(f"  [DeepSeek Error] HTTP {res.status_code}: {res.text[:300]}")
                    return None
                data = res.json()
                return data["choices"][0]["message"]["content"]

        except Exception as exc:
            print(f"  [DeepSeek Exception] {type(exc).__name__}: {exc}")
            return None


# ---------------------------------------------------------------------------
#  Gemini (direct API via OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------
class GeminiLLM(LLMClient):
    """Direct Google Gemini API client using the OpenAI-compatible endpoint.

    Reads ``gemini_api_key`` from config or env.
    """

    def __init__(self, cfg: AiConfig | None = None, timeout: float = 90.0) -> None:
        self.cfg = cfg or get_config().ai
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", ""))

    def complete(self, prompt: str, system: str = "", max_tokens: int = 1200,
                  temperature: float = 0.5) -> str | None:
        api_key = self.cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return None
        try:
            import httpx

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": "gemini-2.5-flash",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json=payload, headers=headers)
                if res.status_code != 200:
                    print(f"  [Gemini Error] HTTP {res.status_code}: {res.text[:300]}")
                    return None
                data = res.json()
                return data["choices"][0]["message"]["content"]

        except Exception as exc:
            print(f"  [Gemini Exception] {type(exc).__name__}: {exc}")
            return None


# ---------------------------------------------------------------------------
#  Legacy App-Proxy client
# ---------------------------------------------------------------------------
class AppAILLM(LLMClient):
    """Calls the APEX app /api/ai proxy (Gemini primary, Groq fallback).

    Degrades to ``None`` on any failure so the research pipeline always
    falls back to its programmatic proposer and never hard-fails.
    """

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

            payload = {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "useLocalLlm": getattr(self.cfg, "use_local_llm", False),
                "localLlmUrl": getattr(self.cfg, "local_llm_url", ""),
                "localLlmModel": getattr(self.cfg, "local_llm_model", ""),
                "localLlmKey": getattr(self.cfg, "local_llm_key", ""),
            }
            if system:
                payload["system"] = system
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(f"{self.cfg.app_url.rstrip('/')}/api/ai", json=payload)
                if res.status_code != 200:
                    print(f"  [LLM Error] API status {res.status_code}: {res.text}")
                    return None
                return res.json().get("text")
        except Exception as e:
            print(f"  [LLM Exception] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return None


# ---------------------------------------------------------------------------
#  Fake / Test client
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
#  Utilities
# ---------------------------------------------------------------------------
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


def build_llm(cfg: AiConfig | None = None) -> LLMClient | None:
    """Factory: returns the best available LLM client for the given config.

    Priority: DeepSeek (direct) > Gemini (direct) > AppProxy > None.
    Returns None when no API key or URL is configured.
    """
    cfg = cfg or get_config().ai
    if cfg.deepseek_api_key:
        client = DeepSeekLLM(cfg)
        if client.available:
            return client
    # Try Gemini direct
    client_gem = GeminiLLM(cfg)
    if client_gem.available:
        return client_gem
    if cfg.app_url:
        client = AppAILLM(cfg)
        if client.available:
            return client
    return None
