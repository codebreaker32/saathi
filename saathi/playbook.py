"""Playbook loading, and the brief that crosses the air gap.

The brief is the only thing that reaches the call worker. It is built from a
whitelist, not by filtering a larger object, because a filter that is wrong
leaks and a whitelist that is wrong merely omits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from saathi.types import FactSpec, Playbook, Tag

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent / "playbooks"


def load_all(directory: Path | None = None) -> dict[str, Playbook]:
    d = directory or PLAYBOOK_DIR
    out: dict[str, Playbook] = {}
    for path in sorted(d.glob("*.yaml")):
        pb = _from_dict(yaml.safe_load(path.read_text()))
        out[pb.id] = pb
    return out


def _from_dict(raw: dict) -> Playbook:
    facts = tuple(
        FactSpec(field=f["field"], tag=Tag(f["tag"]), source=f.get("from", "user"))
        for f in raw["facts_required"]
    )
    return Playbook(
        id=raw["id"],
        company=raw["company"],
        line=raw["line"],
        problem=raw["problem"],
        goal=raw["goal"],
        lookup_key=raw["lookup_key"],
        facts_required=facts,
        ivr_path=tuple(str(d) for d in raw.get("ivr_path", [])),
        expected_asks=tuple(raw.get("expected_asks", [])),
        offer_options=tuple(raw.get("offer_options", [])),
        success=tuple(raw.get("success", [])),
    )


# --------------------------------------------------------------------------- #
# The air gap
# --------------------------------------------------------------------------- #

# The free-text box is the leak channel, because someone will eventually type
# their DOB into it. Redact on construction and tell the user what was removed.
#
# Matching runs on a CLUSTER, not on raw adjacency. The first version of this
# used \b\d{12,19}\b, which catches a card number written the one way the tests
# happened to write it and misses 4111-1111-1111-1111 entirely -- i.e. the way
# people actually write them. A separator-tolerant scan is the difference
# between a scrubber and a scrubber-shaped object.
_CLUSTER = re.compile(r"\d(?:[\s.\-_/]?\d)*")
LONG_NUMBER_MIN_DIGITS = 12   # cards, Aadhaar, account numbers
LONG_NUMBER_MAX_DIGITS = 19

_PII_PATTERNS = [
    # dates, any common separator
    (re.compile(r"\b\d{1,2}[\s./-]\d{1,2}[\s./-]\d{2,4}\b"), "[date removed]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN removed]"),
    (re.compile(r"\b(cvv|otp|pin|password|passcode)\b[:\s]*\S+", re.I),
     "[credential removed]"),
]


class BriefError(Exception):
    pass


@dataclass(frozen=True)
class CallBrief:
    """Frozen, whitelisted. NEVER-tagged fields are absent, not masked."""

    playbook_id: str
    company: str
    line: str
    goal: str
    problem_text: str
    facts: dict[str, str]
    redactions: tuple[str, ...] = ()

    def contains(self, needle: str) -> bool:
        blob = " ".join([self.problem_text, *self.facts.values()])
        return needle.lower() in blob.lower()


def scrub(text: str) -> tuple[str, list[str]]:
    """Returns the cleaned text and a list of what was taken out.

    The list is not cosmetic: an empty list tells the user nothing was removed,
    so a scrubber that silently misses is worse than one that is absent.
    """
    removed: list[str] = []
    for pattern, label in _PII_PATTERNS:
        if pattern.search(text):
            removed.append(label)
            text = pattern.sub(label, text)

    # Long numeric runs, tolerant of the separators people actually type.
    out, last = [], 0
    for m in _CLUSTER.finditer(text):
        digits = sum(c.isdigit() for c in m.group())
        if LONG_NUMBER_MIN_DIGITS <= digits <= LONG_NUMBER_MAX_DIGITS:
            out.append(text[last:m.start()])
            out.append("[long number removed]")
            last = m.end()
            if "[long number removed]" not in removed:
                removed.append("[long number removed]")
    out.append(text[last:])
    return "".join(out), removed


def build_brief(pb: Playbook, problem_text: str, facts: dict[str, str]) -> CallBrief:
    """Whitelist by tag. A NEVER field offered here is a caller bug, so raise.

    This is what keeps "I don't have that" a true statement during the call
    rather than a refusal the model might reconsider.
    """
    never = {f.field for f in pb.never_fields()}
    offered = set(facts)
    if offered & never:
        raise BriefError(
            f"refusing to brief NEVER-tagged fields: {sorted(offered & never)}"
        )
    safe = {f.field for f in pb.safe_fields()}
    cleaned_text, removed = scrub(problem_text)
    kept: dict[str, str] = {}
    for k, v in facts.items():
        if k not in safe:
            continue
        v2, r = scrub(str(v))
        removed.extend(r)
        kept[k] = v2
    return CallBrief(
        playbook_id=pb.id,
        company=pb.company,
        line=pb.line,
        goal=pb.goal,
        problem_text=cleaned_text,
        facts=kept,
        redactions=tuple(removed),
    )
