"""One protocol, several backings. Nothing else in the package imports a vendor.

Latency classes matter here: the live conversation needs a fast hosted model,
while IVR reasoning, mandate parsing and the post-call analyst are all
latency-tolerant and can run on a local vLLM. The detector's verdict uses no
model at all -- that is the one decision where latency is trust.
"""

from __future__ import annotations

import json
from typing import Callable


class StubLLM:
    """Scripted responses. Default in tests so nothing reaches a network."""

    def __init__(self, responses: list[str] | Callable[[list[dict]], str] | None = None):
        self._responses = responses or []
        self._i = 0
        self.calls: list[dict] = []

    def chat(self, messages, *, tools=None, max_tokens=1024, temperature=0.0):
        self.calls.append({"messages": messages, "tools": tools})
        if callable(self._responses):
            return self._responses(messages), {"stub": True}
        if self._i < len(self._responses):
            out = self._responses[self._i]
            self._i += 1
            return out, {"stub": True}
        return "{}", {"stub": True}


class CachedLLM:
    """Serves a recorded call from cache so replay makes zero live model calls.

    A miss during replay is counted and surfaced rather than silently falling
    through to a live model, because that would make the headline numbers
    irreproducible without anyone noticing.
    """

    def __init__(self, inner, cache: dict[str, str] | None = None, *, strict=False):
        self.inner = inner
        self.cache = cache if cache is not None else {}
        self.strict = strict
        self.misses = 0

    @staticmethod
    def key(messages, tools) -> str:
        return json.dumps({"m": messages, "t": tools}, sort_keys=True)

    def chat(self, messages, *, tools=None, max_tokens=1024, temperature=0.0):
        k = self.key(messages, tools)
        if k in self.cache:
            return self.cache[k], {"cached": True}
        self.misses += 1
        if self.strict:
            raise LookupError("replay cache miss; refusing to call a live model")
        out, meta = self.inner.chat(
            messages, tools=tools, max_tokens=max_tokens, temperature=temperature
        )
        self.cache[k] = out
        return out, meta


# Every provider below speaks the same OpenAI chat-completions wire format, so
# switching is a base_url and a key -- never a code change. That is the whole
# point of the LLMClient Protocol, and it is what let the Bedrock outage cost
# nothing: the local model took over without touching a call site.
PROVIDERS = {
    "ollama":     ("http://127.0.0.1:11434/v1",                  "qwen2.5:latest",          False),
    "groq":       ("https://api.groq.com/openai/v1",             "llama-3.3-70b-versatile", True),
    "xai":        ("https://api.x.ai/v1",                        "grok-4-fast",             True),
    "openrouter": ("https://openrouter.ai/api/v1",               "x-ai/grok-4-fast:free",   True),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai",
                                                                 "gemini-2.5-flash",        True),
    "deepseek":   ("https://api.deepseek.com/v1",                "deepseek-chat",           True),
    "together":   ("https://api.together.xyz/v1",                "Qwen/Qwen2.5-72B-Instruct-Turbo", True),
}


class OpenAICompatLLM:
    """One client for every OpenAI-compatible endpoint, local or hosted.

    Almost everything the model does here is latency-tolerant -- parsing a
    mandate before the call, judging a turn pair, reading a menu the grammar
    could not, writing the post-call summary. The one decision that is NOT,
    the human-or-machine verdict, deliberately has no model in it at all.
    """

    def __init__(self, provider: str = "ollama", *, model: str | None = None,
                 api_key: str | None = None, base_url: str | None = None,
                 timeout: float = 120.0) -> None:
        import os
        if provider not in PROVIDERS and base_url is None:
            raise ValueError(f"unknown provider {provider!r}; "
                             f"known: {', '.join(PROVIDERS)}")
        default_url, default_model, needs_key = PROVIDERS.get(
            provider, (base_url, model, True))
        self.provider = provider
        self.base_url = (base_url or default_url).rstrip("/")
        self.model = model or default_model
        self.api_key = api_key or os.environ.get(f"{provider.upper()}_API_KEY")
        self.timeout = timeout
        if needs_key and not self.api_key:
            raise ValueError(
                f"{provider} needs a key: set {provider.upper()}_API_KEY")

    def chat(self, messages, *, tools=None, max_tokens=1024, temperature=0.0):
        import json as _json
        import urllib.error
        import urllib.request

        body = {"model": self.model, "messages": list(messages),
                "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            # Callers want a JSON object matching a tool's input_schema. Ask for
            # JSON and put the schema in the prompt rather than relying on
            # tool-calling support, which varies a lot across these providers.
            schema = tools[0].get("input_schema", {})
            body["response_format"] = {"type": "json_object"}
            body["messages"] = list(messages) + [{
                "role": "system",
                "content": ("Reply with a single JSON object matching this "
                            "schema and nothing else:\n" + _json.dumps(schema)),
            }]

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=_json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = _json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"{self.provider} HTTP {e.code}: {e.read()[:200].decode('utf8','replace')}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(f"{self.provider} unreachable at {self.base_url}: {e}") from e

        return (payload["choices"][0]["message"]["content"],
                {"provider": self.provider, "model": self.model,
                 **payload.get("usage", {})})

    @staticmethod
    def available(base_url: str = "http://127.0.0.1:11434") -> bool:
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(base_url + "/api/tags", timeout=2):
                return True
        except (urllib.error.URLError, TimeoutError, OSError):
            return False


def OllamaLLM(model: str = "qwen2.5:latest",
              base_url: str = "http://127.0.0.1:11434/v1",
              timeout: float = 120.0) -> OpenAICompatLLM:
    """The local default. Kept as a named entry point because it is the one
    that needs no key and no account."""
    c = OpenAICompatLLM("ollama", model=model, base_url=base_url, timeout=timeout)
    return c


OllamaLLM.available = OpenAICompatLLM.available  # type: ignore[attr-defined]
