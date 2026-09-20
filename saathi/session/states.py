"""Session state, and every irreversible latch in one place.

A reader looking for "what can this thing never undo" should find all of it
here and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from saathi.types import EndReason, Floor, LineState, Mandate, UserPresence


@dataclass(frozen=True)
class Latches:
    """One-way bits. Nothing in the reducer may clear one except where noted."""

    disclosed_for_engagement: int = -1
    """Highest engagement_seq whose disclosure clip has completed. Gates ALL
    agent audio. Resets implicitly on transfer because engagement_seq bumps."""

    handoff_required: bool = False
    """Set by a verification question. The AI cannot hold the floor again this
    engagement, with no exception path."""

    detector_demoted: bool = False
    """Set when the user taps 'not a person'. No auto-summon again this
    session -- never ring someone twice after they told you it was wrong."""

    terminated: bool = False
    """No resurrection. A retry is a new session."""


@dataclass(frozen=True)
class SessionState:
    line: LineState = LineState.IDLE
    presence: UserPresence = UserPresence.AWAY
    floor: Floor = Floor.NONE
    latches: Latches = field(default_factory=Latches)

    engagement_seq: int = 0
    """Bumps on every new party. Disclosure is per-engagement, so a transfer to
    a second rep re-discloses automatically."""

    mandate: Mandate = field(default_factory=Mandate.empty)
    end_reason: EndReason | None = None

    ivr_depth: int = 0
    probes_used: int = 0
    explained: bool = False

    def disclosed(self) -> bool:
        return self.latches.disclosed_for_engagement == self.engagement_seq

    def with_latch(self, **kw) -> "SessionState":
        return replace(self, latches=replace(self.latches, **kw))
