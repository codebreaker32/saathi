"""Did their reply depend on what WE said?

This is the one behavioural question worth asking, and it is deliberately not
"does this sound human". Warmth, a first name and an open question are what
every voice bot produces; weighing them is how you build a detector that
confidently fetches you to talk to a robot.

The cheap tier looks for a PLANTED REFERENT -- an order number, an amount, a
date that we stated and no script could have anticipated. Checking for one
specific rare token sidesteps stopword lists entirely: a number is not a
function word in any language.

The expensive tier is an LLM judge, asked for an ordinal rather than a
probability, and asked about information flow rather than humanness.
"""

from __future__ import annotations

import json
import re

from saathi.types import Family, LLMClient, SignalObservation

BINDS_LLR = 1.4
GROUNDED_LLR = 1.5
SCRIPT_CONTINUATION_LLR = -1.5

# Below this, there is nothing to judge either way -- which is the signal to
# probe rather than to score. "Yeah?" is not evidence of anything.
MIN_JUDGEABLE_TOKENS = 5

_JUDGE_SCHEMA = {
    "name": "rate_binding",
    "description": "How much of their reply could only exist because of what we said?",
    "input_schema": {
        "type": "object",
        "properties": {
            "binding": {"type": "string",
                        "enum": ["none", "generic", "specific", "grounded"]},
            "quote": {"type": "string"},
        },
        "required": ["binding"],
    },
}

_ORDINAL = {"none": SCRIPT_CONTINUATION_LLR, "generic": 0.0,
            "specific": BINDS_LLR, "grounded": GROUNDED_LLR}


def plant(text: str) -> set[str]:
    """Tokens from our own utterance that a script cannot have guessed."""
    return {t for t in re.findall(r"\b\d[\d,]{2,}\b|\b[A-Z]{2,}\d+\b", text)}


def tokens(text: str) -> int:
    return len(re.sub(r"[^\w\s]", " ", text).split())


def judgeable(text: str) -> bool:
    return tokens(text) >= MIN_JUDGEABLE_TOKENS


def observe(reply: str, planted: set[str], t_ms: int) -> list[SignalObservation]:
    """Cheap tier. Returns NOTHING when there is nothing to judge.

    Returning nothing is the important case: it is what leaves the score in
    the undecided band, which is what triggers a probe. Scoring a terse reply
    either way would be inventing evidence.
    """
    norm = re.sub(r"[^\w\s]", " ", reply)
    if planted and any(p.replace(",", "") in norm.replace(",", "") for p in planted):
        return [SignalObservation(name="binds_planted_referent",
                                  family=Family.CONTINGENCY, llr=BINDS_LLR,
                                  t_ms=t_ms)]
    if not judgeable(reply):
        return []
    return []


def judge(reply: str, ours: str, llm: LLMClient, t_ms: int
          ) -> list[SignalObservation]:
    """Expensive tier. Only worth spending in the undecided band."""
    if not judgeable(reply):
        return []
    raw, _ = llm.chat(
        messages=[{"role": "user", "content":
                   "We said:\n" + ours + "\n\nThey replied:\n" + reply +
                   "\n\nDoes their reply contain information that could only be "
                   "produced by processing our specific content? Do not consider "
                   "whether it sounds human."}],
        tools=[_JUDGE_SCHEMA], temperature=0.0)
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    llr = _ORDINAL.get(d.get("binding", "generic"), 0.0)
    if llr == 0.0:
        return []
    return [SignalObservation(name=f"judge:{d.get('binding')}",
                              family=Family.CONTINGENCY, llr=llr, t_ms=t_ms)]
