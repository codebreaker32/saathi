"""The offer path and the post-call dashboard.

The enforcement half of this was already built and tested -- permits(),
accept_offer() and the reducer's escalation. What was missing was that no call
ever exercised it: every scenario stopped before anyone offered anything, so a
green suite said nothing about whether the mandate did any work during a call.
These tests are about the wiring, not the arithmetic.
"""

import dataclasses

import pytest

from saathi.mandate import permits
from saathi.simulate import load, run
from saathi.summary import NextAction, conclude, next_actions, timing
from saathi.types import Mandate

REFUND_MIN_400 = Mandate(accept_refund_min_paise=40_000)


def go(name, mandate=REFUND_MIN_400):
    sc = load(name)
    return run(sc, sc.get("goal", ""), mandate=mandate)


# --------------------------------------------------------------------------- #
# The call that finishes alone
# --------------------------------------------------------------------------- #

def test_an_offer_inside_the_mandate_is_settled_without_the_user():
    """Proves: the grant authored before the call is what lets Saathi finish it,
    and that the user is not summoned when it does.
    Does NOT prove: that any money moved. The line is simulated and the summary
    says so unconditionally.
    """
    o = go("bot_settles_refund")
    assert o.accepted and o.accepted["amount_paise"] == 45_000
    assert o.settled_alone
    assert o.fetched_at_ms is None, "a disclosed bot must never fetch the user"
    assert o.handed_off_at_ms is None


def test_the_same_call_needs_you_when_the_offer_is_worse():
    """Identical machinery, worse offer. Proves the refusal comes from the
    mandate rather than from the scenario, because only the figure differs.
    Does NOT prove: that a real rep would accept being refused.
    """
    o = go("offer_below_mandate")
    assert o.accepted is None
    assert o.escalated and o.escalated[0]["amount_paise"] == 15_000
    assert not o.settled_alone
    assert any("summoning the user" in b.text for b in o.beats), \
        "an offer outside the grant must reach the user, not be dropped"


def test_an_empty_mandate_settles_nothing():
    """The default. Proves authority has to be granted rather than assumed."""
    o = go("bot_settles_refund", mandate=Mandate.empty())
    assert o.accepted is None and o.escalated


# --------------------------------------------------------------------------- #
# Floors, not caps -- with the control that can detect the inversion
# --------------------------------------------------------------------------- #

def test_the_bound_is_a_floor_not_a_cap():
    """MUTATION CONTROL for an inversion that is otherwise invisible.

    Asserting only that 450 is accepted would pass under BOTH readings, which
    is why both directions are here. Under a cap, 150 would clear (it is "under
    budget") and 450 would fail -- precisely backwards, since the money is being
    offered TO the user.
    """
    assert permits(REFUND_MIN_400, "refund", 45_000)
    assert not permits(REFUND_MIN_400, "refund", 15_000)


def test_a_worse_offer_cannot_become_acceptable_by_being_worse():
    """The end-to-end form of the same control, through a whole call."""
    good, bad = go("bot_settles_refund"), go("offer_below_mandate")
    assert good.accepted is not None
    assert bad.accepted is None


# --------------------------------------------------------------------------- #
# A button must not be a grant
# --------------------------------------------------------------------------- #

def test_a_next_action_cannot_carry_authority():
    """Proves: the next-action type is structurally incapable of carrying an
    amount, an offer id or a mandate field, so no UI affordance can widen the
    frozen mandate. It hands back a SENTENCE, which re-enters parse() and the
    read-back exactly like a sentence the user typed.
    Does NOT prove: that the UI actually routes the sentence through the
    read-back -- that is page.tsx's job and is not covered here.
    """
    banned = ("amount", "paise", "mandate", "grant", "authoris", "authoriz")
    for f in dataclasses.fields(NextAction):
        assert f.type in (str, "str"), f"{f.name} is not a plain string"
        assert not any(b in f.name.lower() for b in banned), \
            f"{f.name} could carry authority"


def test_the_widening_action_offers_a_sentence_not_a_field():
    acts = next_actions(go("offer_below_mandate"))
    widen = [a for a in acts if a["id"] == "widen_and_call_back"][0]
    assert widen["prefill"] == "accept a refund of 150 or more"
    assert all(isinstance(v, str) for v in widen.values())


def test_no_action_claims_to_resume_a_finished_call():
    """The terminated latch is one-way, so a "continue the call" button would
    promise a line that no longer exists."""
    for name in ("bot_settles_refund", "offer_below_mandate", "real_rep", "ivr_only"):
        for a in next_actions(go(name)):
            assert "continue the call" not in a["label"].lower()
            assert "resume" not in a["label"].lower()


