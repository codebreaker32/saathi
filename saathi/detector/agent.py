"""The detection agent's tool surface.

Split deliberately in two. The read tools exist because a language model cannot
hear: it has no access to synthesis artefacts, breath or room tone, which is
the evidence that actually settles an undisclosed generative bot. The tools
hand it model outputs it could never perceive itself.

The act tools are where the boundary lives. fetch_user() validates the gate and
refuses when it is unmet, so the agent may propose a fetch whenever it likes and
be told no -- exactly as accept_offer() refuses a rep who argues that store
credit is basically a refund. The agent proposes; the gate disposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from saathi.detector.core import EpochAccumulator, Verdict, gate

MAX_PROBES_PER_PARTY = 2


class GateRefused(Exception):
    """fetch_user() was called without corroboration. Not an error -- a refusal."""

    def __init__(self, verdict: Verdict):
        self.verdict = verdict
        super().__init__(
            f"refusing to fetch: {verdict.reason}. "
            f"Keep gathering evidence, or mark_machine() and resume waiting."
        )


class ProbeBudgetExhausted(Exception):
    pass


@dataclass
class DetectionTools:
    acc: EpochAccumulator
    transcript: list[tuple[int, str, str]] = field(default_factory=list)
    probes_used: int = 0
    fetch_attempts: list[Verdict] = field(default_factory=list)
    synthesis: float | None = None
    voice_changed: bool = False

    # -- read -------------------------------------------------------------- #

    def synthesis_score(self) -> dict:
        """Higher means more likely synthesised. From the anti-spoofing model."""
        return {"score": self.synthesis, "available": self.synthesis is not None}

    def voice_changed_since(self, t_ms: int) -> dict:
        return {"changed": self.voice_changed}

    def turn_timing(self) -> dict:
        gaps = [
            self.transcript[i][0] - self.transcript[i - 1][0]
            for i in range(1, len(self.transcript))
        ]
        return {"gaps_ms": gaps, "turns": len(self.transcript)}

    def transcript_window(self, n: int = 6) -> dict:
        return {"turns": [
            {"t_ms": t, "speaker": who, "text": txt}
            for t, who, txt in self.transcript[-n:]
        ]}

    def evidence_summary(self, t_ms: int) -> dict:
        v = gate(self.acc, t_ms)
        return {
            "score": round(v.score, 2),
            "families_agreeing": [f.value for f in v.positive_families],
            "families_needed": 3,
            "veto": v.veto,
            "would_fetch_succeed": v.decision == "FETCH",
        }

    # -- act --------------------------------------------------------------- #

    def probe(self, kind: str, text: str, t_ms: int) -> dict:
        """Bounded outside the prompt. There is a real person on the other end
        about half the time, and burning their patience to run a test is both
        rude and the fastest way to get the whole category blocked."""
        if self.probes_used >= MAX_PROBES_PER_PARTY:
            raise ProbeBudgetExhausted(
                f"already used {self.probes_used} probes on this party; "
                f"decide with what you have")
        self.probes_used += 1
        self.transcript.append((t_ms, "us", text))
        return {"spoken": text, "probes_remaining":
                MAX_PROBES_PER_PARTY - self.probes_used}

    def fetch_user(self, t_ms: int) -> dict:
        v = gate(self.acc, t_ms)
        self.fetch_attempts.append(v)
        if v.decision != "FETCH":
            raise GateRefused(v)
        return {"fetched": True, "families": [f.value for f in v.positive_families]}

    def mark_machine(self, t_ms: int) -> dict:
        return {"marked": "machine", "score": round(gate(self.acc, t_ms).score, 2)}
