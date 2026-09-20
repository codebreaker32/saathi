"""Choosing a digit from a menu heard for the first time.

An IVR menu is an append-only stream of options with an unknown end, where
commitment is irreversible. That makes it optimal stopping, not classification.
Two consequences drive the design:

  - Never press on a partial menu. The option you want is often last.
  - Some options are one-way. An ACTION_VERB option (confirm, cancel, pay,
    authorise) is removed from the candidate set BEFORE anything scores it, so
    a model is never in a position to choose one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# "for billing press 2" and "press 2 for billing" are both common; so are
# bilingual prompts on Indian lines ("billing ke liye 1 dabaye").
_FOR_THEN_PRESS = re.compile(
    r"(?:for|to)\s+(?P<label>[\w\s'/-]{2,40}?)[,\s]+(?:please\s+)?"
    r"(?:press|dial|enter)\s+(?P<digit>\d|star|hash|pound)", re.I)
_PRESS_THEN_FOR = re.compile(
    r"(?:press|dial|enter)\s+(?P<digit>\d|star|hash|pound)\s+"
    r"(?:for|to)\s+(?P<label>[\w\s'/-]{2,40})", re.I)
_HINGLISH = re.compile(
    r"(?P<label>[\w\s'/-]{2,40}?)\s+ke\s+liye\s+(?P<digit>\d)\s*dab", re.I)

_WORD_DIGIT = {"star": "*", "hash": "#", "pound": "#"}

STOPWORDS = {"the", "a", "an", "your", "our", "to", "for", "of", "and", "or",
             "please", "option", "options", "this", "these", "any", "all"}

ACTION_VERBS = re.compile(
    r"\b(confirm|cancel|authorise|authorize|accept|agree|pay|purchase|buy|"
    r"upgrade|subscribe|renew|delete|close your account)\b", re.I)
ABSORBING = re.compile(
    r"\b(leave a (message|voicemail)|voicemail|you will be disconnected|"
    r"call ?back later|goodbye)\b", re.I)


class Risk(str, Enum):
    SAFE_REVERSIBLE = "SAFE_REVERSIBLE"
    PROGRESS = "PROGRESS"
    ABSORBING = "ABSORBING"
    ACTION_VERB = "ACTION_VERB"


@dataclass(frozen=True)
class Option:
    digit: str
    label: str
    risk: Risk


def _risk(label: str) -> Risk:
    if ACTION_VERBS.search(label):
        return Risk.ACTION_VERB
    if ABSORBING.search(label):
        return Risk.ABSORBING
    if re.search(r"\b(repeat|again|main menu|more options|previous)\b", label, re.I):
        return Risk.SAFE_REVERSIBLE
    return Risk.PROGRESS


def parse_menu(prompt: str) -> tuple[Option, ...]:
    found: dict[str, str] = {}
    for rx in (_FOR_THEN_PRESS, _PRESS_THEN_FOR, _HINGLISH):
        for m in rx.finditer(prompt):
            d = m.group("digit").lower()
            digit = _WORD_DIGIT.get(d, d)
            label = re.sub(r"\s+", " ", m.group("label")).strip(" ,.")
            found.setdefault(digit, label)
    return tuple(Option(d, l, _risk(l)) for d, l in sorted(found.items()))


def _tokens(s: str) -> set[str]:
    return {w for w in re.sub(r"[^\w\s]", " ", s.lower()).split()
            if w and w not in STOPWORDS}


def _matches_any(word: str, pool: set[str]) -> bool:
    """Light stemming: 'orders' must match 'order', 'billing' must match 'bill'.

    A real stemmer is overkill for menu labels, which are two or three words of
    plain nouns, and it would add a dependency for no measurable gain.
    """
    for other in pool:
        if word == other:
            return True
        a, b = (word, other) if len(word) <= len(other) else (other, word)
        if len(a) >= 4 and b.startswith(a):
            return True
    return False


def choose(options: tuple[Option, ...], goal: str, *, margin: float = 0.15,
           heard_before: bool = False) -> tuple[str | None, str]:
    """Abstain unless one option wins by a margin. On a tie, prefer the
    reversible option -- preserving optionality beats guessing.

    `heard_before` says this exact menu has already been parsed. It gates the
    "repeat these options" escape hatch, because pressing repeat on a menu you
    have ALREADY HEARD cannot produce new options -- it replays a prompt whose
    every branch you have scored. Reversibility is worth something the first
    time and is a pure loop the second: on a real line each pointless replay
    costs the caller fifteen seconds of the same recording.
    """
    candidates = [o for o in options if o.risk not in
                  (Risk.ACTION_VERB, Risk.ABSORBING)]
    if not candidates:
        return None, "no safe option: every route is irreversible"

    g = _tokens(goal)
    scored = []
    for o in candidates:
        t = _tokens(o.label)
        overlap = (sum(1 for w in t if _matches_any(w, g)) / len(t)) if t else 0.0
        bonus = 0.35 if re.search(
            r"\b(agent|representative|advisor|speak to|customer (care|service))\b",
            o.label, re.I) else 0.0
        scored.append((overlap + bonus, o))
    scored.sort(key=lambda x: -x[0])

    top, best = scored[0]
    if top <= 0.0:
        rev = [o for _, o in scored if o.risk is Risk.SAFE_REVERSIBLE]
        if rev and not heard_before:
            return rev[0].digit, f"nothing matched; taking reversible '{rev[0].label}'"
        if rev:
            return None, ("nothing matched, and repeating a menu we have already "
                          "heard cannot reveal an option it did not have")
        return None, "nothing matched and no reversible option"
    if len(scored) > 1 and top - scored[1][0] < margin:
        return None, (f"'{best.label}' and '{scored[1][1].label}' are too close "
                      f"({top:.2f} vs {scored[1][0]:.2f})")
    return best.digit, f"heard '{best.label}' -> pressing {best.digit}"