# --------------------------------------------------------------------------- #
# The summary must not overstate
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["ivr_only", "recorded_human", "voicebot_warm"])
def test_a_call_nobody_ended_is_not_reported_as_ended(name):
    """Most scenarios stop on a detector tick. Calling that "call ended" would
    assert something nobody observed."""
    c = conclude(go(name))
    assert not c["ended"]
    assert c["ended_label"] == "The transcript stops here"


def test_the_simulated_caveat_is_not_conditional():
    for name in ("bot_settles_refund", "offer_below_mandate", "real_rep"):
        c = conclude(go(name))
        assert c["settlement_is_simulated"] is True
        assert any("simulated" in line for line in c["not_confirmed"])


def test_speech_is_reported_only_as_itself_and_never_subtracted():
    """Proves: hold is MEASURED on the call clock, so it does not depend on
    whether anyone rendered the audio -- and speech, which is measured on a
    different system, is reported as itself or not at all.

    This is the contract that replaced the bug. Hold used to be
    on_line MINUS rendered-audio-length, which mixed the scenario's estimated
    speak_ms with real Polly output; on offer_below_mandate that produced 31s
    of speech inside a 26s call and a hold of 0:00.

    Does NOT prove: that the simulated hold durations resemble a real queue.
    """
    o = go("real_rep")
    unmeasured, measured = timing(o), timing(o, speech_ms=50_000)
    assert unmeasured["speech"] is None
    assert measured["speech"] == "0:50"
    # The hold figure is identical either way: it is not derived from speech.
    assert unmeasured["waited_ms"] == measured["waited_ms"] == o.hold_ms
    assert o.hold_ms > 0


def test_call_length_comes_from_the_clock_not_the_last_beat():
    """REGRESSION. A hold node writes its "(hold music)" beat when the hold
    BEGINS and then advances the clock, so a call that ends while still holding
    has a last beat that predates the end. ivr_only is the case that exposes it:
    last beat 20:13, real end 25:13 -- five minutes of waiting silently deleted
    from the one number the product exists to absorb.

    Proves: the reported length is the full time on the line.
    Does NOT prove: that these simulated hold durations resemble a real queue.
    """
    o = go("ivr_only")
    assert o.beats[-1].t_ms < o.ended_at_ms
    assert o.ended_at_ms - o.beats[-1].t_ms == 300_000
    assert timing(o)["on_line"] == "25:13"


@pytest.mark.parametrize("name", ["ivr_only", "real_rep", "menu_loop",
                                  "offer_below_mandate", "bot_settles_refund"])
def test_hold_can_never_exceed_the_call(name):
    """The arithmetic sanity check that caught the original bug. Hold was
    derived by subtracting RENDERED AUDIO length from the SCENARIO CLOCK -- two
    different measurement systems -- which on a 26-second call produced a
    31-second speech figure and a hold of 0:00. Both numbers now come from the
    same clock.
    """
    t = timing(go(name))
    assert 0 <= t["waited_ms"] <= t["on_line_ms"]


def test_the_summary_never_calls_the_call_a_recording():
    """11:37 is the call clock. The audio is about 50 seconds. Saying
    "recorded audio 11:37" would be wrong by an order of magnitude."""
    o = go("real_rep")
    blob = repr(conclude(o)) + repr(timing(o, 50_000)) + repr(next_actions(o))
    assert "recording" not in blob.lower()
    assert "recorded audio" not in blob.lower()


# --------------------------------------------------------------------------- #
# Recorded as it happened, not reconstructed
# --------------------------------------------------------------------------- #

def test_a_refused_offer_survives_in_the_notes():
    """The reason the notes are written DURING the call rather than assembled
    afterwards. Nothing the other party said records the refusal -- it is
    Saathi's own decision -- so a summary rebuilt by re-reading the transcript
    would lose exactly the case the user most needs to see.
    """
    o = go("offer_below_mandate")
    kinds = [n.kind for n in o.notes]
    assert "offer" in kinds and "escalated" in kinds

    said_by_them = " ".join(b.text for b in o.beats if b.actor == "them")
    assert "150" in said_by_them, "the offer itself was spoken"
    assert "not authorised" not in said_by_them, \
        "but the refusal was never spoken, so it exists only because it was noted"


def test_notes_carry_the_time_they_happened():
    o = go("bot_settles_refund")
    assert o.notes and all(n.t_ms > 0 for n in o.notes)
    assert [n.t_ms for n in o.notes] == sorted(n.t_ms for n in o.notes)
