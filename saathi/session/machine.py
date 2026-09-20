"""The reducer. Pure: no I/O, no clock, no network, no awaits.

    step(state, event) -> (state, [Command])

Total function -- an event it does not understand returns the state unchanged
rather than raising. That matters because the far end is an adversarial,
badly-behaved system and a crash mid-call drops a real person's support call.

Because it is pure, a recorded event stream replays to byte-identical decisions,
which is what makes a bad fetch debuggable rather than a mystery.
"""

from __future__ import annotations

from dataclasses import replace

from saathi import clips
from saathi.session.states import SessionState
from saathi.types import (
    Answered,
    CaptureCallback,
    Command,
    Dialed,
    EndReason,
    Event,
    Floor,
    Hangup,
    HoldMusicDetected,
    HumanDetected,
    LineState,
    MenuDetected,
    OfferMade,
    RemoteHungUp,
    RemoteUtterance,
    Ringing,
    SendDtmf,
    SetFloor,
    Speak,
    StopSpeaking,
    SummonTimeout,
    SummonUser,
    TransferSignature,
    UserArrived,
    UserOverruled,
    UserPresence,
    VerifyAsk,
)

MAX_PROBES_PER_PARTY = 2


def step(state: SessionState, event: Event) -> tuple[SessionState, list[Command]]:
    if state.latches.terminated:
        return state, []
    handler = _HANDLERS.get(type(event))
    if handler is None:
        return state, []
    return handler(state, event)


# --------------------------------------------------------------------------- #


def _dialed(s: SessionState, e: Dialed):
    if s.line is not LineState.IDLE:
        return s, []
    return replace(s, line=LineState.DIALING, floor=Floor.NONE), []


def _ringing(s: SessionState, e: Ringing):
    if s.line is not LineState.DIALING:
        return s, []
    return replace(s, line=LineState.RINGING), []


def _answered(s: SessionState, e: Answered):
    if s.line not in (LineState.RINGING, LineState.TRANSFER_PENDING):
        return s, []
    return replace(s, line=LineState.IVR), []


def _menu(s: SessionState, e: MenuDetected):
    if s.line in (LineState.ENDED, LineState.ASSESSING, LineState.ENGAGED):
        return s, []
    s = replace(s, line=LineState.IVR)
    if not e.complete:
        # Never press on a partial menu: the option you want is often last.
        return s, []
    return s, []


def _hold(s: SessionState, e: HoldMusicDetected):
    if s.line in (LineState.ENDED, LineState.ENGAGED):
        return s, []
    return replace(s, line=LineState.HOLD, ivr_depth=s.ivr_depth), []


def _remote_utterance(s: SessionState, e: RemoteUtterance):
    """Speech that is neither menu nor hold means a candidate party.

    Entering ASSESSING is what triggers disclosure -- before any confirmation
    that this is a human, because you can never speak to a human undisclosed and
    speaking to an IVR by mistake costs nothing.
    """
    if s.line not in (LineState.IVR, LineState.HOLD, LineState.ASSESSING):
        return s, []
    cmds: list[Command] = []
    if s.line is not LineState.ASSESSING:
        s = replace(s, line=LineState.ASSESSING)
    if not s.disclosed():
        cmds.append(Speak(text=clips.DISCLOSURE, purpose="disclosure"))
        s = s.with_latch(disclosed_for_engagement=s.engagement_seq)
        s = replace(s, floor=Floor.AI)
    return s, cmds


def _human_detected(s: SessionState, e: HumanDetected):
    if s.line is not LineState.ASSESSING:
        return s, []
    cmds: list[Command] = []
    s = replace(s, line=LineState.ENGAGED)
    # Summon and explain in PARALLEL. Serialising them leaves a real person
    # listening to silence, which is the fastest way to lose the call.
    if not s.latches.detector_demoted and s.presence is UserPresence.AWAY:
        cmds.append(SummonUser(urgency="normal"))
        s = replace(s, presence=UserPresence.SUMMONING)
    return s, cmds


