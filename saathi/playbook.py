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
_PII_PATTERNS = [
    (re.compile(r"\b\d{2}[/-]\d{2}[/-]\d{2,4}\b"), "[date removed]"),
    (re.compile(r"\b\d{12,19}\b"), "[long number removed]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN removed]"),
    (re.compile(r"\b(cvv|otp|pin|password)\b[:\s]*\S+", re.I), "[credential removed]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[Aadhaar removed]"),
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
    removed: list[str] = []
    for pattern, label in _PII_PATTERNS:
        if pattern.search(text):
            removed.append(label)
            text = pattern.sub(label, text)
    return text, removed


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
