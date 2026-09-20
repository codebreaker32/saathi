"""One sentence in, a playbook and the facts it needs out.

Grammar first, model second -- the same discipline as IVR navigation. Company
names and problem words are a small closed vocabulary, so matching them is
deterministic, instant and testable. A model is only worth spending on the
residue, and the residue here is usually nothing.

What this deliberately does NOT do is pull credentials out of the sentence.
Facts are matched against the playbook's SAFE_TO_STATE whitelist, so a card
number typed into the box cannot become a fact -- it falls through to the
problem text, where the scrubber removes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from saathi.playbook import build_brief, load_all
from saathi.types import Playbook

PROBLEM_WORDS = {
    "delivery_delay": ("late", "delay", "delayed", "hours late", "never arrived",
                       "still waiting", "not delivered"),
    "refund_missing": ("refund", "money back", "not refunded", "refund not",
                       "promised a refund"),
    "double_charge": ("twice", "double", "duplicate", "charged two", "two times"),
}

# The digit group must not END on a comma, or "order 4471, rs 499" matches
# "4471," followed by "rs" and the order number becomes the amount.
_NUM = r"\d{1,3}(?:,\d{2,3})*|\d+"
AMOUNT = re.compile(rf"(?:rs\.?|₹|inr)\s*({_NUM})|\b({_NUM})\s*(?:rupees|rs\b)", re.I)
ORDER = re.compile(r"\border(?:\s*(?:id|no|number|#))?\s*[:#]?\s*([A-Z0-9-]{4,16})\b", re.I)


@dataclass(frozen=True)
class Understanding:
    playbook: Playbook | None
    facts: dict[str, str]
    confidence: str          # high | low | none
    why: str


def _score(pb: Playbook, text: str) -> tuple[int, list[str]]:
    t = text.lower()
    hits, pts = [], 0
    if pb.company.lower().split()[0] in t:
        pts += 3
        hits.append(f"named {pb.company}")
    kind = pb.id.split(".", 1)[1]
    for w in PROBLEM_WORDS.get(kind, ()):
        if w in t:
            pts += 2
            hits.append(f"said '{w}'")
            break
    return pts, hits


def understand(text: str, playbooks: dict[str, Playbook] | None = None
               ) -> Understanding:
    pbs = playbooks or load_all()
    scored = sorted(((*_score(p, text), p) for p in pbs.values()),
                    key=lambda x: -x[0])
    top, hits, pb = scored[0]
    if top == 0:
        return Understanding(None, {}, "none",
                             "no playbook matched -- name the company and what went wrong")

    facts: dict[str, str] = {}
    safe = {f.field for f in pb.safe_fields()}
    if (m := ORDER.search(text)) and "order_id" in safe:
        facts["order_id"] = m.group(1)
    if (m := AMOUNT.search(text)) and "amount" in safe:
        facts["amount"] = (m.group(1) or m.group(2)).replace(",", "")

    runner_up = scored[1][0] if len(scored) > 1 else 0
    conf = "high" if top >= 5 and top > runner_up else "low"
    return Understanding(pb, facts, conf, ", ".join(hits))


def brief_for(u: Understanding, text: str, login_facts: dict[str, str]):
    """Whitelisted, scrubbed, and the only channel into the call."""
    if u.playbook is None:
        raise ValueError("no playbook")
    return build_brief(u.playbook, text, {**login_facts, **u.facts})
