"""Detecting a request for authenticating data, and stopping inside a frame.

Only the deterministic layer gates. A model may also classify each turn, but
asynchronously and purely to label the event -- by the time it returns the
handoff has already happened. Never put a model in the latency path of a
safety stop.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PHRASE_DIR = Path(__file__).resolve().parent.parent / "phrases"

# Any run of 4-8 digits mutes the agent regardless of the words around it.
# The agent never reads digits back, and runs are redacted before a transcript
# is persisted, so an OTP is never written down.
DIGIT_RUN = re.compile(r"\b\d{4,8}\b")


class VerificationDetector:
    def __init__(self, locales: tuple[str, ...] = ("en", "hi")) -> None:
        self.patterns: list[tuple[str, re.Pattern]] = []
        for loc in locales:
            path = PHRASE_DIR / f"verification.{loc}.yaml"
            if not path.exists():
                continue
            data = yaml.safe_load(path.read_text())
            for raw in data.get("patterns", []):
                self.patterns.append((f"{loc}:{raw}", re.compile(raw, re.I)))

    def check(self, text: str, *, ours: set[str] | None = None) -> str | None:
        """Returns the trigger that fired, or None. Recall over precision.

        `ours` is the set of digit strings WE stated -- order numbers, amounts,
        the registered phone. A rep reading our own order number back to us is
        the most ordinary thing on a support call, and treating it as a
        verification request hands over every call at the first useful moment.
        Digits we did not supply stay suspicious.
        """
        for label, pattern in self.patterns:
            if pattern.search(text):
                return label
        ours = ours or set()
        for run in DIGIT_RUN.findall(text):
            if run not in ours:
                return "digit_run"
        return None


def redact_digits(text: str) -> str:
    """Applied before any transcript is stored."""
    return DIGIT_RUN.sub("[redacted]", text)
