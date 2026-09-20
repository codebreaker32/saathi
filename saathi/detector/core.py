"""The verdict. Evidence in, one of {FETCH, MACHINE, UNDECIDED} out.

Two rules shape everything here, and both come from the cost asymmetry:
fetching you when it is still a machine destroys the premise of the product,
while missing a human merely wastes a call.

  1. NO TERM PROPORTIONAL TO ELAPSED TIME MAY BE POSITIVE.
     The previous design seeded a prior that rose with hold duration, so
     "we have waited twenty minutes" became evidence of humanness and the
     score drifted toward fetching with no evidence at all. The prior is now
     seeded ONCE per epoch from the phase, and only evidence moves it.

  2. THE GATE IS AN `if`, NOT ARITHMETIC.
     Thresholds are tuned; a required count of corroborating families is not.
     Broken calibration can break a sum. It cannot break a count.

Recomputed from the retained observation set on every tick rather than
incrementally accumulated. At these rates it costs nothing, and it buys a free
differential oracle: the streaming result must equal a batch recomputation
exactly (see tests/test_detector.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from saathi.types import Family, LineState, SignalObservation

# Whatever answers the phone first is almost never a human. That is the most
# reliable fact in this domain, and it is what the seed encodes.
PRIOR_BY_PHASE = {
    LineState.RINGING: -4.5,
    LineState.IVR: -4.0,
    LineState.HOLD: -3.0,
    LineState.TRANSFER_PENDING: -3.0,
    LineState.ASSESSING: -3.0,
}
DEFAULT_PRIOR = -4.0

POS_CLAMP = 1.5          # asymmetric on purpose: the clamps ARE the asymmetry
NEG_CLAMP = -4.0
FAMILY_POS_CAP = 1.5

# Chosen so that two families at their cap CANNOT reach it from any phase prior,
# which makes the arithmetic agree with the count gate instead of fighting it.
# The count gate is still checked separately: a miscalibration can break a sum,
# and this is the failure that must not be reachable by miscalibration.
#   2 families, capped, from HOLD: -3.0 + 1.5 + 1.5 = 0.0  -> blocked
#   3 families at 1.2 each, HOLD:  -3.0 + 3.6       = 0.6  -> fires
TAU_FETCH = 0.5
TAU_MACHINE = -6.0
MIN_POSITIVE_FAMILIES = 3
VETO_WINDOW_MS = 15_000

POSITIVE_FAMILY_MIN = 0.5   # below this a family has not really voted

Decision = Literal["FETCH", "MACHINE", "UNDECIDED"]


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    score: float
    positive_families: tuple[Family, ...]
    veto: str | None
    reason: str


@dataclass
class EpochAccumulator:
    """Evidence about ONE party. A transfer starts a new one.

    Without that reset, eight minutes of accumulated bot evidence buries the
    human who has just arrived.
    """

    phase: LineState = LineState.HOLD
    observations: list[SignalObservation] = field(default_factory=list)
    truncated: bool = False

    def add(self, obs: SignalObservation | list[SignalObservation]) -> None:
        self.observations.extend(obs if isinstance(obs, list) else [obs])

    # -- aggregation ------------------------------------------------------- #

    def _by_family(self) -> dict[Family, float]:
        pos: dict[Family, float] = {}
        neg: dict[Family, float] = {}
        for o in self.observations:
            llr = max(NEG_CLAMP, min(POS_CLAMP, o.llr))
            if llr > 0:
                # max within family: correlated signals vote once, not twice
                pos[o.family] = max(pos.get(o.family, 0.0), llr)
            elif llr < 0:
                neg[o.family] = max(NEG_CLAMP, neg.get(o.family, 0.0) + llr)
        out = {f: min(FAMILY_POS_CAP, v) for f, v in pos.items()}
        for f, v in neg.items():
            out[f] = out.get(f, 0.0) + v
        return out

    def active_veto(self, t_ms: int) -> str | None:
        for o in reversed(self.observations):
            if o.veto and t_ms - o.t_ms <= VETO_WINDOW_MS:
                return o.name
        return None

    def score(self) -> float:
        return PRIOR_BY_PHASE.get(self.phase, DEFAULT_PRIOR) + sum(
            self._by_family().values())

    def positive_families(self) -> tuple[Family, ...]:
        return tuple(sorted(
            (f for f, v in self._by_family().items() if v >= POSITIVE_FAMILY_MIN),
            key=lambda f: f.value))

    # -- the decision ------------------------------------------------------ #

    def verdict(self, t_ms: int) -> Verdict:
        s = self.score()
        fams = self.positive_families()
        veto = self.active_veto(t_ms)

        if veto:
            return Verdict("MACHINE", s, fams, veto,
                           f"vetoed by {veto}: it told us what it is")
        if self.truncated:
            # Truncation is free here. The expensive error is unreachable by
            # running out of time, so the deadline decides MACHINE.
            return Verdict("MACHINE", s, fams, None,
                           "deadline reached; defaulting to machine")
        if s >= TAU_FETCH and len(fams) >= MIN_POSITIVE_FAMILIES:
            return Verdict("FETCH", s, fams, None,
                           f"{len(fams)} families agree: "
                           + ", ".join(f.value for f in fams))
        if s >= TAU_FETCH:
            return Verdict("UNDECIDED", s, fams, None,
                           f"score {s:.2f} clears the bar but only {len(fams)} "
                           f"famil{'y' if len(fams) == 1 else 'ies'} agree")
        if s <= TAU_MACHINE:
            return Verdict("MACHINE", s, fams, None, f"score {s:.2f} is decisive")
        return Verdict("UNDECIDED", s, fams, None, f"score {s:.2f}, still gathering")


def gate(acc: EpochAccumulator, t_ms: int) -> Verdict:
    """The check the agent cannot argue past.

    fetch_user() calls this. The agent may propose a fetch whenever it likes
    and be refused -- exactly as accept_offer() refuses a rep who argues that
    store credit is basically a refund.
    """
    return acc.verdict(t_ms)
