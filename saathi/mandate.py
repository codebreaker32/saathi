"""What the agent may ACCEPT -- parsed once, frozen, then enforced as fields.

The boundary this file exists to defend: a rep saying "store credit is
basically a refund, though, right?" is a plausible reading of an English
sentence and an easy way to talk a model into agreeing. It is not a plausible
edit to accept_credit_max_paise=None.

So the authoring sentence is parsed exactly once, before the call, and does not
travel with the session. Mid-call there is nothing to re-interpret -- only
fields to check. A sentence can be stretched; a field cannot be moved.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from saathi.types import LLMClient, Mandate

# Offer kinds that have a representation. There is deliberately no wildcard:
# an offer we cannot name is an offer we cannot accept.
KINDS = ("refund", "redelivery", "credit", "replacement")

_FLOORED = {"refund": "accept_refund_min_paise",
            "credit": "accept_credit_min_paise"}
_BOOLEAN = {"redelivery": "accept_redelivery", "replacement": "accept_replacement"}


class MandateError(Exception):
    """Raised by accept_offer when the grant is absent or too small."""


@dataclass(frozen=True)
class ParseResult:
    mandate: Mandate | None = None
    needs: tuple[str, ...] = ()      # questions the user must answer first
    error: str | None = None         # cannot be represented at all

    @property
    def ok(self) -> bool:
        return self.mandate is not None and not self.needs and not self.error


# --------------------------------------------------------------------------- #
# Enforcement -- the only thing that runs during a call
# --------------------------------------------------------------------------- #


def permits(m: Mandate, kind: str, amount_paise: int | None) -> bool:
    if kind in _BOOLEAN:
        return bool(getattr(m, _BOOLEAN[kind]))
    if kind in _FLOORED:
        floor = getattr(m, _FLOORED[kind])
        if floor is None:
            return False
        if amount_paise is None:
            return False   # an unstated amount cannot be shown to clear the floor
        return amount_paise >= floor
    return False                   # unknown kind: no representation, no grant


def accept_offer(m: Mandate, kind: str, amount_paise: int | None) -> dict:
    """The agent's tool. It may call this whenever it likes and be refused.

    The model is never in a position to decide it is fine just this once,
    because the decision is not the model's to make -- it is a field check.
    """
    if not permits(m, kind, amount_paise):
        floor = getattr(m, _FLOORED.get(kind, ""), None) if kind in _FLOORED else None
        detail = f" of {amount_paise/100:.2f}" if amount_paise is not None else ""
        why = (f"; they offered less than the {floor/100:.0f} you set"
               if floor is not None and amount_paise is not None
               and amount_paise < floor else "")
        raise MandateError(
            f"not authorised to accept {kind}{detail}{why}. "
            f"Escalate to the user instead."
        )
    return {"accepted": True, "kind": kind, "amount_paise": amount_paise}


# --------------------------------------------------------------------------- #
# Read-back -- step 3 of authoring, and load-bearing
# --------------------------------------------------------------------------- #


def render(m: Mandate) -> str:
    """Plain words, so unbounded authority is never granted by a missing number."""
    if m.is_empty():
        return "nothing -- it will explain the problem and ask them to call you back"
    parts = []
    if m.accept_refund_min_paise is not None:
        parts.append("accept a refund of Rs "
                     f"{m.accept_refund_min_paise/100:,.0f} or more")
    if m.accept_credit_min_paise is not None:
        parts.append("accept a credit of Rs "
                     f"{m.accept_credit_min_paise/100:,.0f} or more")
    if m.accept_redelivery:
        parts.append("accept a redelivery")
    if m.accept_replacement:
        parts.append("accept a replacement")
    return ("; ".join(parts)
            + "; nothing else, and anything lower comes back to you")


_WANTED = (
    ("accept_refund_min_paise", "a refund"),
    ("accept_credit_min_paise", "store credit"),
    ("accept_redelivery", "a redelivery"),
    ("accept_replacement", "a replacement"),
)


def wanted(m: Mandate) -> str:
    """WHAT the user is asking for. NEVER what they would settle for.

    render() is the read-back: it exists to show the USER what they authorised,
    and it names the floors. This is the opposite direction -- it is the only
    thing that may be said to the counterparty, so it carries KINDS and no
    amounts at all.

    The distinction is the invariant on Mandate itself: "What the agent may
    ACCEPT. Never what it may REVEAL -- that axis is zero." Announcing a floor
    also guarantees you are offered exactly the floor, and "anything lower
    comes back to you" would teach a rep how to force an escalation.

    Bounded output by construction: four kinds, so at most fifteen sentences,
    which is a key space the audio cache can actually hold. An amount would
    make it unbounded and every such line would be silent.
    """
    parts = [label for field, label in _WANTED if getattr(m, field, None)]
    if not parts:
        return "the problem put right"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " or " + parts[-1]


# --------------------------------------------------------------------------- #
# Authoring-time parse -- runs once, never during a call
# --------------------------------------------------------------------------- #

_SCHEMA = {
    "name": "emit_mandate",
    "description": (
        "Convert an authorisation sentence into permission fields. Amounts are "
        "MINIMUMS the user is willing to settle for -- money is being offered TO "
        "them, so a smaller offer is a worse one. Use null for a floor the user "
        "did not state; never invent one, and never treat an unstated floor as "
        "'accept any amount'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "refund": {"type": ["integer", "null"],
                       "description": ("smallest refund in paise the user would "
                                       "accept without being asked; null if not "
                                       "stated")},
            "refund_mentioned": {"type": "boolean"},
            "credit": {"type": ["integer", "null"]},
            "credit_mentioned": {"type": "boolean"},
            "redelivery": {"type": "boolean"},
            "replacement": {"type": "boolean"},
            "unrepresentable": {
                "type": "boolean",
                "description": (
                    "true if the sentence grants open-ended discretion such as "
                    "'whatever is reasonable' or 'use your judgement'"
                ),
            },
        },
        "required": ["refund_mentioned", "credit_mentioned", "redelivery",
                     "replacement", "unrepresentable"],
    },
}

# Deliberately broad. A false positive costs one clarifying question; a false
# negative silently grants authority the user did not mean to give.
_VAGUE = re.compile(
    r"\b(whatever|anything|reasonab\w*|sensib\w*|appropriate|best|usual|"
    r"norm(al|ally)|discretion|judg\w*|as you see fit|up to you|you decide|"
    r"sort(ed)? out|handle it)\b",
    re.I,
)


def _grounded_in(text: str, paise: int | None) -> bool:
    """Does this figure actually appear in what the user wrote?

    The parser must not trust the model to signal "no amount stated" by
    returning null. A small local model asked for an integer will happily
    invent one -- measured: "accept a refund" came back as 100 paise, which
    renders as "a refund of Rs 1 or more" and is therefore authority to accept
    almost anything. The read-back would have shown it, but a grant that only
    a careful reader catches is a grant waiting to be waved through.

    So the extraction is checked against the source. A number that is not in
    the sentence cannot end up in the mandate, whatever the model returns.
    """
    if paise is None:
        return True
    written = {n.replace(",", "") for n in re.findall(r"\d[\d,]*", text)}
    rupees = paise // 100
    return bool(written & {str(rupees), str(paise), f"{rupees}"})


def parse(text: str, llm: LLMClient) -> ParseResult:
    """Free text -> fields. Ambiguity resolves toward LESS authority, always.

    Two failure modes are handled explicitly rather than guessed at:
      - open-ended discretion has no field representation, so it is an error
      - a stated grant with no cap asks for one rather than emitting unlimited
    """
    if not text or not text.strip():
        return ParseResult(mandate=Mandate.empty())

    if _VAGUE.search(text):
        return ParseResult(
            error="'{}' grants open-ended discretion, which has no representation "
                  "as a permission. Say what it may accept, and up to how much."
                  .format(text.strip())
        )

    raw, _ = llm.chat(
        messages=[{
            "role": "user",
            "content": (
                "Convert this authorisation into permission fields. Amounts "
                "are the SMALLEST offer the user would accept unasked. Do not "
                "infer a figure they did not state.\n\n" + text
            ),
        }],
        tools=[_SCHEMA],
        temperature=0.0,
    )
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ParseResult(error="could not read that as a set of permissions")

    if d.get("unrepresentable"):
        return ParseResult(
            error="that grants open-ended discretion. Say what it may accept, "
                  "and up to how much."
        )

    # Drop any figure the user did not actually write, so the checks below
    # treat a hallucinated amount exactly like a missing one.
    for key in ("refund", "credit"):
        if not _grounded_in(text, d.get(key)):
            d[key] = None

    needs: list[str] = []
    if d.get("refund_mentioned") and d.get("refund") is None:
        needs.append("You said it may accept a refund, but not how small an "
                     "offer is too small. What is the least you would settle "
                     "for without being asked?")
    if d.get("credit_mentioned") and d.get("credit") is None:
        needs.append("You said it may accept a credit, but not how small an "
                     "offer is too small. What is the least you would settle "
                     "for without being asked?")
    if needs:
        return ParseResult(needs=tuple(needs))

    return ParseResult(mandate=Mandate(
        accept_refund_min_paise=d.get("refund") if d.get("refund_mentioned") else None,
        accept_credit_min_paise=d.get("credit") if d.get("credit_mentioned") else None,
        accept_redelivery=bool(d.get("redelivery")),
        accept_replacement=bool(d.get("replacement")),
    ))
