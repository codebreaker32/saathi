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

# Any cluster of 4+ digits mutes the agent regardless of the words around it,
# separators included. Counting DIGITS rather than characters is the whole
# point: the previous `\b\d{4,8}\b` caught 4111-1111-1111-1111 only because
# each group happens to be four digits, and missed a bare 4111111111111111
# entirely -- a word boundary cannot occur inside a digit run, so no 4-to-8
# digit substring of one is ever \b-bounded. playbook._CLUSTER already scans
# this way; this is the same rule applied where it was missing.
_CLUSTER = re.compile(r"\d(?:[\s.\-_/]?\d)*")
MIN_RUN_DIGITS = 4


def _bare(s: str) -> str:
    return re.sub(r"\D", "", s)


def digit_runs(text: str) -> list[str]:
    """Digit clusters of MIN_RUN_DIGITS or more, returned without separators."""
    return [d for d in (_bare(m.group()) for m in _CLUSTER.finditer(text))
            if len(d) >= MIN_RUN_DIGITS]


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
        ours = {_bare(o) for o in (ours or set())}
        for run in digit_runs(text):
            if run not in ours:
                return "digit_run"
        return None


def redact_digits(text: str) -> str:
    """Redacts every digit cluster of MIN_RUN_DIGITS or more.

    NOT CURRENTLY WIRED. Nothing in saathi/ or server/ calls this, so the
    protection the module header describes is not in force on any stored
    transcript. The rule is right and the call site is the missing half; do not
    cite it as a guarantee until something calls it.
    """
    return _CLUSTER.sub(
        lambda m: "[redacted]" if len(_bare(m.group())) >= MIN_RUN_DIGITS
        else m.group(), text)
