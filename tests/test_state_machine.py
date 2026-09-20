"""The reducer: total, pure, and disclosure-first."""

import dataclasses

import pytest

from saathi.session.machine import step
from saathi.session.states import SessionState
from saathi.types import (
    Answered, Dialed, EndReason, Event, Floor, HoldMusicDetected, HumanDetected,
    LineState, RemoteHungUp, RemoteUtterance, Ringing, SetFloor, Speak,
    StopSpeaking, SummonTimeout, SummonUser, TransferSignature, UserArrived,
    UserOverruled, UserPresence, VerifyAsk,
)
from saathi import clips


def drive(events, state=None):
    s = state or SessionState()
    out = []
    for e in events:
        s, cmds = step(s, e)
        out.extend(cmds)
    return s, out


def to_human(t0=0):
    return [Dialed(t_ms=t0), Ringing(t_ms=t0 + 100), Answered(t_ms=t0 + 6000),
            HoldMusicDetected(t_ms=t0 + 8000),
            RemoteUtterance(t_ms=t0 + 600_000, text="Billing, this is Priya.",
                            t_end_ms=t0 + 602_000)]


def test_reducer_never_raises_on_unknown_event():
    s = SessionState()
    s2, cmds = step(s, Event(kind="something_we_never_defined", t_ms=1))
    assert s2 is s and cmds == []


def test_reducer_is_pure_state_is_never_mutated():
    s = SessionState()
    before = dataclasses.asdict(s)
    step(s, Dialed(t_ms=0))
    assert dataclasses.asdict(s) == before


def test_happy_path_reaches_engaged():
    s, cmds = drive(to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C"))])
    assert s.line is LineState.ENGAGED
    assert any(isinstance(c, SummonUser) for c in cmds)


def test_first_agent_audio_is_disclosure():
    """Every engagement, unconditionally, before anything else is said."""
    _, cmds = drive(to_human())
    speaks = [c for c in cmds if isinstance(c, Speak)]
    assert speaks, "the agent must speak on meeting a party"
    assert speaks[0].purpose == "disclosure"
    assert speaks[0].text == clips.DISCLOSURE


def test_transfer_redistributes_disclosure_to_the_second_rep():
    s, _ = drive(to_human())
    assert s.disclosed()
    s, _ = step(s, TransferSignature(t_ms=700_000))
    assert not s.disclosed(), "a new party has not been disclosed to"
    s, cmds = step(s, Answered(t_ms=701_000))
    s, cmds = step(s, RemoteUtterance(t_ms=702_000, text="Hi, escalations.",
                                      t_end_ms=703_000))
    assert [c for c in cmds if isinstance(c, Speak)][0].purpose == "disclosure"


def test_verify_ask_always_hands_over_and_latches():
    s, _ = drive(to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C")),
                               UserArrived(t_ms=620_000, mode="join")])
    s, cmds = step(s, VerifyAsk(t_ms=640_000, trigger="last four"))
    assert s.latches.handoff_required
    assert s.floor is Floor.USER
    assert isinstance(cmds[0], StopSpeaking), "must stop mid-word, not finish the sentence"
    assert cmds[1].text == clips.VERIFY_HANDOFF


def test_verify_ask_with_user_away_summons_at_max_urgency():
    s, _ = drive(to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C"))])
    s = dataclasses.replace(s, presence=UserPresence.AWAY)
    s, cmds = step(s, VerifyAsk(t_ms=640_000, trigger="otp"))
    summons = [c for c in cmds if isinstance(c, SummonUser)]
    assert summons and summons[0].urgency == "max"


def test_overrule_stops_mid_word_and_never_rings_again():
    s, _ = drive(to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C"))])
    s, cmds = step(s, UserOverruled(t_ms=630_000))
    assert isinstance(cmds[0], StopSpeaking)
    assert s.latches.detector_demoted
    assert s.line is LineState.HOLD and s.presence is UserPresence.AWAY

    # A later detection must not ring them a second time on this session.
    s, cmds = step(s, RemoteUtterance(t_ms=900_000, text="Hello, billing.",
                                      t_end_ms=901_000))
    s, cmds = step(s, HumanDetected(t_ms=902_000, families=("A", "B", "C")))
    assert not [c for c in cmds if isinstance(c, SummonUser)]


def test_take_over_revokes_agent_floor():
    s, _ = drive(to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C"))])
    s, cmds = step(s, UserArrived(t_ms=620_000, mode="command"))
    assert s.floor is Floor.USER
    assert any(isinstance(c, StopSpeaking) for c in cmds)
    assert SetFloor(floor=Floor.USER) in cmds


def test_listen_only_leaves_floor_with_the_ai():
    s, _ = drive(to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C"))])
    s, _ = step(s, UserArrived(t_ms=620_000, mode="listen"))
    assert s.presence is UserPresence.LISTENING and s.floor is Floor.AI


def test_summon_timeout_falls_back_to_callback():
    s, _ = drive(to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C"))])
    s, cmds = step(s, SummonTimeout(t_ms=650_000))
    assert s.presence is UserPresence.UNREACHABLE
    assert any(isinstance(c, Speak) and c.text == clips.CALLBACK_REQUEST for c in cmds)


def test_terminated_is_final():
    s, _ = drive(to_human())
    s, _ = step(s, RemoteHungUp(t_ms=999_000))
    assert s.latches.terminated and s.end_reason is EndReason.DROPPED_BY_ORG
    s2, cmds = step(s, HumanDetected(t_ms=999_500, families=("A", "B", "C")))
    assert s2 is s and cmds == [], "no resurrection; a retry is a new session"


def test_determinism_same_events_same_decisions():
    events = to_human() + [HumanDetected(t_ms=603_000, families=("A", "B", "C")),
                           UserArrived(t_ms=620_000, mode="join"),
                           VerifyAsk(t_ms=640_000, trigger="date of birth")]
    a = drive(events)
    b = drive(events)
    assert a[0] == b[0] and a[1] == b[1]
