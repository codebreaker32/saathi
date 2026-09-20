"""Turning a running call into frames a browser can render live.

Frames are pushed as the call happens rather than returned at the end, but the
Outcome.beats list is still built exactly as before -- so every existing test
that calls run() and reads beats keeps working, and streaming is additive.

Nothing here paces or waits. Pacing belongs to the clock module, which is the
only place allowed to do that; this file only shapes and emits.

One rule the frames enforce: a value that was SCRIPTED rather than measured
says so in the frame itself. SYNTHESIS and IDENTITY are persona-supplied
stand-ins for the anti-spoofing and speaker-embedding models, and SYNTHESIS is
one of the three agreeing families at the moment real_rep fetches -- so the
headline moment leans on a stand-in. A UI that renders it identically to a
measured family is making the same overclaim in pixels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PROTOCOL = 1

SCRIPTED_FAMILIES = {"SYNTHESIS", "IDENTITY"}
"""Rendered with a visible marker. Not yet backed by a model."""


@dataclass
class Stream:
    """Collects frames and hands them to a sink as they are produced."""

    session: str
    sink: Callable[[dict], None] | None = None
    speed: float = 1.0
    seq: int = 0
    frames: list[dict] = field(default_factory=list)
    controls: Callable[[], list[dict]] | None = None

    def push(self, kind: str, call_ms: int, **body) -> dict:
        self.seq += 1
        frame = {
            "v": PROTOCOL,
            "seq": self.seq,
            "session": self.session,
            "call_ms": call_ms,
            "speed": self.speed,
            "type": kind,
            **body,
        }
        self.frames.append(frame)
        if self.sink is not None:
            self.sink(frame)
        return frame

    def drain_controls(self) -> list[dict]:
        return self.controls() if self.controls else []


def family_view(verdict, needed: int = 3) -> dict:
    """The detector rendered so a viewer can see WHICH families agreed.

    One opaque number is the wrong display: the whole argument is that a bot
    has to beat several independent kinds of evidence at once, and that is
    invisible if you only show the total.
    """
    voted = [f.value for f in verdict.positive_families]
    return {
        "decision": verdict.decision,
        "score": round(verdict.score, 2),
        "voted": voted,
        "voted_count": len(voted),
        "families_needed": needed,
        "veto": verdict.veto,
        "reason": verdict.reason,
        "scripted": sorted(SCRIPTED_FAMILIES & set(voted)),
        "would_fetch": verdict.decision == "FETCH",
    }


def controls_for(presence) -> dict:
    """Which buttons actually do something right now.

    The reducer returns state unchanged when presence is AWAY or UNREACHABLE,
    so listen/join/take-over are no-ops before a summon. A button that silently
    does nothing reads as a broken product, so the UI is told which are live.
    """
    live = presence.value in ("SUMMONING", "CONNECTING", "LISTENING", "SPEAKING",
                              "IN_COMMAND")
    return {
        "listen": live,
        "join": live,
        "take_over": live,
        "not_a_person": live,
    }
