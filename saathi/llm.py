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
