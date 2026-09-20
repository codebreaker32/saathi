"""The contract. Every other module codes against this file and nothing else.

Design in one paragraph: a call is an ordered stream of Events folded by a pure
reducer into (State, [Command]).  The reducer performs no I/O and reads no clock,
so a recorded event stream replays to byte-identical decisions.  Three state
tracks are kept orthogonal -- what the far end is doing (LineState), where the
user is (UserPresence), and who may speak (Floor) -- because flattening them makes
"who holds the microphone" derivable from two racing variables.  Floor is stored,
never derived, and every transport mute is a projection of it applied by one
idempotent reconciler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Literal, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


@runtime_checkable
class Clock(Protocol):
    """Wall-clock access is banned outside implementations of this protocol.

    tests/test_clock_purity.py greps the package for direct wall-clock and RNG
    use outside clock.py and fails.  This is what lets a twenty-minute hold run
    in milliseconds and replay identically.
    """

    def now_ms(self) -> int: ...

    def sleep_ms(self, ms: int) -> None: ...


# --------------------------------------------------------------------------- #
# State tracks
# --------------------------------------------------------------------------- #


class LineState(str, Enum):
    IDLE = "IDLE"
    DIALING = "DIALING"
    RINGING = "RINGING"
    IVR = "IVR"
    HOLD = "HOLD"
    ASSESSING = "ASSESSING"          # speech that is neither menu nor hold
    ENGAGED = "ENGAGED"              # a human, as far as we can tell
    TRANSFER_PENDING = "TRANSFER_PENDING"
    ENDED = "ENDED"


class UserPresence(str, Enum):
    AWAY = "AWAY"
    SUMMONING = "SUMMONING"
    CONNECTING = "CONNECTING"
    LISTENING = "LISTENING"          # token carries NO publish grant
    SPEAKING = "SPEAKING"
    IN_COMMAND = "IN_COMMAND"        # agent publish revoked
    UNREACHABLE = "UNREACHABLE"


class Floor(str, Enum):
    """Who holds the microphone. Stored explicitly, never inferred."""

    AI = "AI"
    BOTH = "BOTH"
    USER = "USER"
    NONE = "NONE"


class EndReason(str, Enum):
    RESOLVED_WITH_USER = "RESOLVED_WITH_USER"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"
    USER_TOOK_OVER_AND_ENDED = "USER_TOOK_OVER_AND_ENDED"
    NO_ROUTE = "NO_ROUTE"
    MAX_HOLD = "MAX_HOLD"
    DROPPED_BY_ORG = "DROPPED_BY_ORG"
    USER_CANCELLED = "USER_CANCELLED"
    SAFETY_ABORT = "SAFETY_ABORT"
    FAILED_DIAL = "FAILED_DIAL"


@dataclass(frozen=True)
class Caps:
    """A participant's publish/subscribe grant. The SFU enforces it; we project it."""

    can_publish: bool
    subscribes_to: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Facts, playbooks, mandates
# --------------------------------------------------------------------------- #


class Tag(str, Enum):
    """Stating an identifier is not authenticating with one.

    SAFE_TO_STATE is printed on every receipt and is how an account gets found;
    the agent may say it.  NEVER fields are not withheld by policy -- they are
    never loaded into the brief, which is what keeps "I don't have that" a true
    statement rather than a refusal a model might reconsider.
    """

    SAFE_TO_STATE = "SAFE_TO_STATE"
    NEVER = "NEVER"


@dataclass(frozen=True)
class FactSpec:
    field: str
    tag: Tag
    source: Literal["user", "login", "never"] = "user"


@dataclass(frozen=True)
class Playbook:
    id: str
    company: str
    line: str
    problem: str
    goal: str
    lookup_key: str
    facts_required: tuple[FactSpec, ...]
    ivr_path: tuple[str, ...] = ()
    expected_asks: tuple[str, ...] = ()
    offer_options: tuple[str, ...] = ()
    success: tuple[str, ...] = ()

    def safe_fields(self) -> tuple[FactSpec, ...]:
        return tuple(f for f in self.facts_required if f.tag is Tag.SAFE_TO_STATE)

    def never_fields(self) -> tuple[FactSpec, ...]:
        return tuple(f for f in self.facts_required if f.tag is Tag.NEVER)


@dataclass(frozen=True)
class Mandate:
    """What the agent may ACCEPT. Never what it may REVEAL -- that axis is zero.

    Parsed once from free text at authoring time and frozen. The authoring
    sentence does not travel with it: a sentence can be stretched by a rep
    ("store credit is basically a refund, right?"), a field cannot be moved.
    """

    accept_refund_min_paise: int | None = None
    accept_redelivery: bool = False
    accept_credit_min_paise: int | None = None
    accept_replacement: bool = False
    """Money offered to YOU, so the bound is a FLOOR, not a cap.

    A cap would be backwards: it would accept a derisory 50 rupees and refuse
    600. What you actually want to delegate is 'settle it without me if they
    offer at least this much; anything less, come and ask.'"""

    @staticmethod
    def empty() -> "Mandate":
        return Mandate()

    def is_empty(self) -> bool:
        return self == Mandate()


