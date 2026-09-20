"""What happens when Saathi rings you and you do not pick up.

The sequence: a human answers, Saathi summons the user AND explains the problem
at the same time (serialising them leaves a real person listening to silence),
and if nobody answers it states what was authorised, hears the agent out, and
records whatever they said.

THE ONE LINE THAT MUST NEVER BE SPOKEN is "my user isn't picking up" to an agent
who is already talking to that user. It is the sentence that makes the product
look broken to both people at once, and it is gated in the reducer on presence
rather than on timing -- a takeover a fraction of a second earlier silences it
by construction.
"""

import pytest

from saathi import clips
from saathi.session.machine import step
from saathi.session.states import SessionState
from saathi.simulate import load, run
from saathi.summary import conclude, next_actions
from saathi.types import (
    LineState, Mandate, Speak, SummonTimeout, UserArrived, UserPresence,
)

AUTHORITY = Mandate(accept_refund_min_paise=40_000)


def go(name, mandate=AUTHORITY):
    sc = load(name)
    return run(sc, sc.get("goal", ""), mandate=mandate)


def said(out):
    return " ".join(b.text for b in out.beats if b.actor == "saathi")


# --------------------------------------------------------------------------- #
# The conditional. This is the important part of the file.
# --------------------------------------------------------------------------- #

def _summoning(mandate=AUTHORITY):
    return SessionState(mandate=mandate, line=LineState.ENGAGED,
                        presence=UserPresence.SUMMONING)


def test_the_unavailable_line_is_spoken_when_nobody_answered():
    _, cmds = step(_summoning(), SummonTimeout(t_ms=1))
    assert any(isinstance(c, Speak) for c in cmds)


@pytest.mark.parametrize("presence", [
    UserPresence.IN_COMMAND, UserPresence.SPEAKING, UserPresence.LISTENING,
    UserPresence.AWAY, UserPresence.UNREACHABLE,
])
def test_it_is_never_spoken_once_the_user_is_on_the_line(presence):
    """Proves: the guard is on STATE, not on a timer, so no ordering of events
    can produce the sentence after a takeover.
    Does NOT prove: that the UI delivers the takeover promptly. A tap that never
    reaches the reducer is a transport problem, not covered here.
    """
    s = SessionState(mandate=AUTHORITY, line=LineState.ENGAGED, presence=presence)
    _, cmds = step(s, SummonTimeout(t_ms=1))
    assert cmds == [], f"spoke to an agent whose presence was {presence.value}"


def test_a_takeover_landing_first_silences_it():
    """The race, run in the order that matters: the user joins, THEN the timer
    fires. This is the sequence a slow network produces."""
    s = _summoning()
    s, _ = step(s, UserArrived(t_ms=1, mode="command"))
    _, cmds = step(s, SummonTimeout(t_ms=2))
    assert cmds == []


def test_a_second_timeout_cannot_speak_twice():
    """Idempotence. The first timeout moves presence to UNREACHABLE, so a
    duplicate event finds nothing to do."""
    s = _summoning()
    s, first = step(s, SummonTimeout(t_ms=1))
    _, second = step(s, SummonTimeout(t_ms=2))
    assert first and second == []


# --------------------------------------------------------------------------- #
# What gets said
# --------------------------------------------------------------------------- #

def test_the_rep_is_told_what_is_wanted_and_never_the_floor():
    """THE LEAK TEST, and it guards an invariant stated on Mandate itself:
    "What the agent may ACCEPT. Never what it may REVEAL -- that axis is zero."

    An earlier version filled this line from render(), the USER-facing read-back,
    and announced "accept a refund of Rs 4,000 or more; nothing else, and
    anything lower comes back to you" to the counterparty. Beyond the invariant,
    it guarantees an offer of exactly the floor and teaches the rep how to force
    an escalation.

    Proves: no digit from the mandate reaches the utterance, at any value.
    Does NOT prove: that the rep behaves better for not knowing. Nothing here
    models a negotiation.
    """
    for paise in (1, 40_000, 99_900, 400_000, 12_345_600):
        s = SessionState(mandate=Mandate(accept_refund_min_paise=paise),
                         line=LineState.ENGAGED, presence=UserPresence.SUMMONING)
        _, cmds = step(s, SummonTimeout(t_ms=1))
        text = [c.text for c in cmds if isinstance(c, Speak)][0]
        assert "isn't picking up" in text
        assert "refund" in text, "the ASK must still be stated"
        assert not any(ch.isdigit() for ch in text), f"leaked a figure: {text}"


def test_the_leak_test_has_teeth():
    """MUTATION CONTROL. render() is the read-back that DOES carry figures. If
    formatting the clip with it no longer trips the assertion above, that
    assertion has stopped guarding anything."""
    from saathi.mandate import render
    leaky = clips.STATE_DEMAND.replace("{wanted}", "{demand}").format(
        demand=render(Mandate(accept_refund_min_paise=400_000)))
    assert any(ch.isdigit() for ch in leaky)


def test_what_the_user_sees_still_carries_the_figures():
    """The read-back must NOT be weakened by fixing the leak: the user has to
    see the numbers they are granting."""
    from saathi.mandate import render
    assert "400" in render(Mandate(accept_refund_min_paise=40_000))


def test_with_no_authority_it_only_asks_for_a_callback():
    """Nothing to negotiate, so the honest move is to ask them to call back."""
    _, cmds = step(_summoning(Mandate.empty()), SummonTimeout(t_ms=1))
    text = [c.text for c in cmds if isinstance(c, Speak)][0]
    assert text == clips.CALLBACK_REQUEST


@pytest.mark.parametrize("mandate", [AUTHORITY, Mandate.empty()])
def test_a_callback_is_requested_whether_or_not_authority_was_granted(mandate):
    """Missing the call is the trigger, not the absence of a mandate.

    An earlier version raised the callback only on the no-authority branch, so
    it was skipped on exactly the calls where the user had engaged most -- they
    granted authority, missed the call, and got no callback arranged.
    """
    _, cmds = step(_summoning(mandate), SummonTimeout(t_ms=1))
    assert any(type(c).__name__ == "CaptureCallback" for c in cmds)


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #

def test_the_agent_answers_the_demand_and_it_is_recorded():
    out = go("real_rep")
    assert out.summon_unanswered
    assert "isn't picking up" in said(out)
    assert out.accepted and out.accepted["amount_paise"] == 45_000


def test_an_agent_who_cannot_authorise_offers_a_callback():
    out = go("terse_rep")
    assert out.summon_unanswered and out.callback_requested
    assert not out.accepted
    kinds = [n.kind for n in out.notes]
    assert "callback" in kinds


def test_the_summary_does_not_claim_you_took_a_call_you_missed():
    """A fetch means SUMMONED, not answered. Telling someone they took a call
    they slept through is the summary getting their own history wrong."""
    ids = [a["id"] for a in next_actions(go("real_rep"))]
    assert "missed_it" in ids and "took_over" not in ids


def test_the_ending_is_labelled_for_what_happened():
    assert conclude(go("terse_rep"))["ended_label"] == "You didn't pick up"


def test_a_bot_call_never_reaches_the_demand():
    """No summon, no timeout, nothing said. The detector holding the line is
    the whole product; this checks the new path cannot undo it."""
    for name in ("voicebot_warm", "voicebot_disclosed", "ivr_only"):
        out = go(name)
        assert not out.summon_unanswered, f"{name} summoned on a machine"
        assert "isn't picking up" not in said(out)
