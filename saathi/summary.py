"""What to tell the user once the call is over.

A dashboard, not a transcript. The person did not listen to the call; replaying
it to them is work, not an answer. What they want is four things: did it end,
what was offered, what Saathi did about it, and what is still open.

Three rules shape everything here.

  1. NEVER SAY "RECORDING". Three different numbers are in play and conflating
     them is a lie. real_rep is 11:37 on the line, about 50s of actual speech,
     and a few seconds of browser replay at speed. The call took 11:37; no
     recording of that length exists anywhere.

  2. NEVER INVENT AN ENDING. Most scenarios stop on a detector tick rather than
     a hangup. Saying "call ended" there asserts something nobody observed. If
     no terminal event was seen, the summary says the transcript stops here.

  3. NEVER IMPLY A SETTLEMENT IS REAL. The line is simulated. "accepted" means
     the mandate permitted it and Saathi said yes, not that money moved. That
     caveat renders every time, in the same register as the amber markers, and
     it is conditional on nothing.

Pure: no clock, no I/O. It reads an Outcome and returns dicts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class NextAction:
    """Something the user can do now that the call is over.

    EVERY FIELD IS A STRING, and that is load-bearing rather than incidental. A
    next action must not be able to carry an amount, an offer id or a mandate
    field, because the moment it can, a button becomes a way to grant authority
    the frozen mandate did not. prefill is a SENTENCE for the authoring box --
    it re-enters mandate.parse() and the read-back exactly like a sentence the
    user typed, so widening costs a deliberate confirmation and cannot happen as
    a side effect of a tap.
    """

    id: str
    label: str
    detail: str
    prefill: str = ""


def _mmss(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def timing(outcome, speech_ms: int | None = None) -> dict:
    """The clocks, kept apart and each named for what it is.

    THE HOLD FIGURE IS MEASURED, NOT DERIVED. It is accumulated on the call's
    own clock as the call sits in hold nodes. The tempting alternative --
    on_line minus rendered-audio length -- silently mixes two measurement
    systems: the scenario's speak_ms are estimates and real Polly runs longer,
    so on a short call the subtraction goes negative and reports a hold of
    0:00 on a call that demonstrably held. It was wrong by 6 seconds on a
    26-second call before this was measured properly.

    speech_ms stays None when nobody rendered the audio, and is reported only
    as itself -- it is never subtracted from anything.
    """
    on_line = getattr(outcome, "ended_at_ms", 0) or (
        outcome.beats[-1].t_ms if outcome.beats else 0)
    held = getattr(outcome, "hold_ms", 0)
    return {
        "on_line_ms": on_line,
        "on_line": _mmss(on_line),
        "speech_ms": speech_ms,
        "speech": _mmss(speech_ms) if speech_ms is not None else None,
        "waited_ms": held,
        "waited": _mmss(held),
        # The product's whole claim, as a number: time the user did not spend.
        "headline": (f"Saathi held for {_mmss(held)} so you did not have to"
                     if held > 0 else None),
    }


def conclude(outcome) -> dict:
    """The conclusion block. Every field is something that was observed."""
    offered = bool(outcome.offers)
    best = None
    if offered:
        priced = [o for o in outcome.offers if o.get("amount_paise") is not None]
        best = max(priced, key=lambda o: o["amount_paise"], default=outcome.offers[0])

    # A call "ended" only if something terminal was seen. Most scenarios stop on
    # a detector tick, which is not an ending and must not be rendered as one.
    ended = (outcome.handed_off_at_ms is not None
             or outcome.accepted is not None
             or bool(outcome.escalated))

    not_confirmed = [
        "The line is simulated. Nothing here contacted a real company.",
        "Saathi cannot confirm the company will do what it said on the call.",
    ]
    if not outcome.reference_number:
        not_confirmed.append(
            "No reference number was captured. Saathi hands the call over rather "
            "than write down a number it cannot tell apart from a one-time code."
        )

    return {
        "ended": ended,
        "ended_label": (
            "Call ended" if ended
            # Being rung and not answering is a DIFFERENT ending from taking
            # the call, and the summary is the only place the user finds out
            # which one happened to them.
            else "You didn't pick up" if getattr(outcome, "summon_unanswered", False)
            else "Handed to you" if outcome.fetched_at_ms is not None
            else "The transcript stops here"
        ),
        "ended_detail": (
            "" if ended
            else "A person answered and the call became yours. What happened after "
                 "that is not Saathi's to report."
            if outcome.fetched_at_ms is not None
            else "Saathi did not hear anyone hang up; the scenario simply runs out."
        ),
        "offer_made": offered,
        "offer_kind": (best or {}).get("kind") if offered else None,
        "offer_amount_paise": (best or {}).get("amount_paise") if offered else None,
        "accepted": outcome.accepted is not None,
        "escalated": bool(outcome.escalated),
        "escalated_why": outcome.escalated[0]["why"] if outcome.escalated else "",
        "reference_number": outcome.reference_number,
        "settled_alone": outcome.settled_alone,
        "summon_unanswered": getattr(outcome, "summon_unanswered", False),
        "callback_requested": getattr(outcome, "callback_requested", False),
        "handed_over": outcome.handed_off_at_ms is not None,
        "fetched": outcome.fetched_at_ms is not None,
        # Unconditional. Not a branch, not a flag anyone can turn off.
        "settlement_is_simulated": True,
        "not_confirmed": not_confirmed,
    }


def next_actions(outcome) -> list[dict]:
    """What the user can do now. Offered only where it is actually actionable.

    Note what is NOT here: a "continue the call" action. The call is over and
    the terminated latch is one-way, so a button promising to resume it would
    promise a line that no longer exists. Calling back is a new call, and the
    label says so.
    """
    acts: list[NextAction] = []

    if outcome.escalated:
        e = outcome.escalated[0]
        amt, kind = e.get("amount_paise"), e.get("kind", "offer")
        shown = f"Rs {amt / 100:.0f}" if amt is not None else "what they offered"
        acts.append(NextAction(
            id="widen_and_call_back",
            label=f"Call back and accept {shown}",
            detail="They offered less than you authorised, so Saathi did not take it. "
                   "This opens the permission screen with a wider sentence for you to "
                   "confirm. It does not widen anything on its own, and the sentence "
                   "REPLACES your current one rather than adding to it, so check the "
                   "read-back before placing the call.",
            prefill=(f"accept a {kind} of {amt / 100:.0f} or more"
                     if amt is not None else ""),
        ))
        acts.append(NextAction(
            id="leave_it",
            label="Leave it",
            detail="Nothing was accepted, so nothing is settled. The offer stands if "
                   "you call back yourself.",
        ))

    if outcome.settled_alone:
        acts.append(NextAction(
            id="done",
            label="Nothing to do",
            detail="Saathi settled this inside the authority you gave before the call. "
                   "You were never needed.",
        ))

    unanswered = getattr(outcome, "summon_unanswered", False)

    if unanswered:
        # A fetch means SUMMONED, not answered. Reporting "you took the call" to
        # someone who never picked it up would be the summary telling them their
        # own history wrong.
        acts.append(NextAction(
            id="missed_it",
            label="You missed this one",
            detail="Saathi found a person, rang you, and carried on when you did not "
                   "answer. Everything below is what happened while you were away.",
        ))
    elif outcome.fetched_at_ms is not None:
        acts.append(NextAction(
            id="took_over",
            label="You took the call",
            detail="Saathi found a person and handed the line to you. Anything agreed "
                   "after that point was agreed by you, so there is nothing here for "
                   "Saathi to report or to act on.",
        ))

    if outcome.callback_requested and not outcome.accepted:
        acts.append(NextAction(
            id="await_callback",
            label="Wait for their callback",
            detail="They said they would log it and call back. Saathi cannot confirm "
                   "they will, and there is no reference number to hold them to it, "
                   "so give it a day and call again if nothing arrives.",
        ))

    if outcome.handed_off_at_ms is not None and not outcome.offers:
        acts.append(NextAction(
            id="call_back",
            label="Call back yourself",
            detail="Saathi handed over because the line asked for something only you "
                   "can give. It cannot authenticate as you, by construction.",
        ))

    if not acts:
        acts.append(NextAction(
            id="retry",
            label="Try again",
            detail="Nobody was reached and nothing was offered.",
        ))
    return [asdict(a) for a in acts]