# --------------------------------------------------------------------------- #
# Events -- the only input to the reducer
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Event:
    kind: str
    t_ms: int


@dataclass(frozen=True)
class Dialed(Event):
    kind: str = field(default="dialed", init=False)


@dataclass(frozen=True)
class Ringing(Event):
    kind: str = field(default="ringing", init=False)


@dataclass(frozen=True)
class Answered(Event):
    kind: str = field(default="answered", init=False)


@dataclass(frozen=True)
class RemoteUtterance(Event):
    """A final transcript from the far end."""

    text: str
    t_end_ms: int
    kind: str = field(default="remote_utterance", init=False)


@dataclass(frozen=True)
class OurUtterance(Event):
    """Something we said. Needed for contingency: did their reply depend on it?"""

    text: str
    purpose: Literal["disclosure", "probe", "explain", "stall", "handoff"]
    kind: str = field(default="our_utterance", init=False)


@dataclass(frozen=True)
class HoldMusicDetected(Event):
    kind: str = field(default="hold_music", init=False)


@dataclass(frozen=True)
class MenuDetected(Event):
    options: tuple[tuple[str, str], ...]   # (digit, label)
    complete: bool
    kind: str = field(default="menu", init=False)


@dataclass(frozen=True)
class TransferSignature(Event):
    """Music stopped, dead air, then speech -- or the voice embedding changed."""

    kind: str = field(default="transfer", init=False)


@dataclass(frozen=True)
class HumanDetected(Event):
    families: tuple[str, ...]
    kind: str = field(default="human_detected", init=False)


@dataclass(frozen=True)
class VerifyAsk(Event):
    """A request for authenticating data. Always hands the call over."""

    trigger: str
    kind: str = field(default="verify_ask", init=False)


@dataclass(frozen=True)
class OfferMade(Event):
    offer_kind: str
    amount_paise: int | None
    kind: str = field(default="offer", init=False)


@dataclass(frozen=True)
class UserArrived(Event):
    mode: Literal["listen", "join", "command"]
    kind: str = field(default="user_arrived", init=False)


@dataclass(frozen=True)
class UserOverruled(Event):
    """One tap: 'this isn't a person'. Latches detector_demoted."""

    kind: str = field(default="user_overruled", init=False)


@dataclass(frozen=True)
class SummonTimeout(Event):
    kind: str = field(default="summon_timeout", init=False)


@dataclass(frozen=True)
class RemoteHungUp(Event):
    kind: str = field(default="remote_hungup", init=False)


# --------------------------------------------------------------------------- #
# Commands -- effects are returned, never performed by the reducer
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Command:
    kind: str


@dataclass(frozen=True)
class Speak(Command):
    text: str
    purpose: Literal["disclosure", "probe", "explain", "stall", "handoff"]
    kind: str = field(default="speak", init=False)


@dataclass(frozen=True)
class SendDtmf(Command):
    digits: str
    kind: str = field(default="dtmf", init=False)


@dataclass(frozen=True)
class SetFloor(Command):
    floor: Floor
    kind: str = field(default="set_floor", init=False)


@dataclass(frozen=True)
class SummonUser(Command):
    urgency: Literal["normal", "max"] = "normal"
    kind: str = field(default="summon", init=False)


@dataclass(frozen=True)
class StopSpeaking(Command):
    """Cancels TTS and flushes the buffer. Sub-frame, in-process."""

    kind: str = field(default="stop_speaking", init=False)


@dataclass(frozen=True)
class Hangup(Command):
    reason: EndReason
    kind: str = field(default="hangup", init=False)


@dataclass(frozen=True)
class CaptureCallback(Command):
    kind: str = field(default="capture_callback", init=False)


# --------------------------------------------------------------------------- #
# Detector
# --------------------------------------------------------------------------- #


class Family(str, Enum):
    SYNTHESIS = "SYNTHESIS"
    IDENTITY = "IDENTITY"
    REPETITION = "REPETITION"
    CONTINGENCY = "CONTINGENCY"
    DUPLEX = "DUPLEX"
    DISCLOSURE = "DISCLOSURE"


@dataclass(frozen=True)
class SignalObservation:
    name: str
    family: Family
    llr: float          # positive = human
    t_ms: int
    veto: bool = False


# --------------------------------------------------------------------------- #
# External boundaries
# --------------------------------------------------------------------------- #


@runtime_checkable
class LLMClient(Protocol):
    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> tuple[str, dict]: ...


@runtime_checkable
class CallTransport(Protocol):
    def dial(self, to: str, *, session_id: str) -> str: ...

    def send_dtmf(self, digits: str, *, gap_ms: int = 120) -> None: ...

    def speak(self, text: str) -> None: ...

    def cancel_playback(self) -> None: ...

    def set_capabilities(self, leg: str, caps: Caps) -> None: ...

    def hangup(self, reason: EndReason) -> None: ...

    def events(self) -> Iterator[Event]: ...

    @property
    def clock(self) -> Clock: ...
