"""Repetition: the strongest cheap signal, and the one that catches recordings.

Machines repeat themselves word for word. People never do -- with one
important exception this module has to get right (see REPAIR below).

On stopwords: the instinct is to strip them before fingerprinting. Don't. IVR
prompts are formulaic and their function-word pattern is part of what makes two
renderings identical; strip them and short fragments collapse until unrelated
ones start colliding. Keep them, and use n-gram shingles so that word ORDER
does the discriminating -- two sentences sharing "the" and "to" then score
nothing unless they share an actual sequence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from saathi.types import Family, SignalObservation

N = 4                      # shingle width
MIN_TOKENS = 8             # below this, a repeat means nothing
MIN_SPEECH_MS = 1500
SIM_THRESHOLD = 0.90
CROSS_CALL_LLR = -4.0      # the same prompt on a previous call to this number
IN_CALL_LLR = -3.5

_PUNCT = re.compile(r"[^\w\s]")
_DIGITS = re.compile(r"\b\d+\b")
_WS = re.compile(r"\s+")

# A distressed human saying "Hello? ... Hello?" is a verbatim repeat, and that
# is the worst possible moment to score someone as a machine. The length gates
# above already exclude these, but they are listed explicitly because getting
# this wrong is a trust-destroying failure rather than a scoring inaccuracy.
REPAIR = {
    "hello", "hi", "sorry", "pardon", "you there", "are you there",
    "can you hear me", "still there", "hello can you hear me", "yes", "yeah",
}


def normalise(text: str) -> list[str]:
    """Digit runs collapse to a placeholder so 'wait is 7 minutes' and
    '...9 minutes' still recognise each other as the same announcement."""
    t = _PUNCT.sub(" ", text.lower())
    t = _DIGITS.sub("#", t)
    return _WS.sub(" ", t).strip().split()


def shingles(tokens: list[str], n: int = N) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def similarity(a: str, b: str) -> float:
    sa, sb = shingles(normalise(a)), shingles(normalise(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def is_repair(text: str) -> bool:
    return " ".join(normalise(text)) in REPAIR


def scorable(text: str, speech_ms: int) -> bool:
    return (
        len(normalise(text)) >= MIN_TOKENS
        and speech_ms >= MIN_SPEECH_MS
        and not is_repair(text)
    )


@dataclass
class RepetitionDetector:
    """In-call history plus a cross-call index keyed by destination.

    The cross-call half is the strongest signal available and it is the reason
    persistent per-destination state earns its place in the architecture: an
    IVR prompt is identical across every call to that number.
    """

    destination: str
    seen_in_call: list[tuple[int, str]] = field(default_factory=list)
    cross_call_index: dict[str, set[tuple[str, ...]]] = field(default_factory=dict)
    min_gap_ms: int = 20_000

    def observe(self, text: str, t_ms: int, speech_ms: int, *,
                after_probe: bool = False) -> list[SignalObservation]:
        """`after_probe` drops the minimum-gap rule.

        The gap exists so that natural short-term repetition is not scored. But
        a verbatim replay in DIRECT RESPONSE to a probe is the opposite case --
        it is the single strongest tell available, and it is the entire reason
        the repair probe is worth spending a real person's patience on. Keeping
        the gap here would suppress exactly the evidence the probe elicited.
        """
        out: list[SignalObservation] = []
        if not scorable(text, speech_ms):
            self.seen_in_call.append((t_ms, text))
            return out

        for prior_t, prior in self.seen_in_call:
            if not after_probe and t_ms - prior_t < self.min_gap_ms:
                continue
            if similarity(text, prior) >= SIM_THRESHOLD:
                out.append(SignalObservation(
                    name="replayed_after_probe" if after_probe else "in_call_repeat",
                    family=Family.REPETITION, llr=IN_CALL_LLR, t_ms=t_ms))
                break

        known = self.cross_call_index.get(self.destination)
        if known:
            s = shingles(normalise(text))
            if s and len(s & known) / len(s) >= SIM_THRESHOLD:
                out.append(SignalObservation(
                    name="cross_call_repeat", family=Family.REPETITION,
                    llr=CROSS_CALL_LLR, t_ms=t_ms))

        self.seen_in_call.append((t_ms, text))
        return out

    def remember(self, text: str) -> None:
        """Called at end of call for utterances confirmed to be machine."""
        self.cross_call_index.setdefault(self.destination, set()).update(
            shingles(normalise(text)))