def _verify_ask(s: SessionState, e: VerifyAsk):
    """Always hands over. There is no mandate, setting or grant that changes it."""
    if s.line is LineState.ENDED:
        return s, []
    cmds: list[Command] = [
        StopSpeaking(),
        Speak(text=clips.VERIFY_HANDOFF, purpose="handoff"),
    ]
    s = s.with_latch(handoff_required=True)
    if s.presence in (UserPresence.AWAY, UserPresence.UNREACHABLE):
        cmds.append(SummonUser(urgency="max"))
        s = replace(s, presence=UserPresence.SUMMONING, floor=Floor.NONE)
    else:
        cmds.append(SetFloor(floor=Floor.USER))
        s = replace(s, floor=Floor.USER, presence=UserPresence.IN_COMMAND)
    return s, cmds


def _offer(s: SessionState, e: OfferMade):
    """The reducer does not accept offers -- mandate.accept_offer() does.

    All this does is escalate when the offer is outside the grant and nobody is
    here to widen it.
    """
    from saathi.mandate import permits

    if permits(s.mandate, e.offer_kind, e.amount_paise):
        return s, []
    if s.presence is UserPresence.AWAY:
        return replace(s, presence=UserPresence.SUMMONING), [SummonUser(urgency="normal")]
    return s, []


def _user_arrived(s: SessionState, e: UserArrived):
    if s.presence in (UserPresence.AWAY, UserPresence.UNREACHABLE):
        return s, []
    if e.mode == "listen":
        # Listen-only is a token that carries no publish grant, not a mute flag.
        return replace(s, presence=UserPresence.LISTENING, floor=Floor.AI), [
            SetFloor(floor=Floor.AI)
        ]
    if e.mode == "join":
        return replace(s, presence=UserPresence.SPEAKING, floor=Floor.BOTH), [
            SetFloor(floor=Floor.BOTH)
        ]
    # Taking over: the AI stops mid-word and loses publish entirely.
    return replace(s, presence=UserPresence.IN_COMMAND, floor=Floor.USER), [
        StopSpeaking(),
        SetFloor(floor=Floor.USER),
    ]


def _user_overruled(s: SessionState, e: UserOverruled):
    """One tap, no confirmation dialog, stops mid-word.

    Finishing the sentence or explaining itself is exactly the ungracefulness
    that loses trust. And it never auto-summons again this session: you do not
    ring someone twice after they told you it was wrong.
    """
    s = s.with_latch(detector_demoted=True)
    s = replace(
        s,
        line=LineState.HOLD,
        presence=UserPresence.AWAY,
        floor=Floor.NONE,
    )
    return s, [StopSpeaking(), SetFloor(floor=Floor.NONE)]


def _transfer(s: SessionState, e: TransferSignature):
    """New party: bump the engagement so disclosure fires again, and drop the
    verification latch, which was about the rep who just left."""
    if s.line is LineState.ENDED:
        return s, []
    s = replace(
        s,
        line=LineState.TRANSFER_PENDING,
        engagement_seq=s.engagement_seq + 1,
        floor=Floor.NONE,
        probes_used=0,
        explained=False,
    )
    s = s.with_latch(handoff_required=False)
    return s, [SetFloor(floor=Floor.NONE)]


def _summon_timeout(s: SessionState, e: SummonTimeout):
    if s.presence is not UserPresence.SUMMONING:
        return s, []
    s = replace(s, presence=UserPresence.UNREACHABLE)
    if s.line is LineState.ENGAGED:
        return s, [
            Speak(text=clips.CALLBACK_REQUEST, purpose="explain"),
            CaptureCallback(),
        ]
    return s, []


def _remote_hungup(s: SessionState, e: RemoteHungUp):
    s = replace(s, line=LineState.ENDED, floor=Floor.NONE,
                end_reason=EndReason.DROPPED_BY_ORG)
    return s.with_latch(terminated=True), [Hangup(reason=EndReason.DROPPED_BY_ORG)]


_HANDLERS = {
    Dialed: _dialed,
    Ringing: _ringing,
    Answered: _answered,
    MenuDetected: _menu,
    HoldMusicDetected: _hold,
    RemoteUtterance: _remote_utterance,
    HumanDetected: _human_detected,
    VerifyAsk: _verify_ask,
    OfferMade: _offer,
    UserArrived: _user_arrived,
    UserOverruled: _user_overruled,
    TransferSignature: _transfer,
    SummonTimeout: _summon_timeout,
    RemoteHungUp: _remote_hungup,
}
