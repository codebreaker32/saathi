"""Words. The weakest family, and the one most people over-trust.

The entry worth reading is OPEN_OFFER, weighted at exactly zero. "How can I
help you today" feels like the strongest possible sign of a person and is
worth nothing, because every voice bot on earth opens with it. It is kept in
the table at zero rather than deleted, because the zero is the point.
"""

from __future__ import annotations

import re

from saathi.types import Family, SignalObservation

MENU_IMPERATIVE = re.compile(
    r"\b(press|dial)\s+(\d|star|pound|hash|zero)\b|\b(press or say|say or press)\b",
    re.I)
QUEUE_BOILERPLATE = re.compile(
    r"your call is important|estimated wait|all of our (representatives|agents|"
    r"advisors) are|currently (experiencing|busy)|you are (number|caller) \w+ in",
    re.I)
ASR_FAILURE = re.compile(
    r"i (didn'?t|did not) (catch|get|understand) that|sorry,? i didn'?t quite|"
    r"let'?s try that again|i'?m sorry,? i missed that", re.I)
SELF_ID_BOT = re.compile(
    r"\b(virtual (assistant|agent)|automated (assistant|system|service)|"
    r"i'?m an? (ai|bot)|digital assistant|this is an automated)\b", re.I)
OPEN_OFFER = re.compile(
    r"how (can|may) i help you|what can i do for you|how can i assist", re.I)

# name, pattern, family, llr, veto
TABLE = [
    ("self_id_automated", SELF_ID_BOT, Family.DISCLOSURE, -5.0, True),
    ("menu_imperative", MENU_IMPERATIVE, Family.REPETITION, -3.0, False),
    ("asr_failure_phrase", ASR_FAILURE, Family.REPETITION, -2.5, False),
    ("queue_boilerplate", QUEUE_BOILERPLATE, Family.REPETITION, -2.0, False),
    # Deliberately zero. Kept visible so nobody re-adds it as positive evidence.
    ("open_offer", OPEN_OFFER, Family.CONTINGENCY, 0.0, False),
]


def observe(text: str, t_ms: int) -> list[SignalObservation]:
    out = []
    for name, pattern, family, llr, veto in TABLE:
        if pattern.search(text):
            out.append(SignalObservation(name=name, family=family, llr=llr,
                                         t_ms=t_ms, veto=veto))
    return out
