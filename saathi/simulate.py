"""A mock company on the other end of the line, under a virtual clock.

A twenty-minute hold costs microseconds here, which is the only reason it is
practical to run the same call a hundred times while tuning.

Honest about its stage: this drives the EVENT layer. The audio-derived families
(SYNTHESIS, IDENTITY) are supplied by the persona as scripted values standing in
for the anti-spoofing and speaker-embedding models. Those are marked `scripted`
in the timeline so nothing here can be mistaken for a measurement. Swapping in
real audio means replacing those two suppliers, not rewriting the runner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from saathi import clips
from saathi.clock import VirtualClock
from saathi.detector.agent import MAX_PROBES_PER_PARTY as MAX_PROBES
from saathi.detector.core import EpochAccumulator, gate
from saathi.llm import StubLLM
from saathi.evidence import contingency, lexical
from saathi.evidence.repetition import RepetitionDetector
from saathi.navigate import choose, parse_menu
from saathi.session.machine import step
from saathi.session.states import SessionState
from saathi.types import (
    Answered, Dialed, Family, HoldMusicDetected, HumanDetected, LineState,
    MenuDetected, RemoteUtterance, Ringing, SignalObservation, Speak,
    SummonUser, VerifyAsk,
)
from saathi.verification import VerificationDetector

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"


@dataclass
class Beat:
    t_ms: int
    actor: str
    text: str
    note: str = ""
    gap_ms: int = 0
    """How long this party waited before speaking.

    Carried explicitly rather than derived from timestamps, because the
    scenario's speak_ms figures are estimates and real speech runs longer --
    so a derived gap drowns the very difference that matters. The bot answers
    in 400ms and the rep takes 1400ms, and that contrast is evidence the
    detector scores. If the rendered audio cannot reproduce it, the recording
    contradicts the thing it exists to demonstrate.
    """


@dataclass
class Outcome:
    beats: list[Beat] = field(default_factory=list)
    fetched_at_ms: int | None = None
    fetch_families: tuple[str, ...] = ()
    handed_off_at_ms: int | None = None
    truth: str = "unknown"
    human_first_word_ms: int | None = None
    warm_start: bool = False
    learned: int = 0
    disclosed_before_speaking: bool = True
    probes_used: int = 0

    @property
    def false_fetch(self) -> bool:
        return self.fetched_at_ms is not None and self.truth != "human"

    @property
    def missed_human(self) -> bool:
        return self.truth == "human" and self.fetched_at_ms is None

    @property
    def time_to_fetch_ms(self) -> int | None:
        if self.fetched_at_ms is None or self.human_first_word_ms is None:
            return None
        return self.fetched_at_ms - self.human_first_word_ms


def load(name: str, directory: Path | None = None) -> dict:
    d = directory or SCENARIO_DIR
    return yaml.safe_load((d / f"{name}.yaml").read_text())


def run(scenario: dict, goal: str, *, brief=None, verifier=None, store=None,
        max_hold_ms: int = 1_800_000) -> Outcome:
    """`brief` is the ONLY channel by which user facts reach anything the agent
    says. Nothing else is consulted, which is what makes the whitelist in
    build_brief load-bearing rather than decorative.

    `verifier` is injectable so a test can empty the trigger-phrase list and
    show that a miss still leaks nothing."""
    clock = VirtualClock()
    state = SessionState()
    out = Outcome()
    acc = EpochAccumulator(phase=LineState.IVR)
    rep = RepetitionDetector(destination=scenario.get("line", "sim://unknown"))
    # What we heard on previous calls to this number -- the single strongest
    # signal available, because an IVR prompt is identical on every call and a
    # person is not. Fetched ONCE, here, and matched in memory for the rest of
    # the call: a round trip inside the decision loop would put latency on the
    # one decision where latency is trust.
    #
    # The scenario's own known_prompts are kept as a seed so tests stay
    # offline and deterministic whether or not a table exists.
    line = scenario.get("line", "sim://unknown")
    for prompt in scenario.get("known_prompts", []):
        rep.remember(prompt)
    if store is not None:
        for prompt in store.known(line):
            rep.remember(prompt)
        out.warm_start = True
    verifier = verifier if verifier is not None else VerificationDetector()
    spoken_by_us: list[str] = []
    # Digits a rep may read back to us without it counting as a request to
    # authenticate. Derived ONLY from whitelisted fact values -- never from the
    # free-text problem box.
    #
    # Deriving it from the rendered utterance instead compounds a scrubber miss
    # into a second failure: a credential that leaked into what we said would be
    # added to this set, which then DISARMS the digit backstop for exactly those
    # digits, so the rep reading it back would pass unnoticed too.
    if brief is not None:
        ours_spoken: set[str] = {
            d for v in brief.facts.values()
            for d in re.findall(r"\d{4,8}", str(v))
        }
    else:
        ours_spoken = set(
            re.findall(r"\d{4,8}", _render(clips.DISCLOSURE, scenario, brief)))
    seen_prompts: dict[str, int] = {}

    def emit(event):
        nonlocal state
        state, cmds = step(state, event)
        for c in cmds:
            if isinstance(c, Speak):
                spoken_by_us.append(c.purpose)
                out.beats.append(Beat(clock.now_ms(), "saathi",
                                      _render(c.text, scenario, brief),
                                      f"[{c.purpose}]"))
            elif isinstance(c, SummonUser):
                out.beats.append(Beat(clock.now_ms(), "system",
                                      f"summoning the user ({c.urgency})"))
        return cmds

    emit(Dialed(t_ms=clock.now_ms()))
    clock.advance(400)
    emit(Ringing(t_ms=clock.now_ms()))
    clock.advance(scenario.get("ring_ms", 6000))
    emit(Answered(t_ms=clock.now_ms()))

    node_id = scenario.get("start", "root")
    guard = 0
    while node_id and guard < 40:
        guard += 1
        node = scenario["nodes"][node_id]
        kind = node["kind"]

        if kind == "menu":
            prompt = node["prompt"]
            out.beats.append(Beat(clock.now_ms(), "them", prompt, "[menu]"))
            clock.advance(node.get("speak_ms", 7000))
            # Same prompt three times means a loop. Escalating beats pressing
            # the same wrong digit forever, and staying silent is the rung that
            # actually works -- many systems route no-input callers to a human.
            fp = " ".join(prompt.lower().split())
            seen_prompts[fp] = seen_prompts.get(fp, 0) + 1
            if seen_prompts[fp] >= 3:
                rung = "0" if seen_prompts[fp] == 3 else None
                if rung:
                    out.beats.append(Beat(clock.now_ms(), "saathi", "presses 0",
                                          "stuck: escalating to an operator"))
                    clock.advance(600)
                    node_id = node["options"].get("0") or node.get("on_zero")
                    if node_id:
                        continue
                out.beats.append(Beat(clock.now_ms(), "saathi", "(stays silent)",
                                      "stuck: waiting out the reprompt"))
                clock.advance(9000)
                node_id = node.get("on_silence") or node.get("on_timeout")
                continue
            options = parse_menu(prompt)
            emit(MenuDetected(t_ms=clock.now_ms(),
                              options=tuple((o.digit, o.label) for o in options),
                              complete=True))
            acc.phase = LineState.IVR
            acc.add(lexical.observe(prompt, clock.now_ms()))
            acc.add(rep.observe(prompt, clock.now_ms(), node.get("speak_ms", 7000)))
            digit, why = choose(options, goal)
            if digit is None:
                out.beats.append(Beat(clock.now_ms(), "saathi", "(waiting)", why))
                node_id = node.get("on_timeout")
                continue
            out.beats.append(Beat(clock.now_ms(), "saathi", f"presses {digit}", why))
            clock.advance(600)
            node_id = node["options"].get(digit)

        elif kind == "hold":
            emit(HoldMusicDetected(t_ms=clock.now_ms()))
            acc.phase = LineState.HOLD
            out.beats.append(Beat(clock.now_ms(), "them", "(hold music)", "[hold]"))
            start = clock.now_ms()
            for ann in node.get("announcements", []):
                clock.advance(max(0, start + ann["at_ms"] - clock.now_ms()))
                out.beats.append(Beat(clock.now_ms(), "them", ann["text"], "[queue]"))
                acc.add(lexical.observe(ann["text"], clock.now_ms()))
                acc.add(rep.observe(ann["text"], clock.now_ms(), 4000))
            wait = min(node.get("wait_ms", 60_000), max_hold_ms)
            clock.advance(max(0, start + wait - clock.now_ms()))
            node_id = node.get("then")

        elif kind == "party":
            # New party, new epoch. Without this reset, eight minutes of
            # accumulated menu-and-hold evidence buries the human who has
            # just arrived -- the failure the old design actually had.
            acc = EpochAccumulator(phase=LineState.ASSESSING)
            _party(node["persona"], scenario, clock, acc, rep, verifier,
                   emit, out, spoken_by_us, ours_spoken, brief)
            node_id = None
        else:
            node_id = None

    gt = scenario.get("ground_truth", {})
    out.truth = gt.get("truth", "unknown")
    out.human_first_word_ms = gt.get("human_first_word_ms")

    # Write back only what the detector concluded was a machine. Remembering a
    # human's words would poison the index: the next call would score a live
    # person as a recording of themselves.
    if store is not None:
        for b in out.beats:
            note = b.note or ""
            if b.actor == "them" and any(t in note for t in
                                         ("[menu]", "[queue]", "[machine]")):
                store.remember(line, b.text)
                out.learned += 1
    return out


def _render(text: str, scenario: dict, brief=None) -> str:
    """Substitutes ONLY from the brief, which carries SAFE_TO_STATE fields and
    nothing else. A NEVER field cannot appear in an utterance because it never
    entered the brief -- there is no branch here that consults a wider source."""
    if brief is not None:
        phone = brief.facts.get("registered_phone", "the registered number")
        problem = brief.problem_text
        order = brief.facts.get("order_id")
        if order:
            problem = f"Order {order}: {problem}"
    else:
        phone = scenario.get("registered_phone", "98xxx 41182")
        problem = scenario.get(
            "problem_line", "Their order 4471 from Thursday arrived two hours late.")
    return text.replace("{registered_phone}", phone).replace("{problem_line}", problem)


def _party(persona, scenario, clock, acc, rep, verifier, emit, out, spoken_by_us,
           ours_spoken, brief=None):
    """A candidate party speaks. Disclosure fires here, before any verdict."""
    acc.phase = LineState.ASSESSING
    truth = persona.get("truth", "bot")
    planted = contingency.plant(_render(clips.DISCLOSURE, scenario, brief))
    last_ours = _render(clips.DISCLOSURE, scenario, brief)
    # The model's ANSWER is scripted; the judge code path is real. Marked so
    # nothing here can be read as a measurement.
    judge_llm = StubLLM([json.dumps({"binding": persona.get("judge_binding",
                                                            "generic")})] * 20)

    # Audio-derived families. Scripted, standing in for the anti-spoofing and
    # speaker-embedding models until the audio pipeline lands.
    if (syn := persona.get("synthesis_score")) is not None:
        acc.add(SignalObservation(
            name="anti_spoofing(scripted)", family=Family.SYNTHESIS,
            llr=1.5 if syn < 0.5 else -2.0, t_ms=clock.now_ms()))

    def hear(text: str, speak_ms: int, tag: str, *, after_probe: bool = False) -> bool:
        """Process one remote utterance. Returns False if the call is over."""
        nonlocal last_ours
        out.beats.append(Beat(clock.now_ms(), "them", text,
                              tag + persona.get("voice_tag", ""),
                              gap_ms=persona.get("gap_ms", 900)))

        # Order matters. The utterance is processed FIRST, because that is what
        # fires the disclosure -- and a rep whose very first sentence asks for
        # an OTP (the normal case in India, not the unusual one) must still be
        # told what they are talking to before anything else is said to them.
        emit(RemoteUtterance(t_ms=clock.now_ms(), text=text,
                             t_end_ms=clock.now_ms() + speak_ms))

        trigger = verifier.check(text, ours=ours_spoken)
        if trigger:
            emit(VerifyAsk(t_ms=clock.now_ms(), trigger=trigger))
            out.handed_off_at_ms = clock.now_ms()
            out.beats.append(Beat(clock.now_ms(), "system",
                                  "verification request -> handing over",
                                  f"trigger: {trigger}"))
            return False
        acc.add(lexical.observe(text, clock.now_ms()))
        acc.add(rep.observe(text, clock.now_ms(), speak_ms,
                            after_probe=after_probe))
        acc.add(contingency.observe(text, planted, clock.now_ms()))
        if contingency.judgeable(text):
            acc.add(contingency.judge(text, last_ours, judge_llm, clock.now_ms()))

        duplex = persona.get("duplex")
        if duplex == "yield":
            acc.add(SignalObservation(name="overlap_and_yield",
                                      family=Family.DUPLEX, llr=1.2,
                                      t_ms=clock.now_ms()))
        elif duplex == "steamroll":
            acc.add(SignalObservation(name="continues_through_us",
                                      family=Family.DUPLEX, llr=-1.5,
                                      t_ms=clock.now_ms()))

        clock.advance(speak_ms + persona.get("gap_ms", 900))
        v = gate(acc, clock.now_ms())
        out.beats.append(Beat(clock.now_ms(), "detector",
                              f"{v.decision}  score={v.score:+.2f}", v.reason))
        if v.decision == "FETCH" and out.fetched_at_ms is None:
            out.fetched_at_ms = clock.now_ms()
            out.fetch_families = tuple(f.value for f in v.positive_families)
            emit(HumanDetected(t_ms=clock.now_ms(), families=out.fetch_families))
        return True

    for turn in persona.get("turns", []):
        text = turn if isinstance(turn, str) else turn["say"]
        speak_ms = 2500 if isinstance(turn, str) else turn.get("speak_ms", 2500)
        if not hear(text, speak_ms, "[human]" if truth == "human" else "[machine]"):
            return

        # Nothing to judge and nothing decided: this is the terse case, and the
        # only way forward is to say something and see what comes back.
        v = gate(acc, clock.now_ms())
        if (v.decision == "UNDECIDED" and not contingency.judgeable(text)
                and out.probes_used < MAX_PROBES and out.fetched_at_ms is None):
            probe = clips.REPAIR_PROBE.format(heard="billing", confusable="building")
            out.probes_used += 1
            out.beats.append(Beat(clock.now_ms(), "saathi", probe,
                                  f"[probe {out.probes_used}/{MAX_PROBES}] "
                                  "nothing to score; eliciting a repair"))
            last_ours = probe
            clock.advance(2200)
            reply = persona.get("on_probe")
            if reply:
                r_text = reply if isinstance(reply, str) else reply["say"]
                r_ms = 1800 if isinstance(reply, str) else reply.get("speak_ms", 1800)
                if not hear(r_text, r_ms, "[reply to probe]",
                            after_probe=True):
                    return

    out.disclosed_before_speaking = (
        not spoken_by_us or spoken_by_us[0] == "disclosure")
