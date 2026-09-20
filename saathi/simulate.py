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
from saathi.mandate import MandateError, accept_offer
from saathi.types import (
    Answered, CaptureCallback, Dialed, Family, HoldMusicDetected, HumanDetected,
    LineState, Mandate, MenuDetected, OfferMade, RemoteUtterance, Ringing,
    SignalObservation, Speak, StopSpeaking, SummonTimeout, SummonUser,
    UserPresence, VerifyAsk,
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
    families: tuple[str, ...] = ()
    """Which families were voting FOR a human at this tick.

    Carried structurally rather than parsed back out of `note`. Verdict.reason
    only names families on the FETCH branch, so re-deriving them by substring
    match left the UI's "N of 3 signs agree" counter reading 0 on every tick
    that was not already a fetch -- i.e. on every tick a viewer is watching to
    decide whether to trust it.
    """


@dataclass(frozen=True)
class CallNote:
    """One thing worth telling the user afterwards, recorded AS IT HAPPENS.

    Not a transcript and not reconstructed at the end. A summary assembled by
    re-reading the call later is a summary of what we can still parse; this is
    a summary of what the call actually did. The distinction matters most
    exactly where it is hardest to re-derive -- an offer that was refused, a
    figure that was never stated.
    """

    t_ms: int
    kind: str            # offer | accepted | escalated | reference | milestone
    text: str            # one line, already fit to show a person
    detail: str = ""


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
    we_spoke: bool = False
    hold_ms: int = 0
    ended_at_ms: int = 0
    """When the call actually stopped, read from the clock.

    NOT beats[-1].t_ms. A hold node writes its "(hold music)" beat when the
    hold BEGINS and then advances the clock, so on a call that ends while still
    holding the last beat predates the end. Measured on ivr_only: last beat
    20:13, real end 25:13 -- five minutes of waiting deleted from the one
    number the product exists to absorb.
    """
    notes: list[CallNote] = field(default_factory=list)
    offers: list[dict] = field(default_factory=list)
    accepted: dict | None = None
    escalated: list[dict] = field(default_factory=list)
    reference_number: str | None = None
    callback_requested: bool = False
    summon_unanswered: bool = False
    probes_used: int = 0

    def note(self, t_ms: int, kind: str, text: str, detail: str = "") -> None:
        self.notes.append(CallNote(t_ms, kind, text, detail))

    @property
    def settled_alone(self) -> bool:
        """The call resolved without the user ever being pulled in.

        Deliberately requires that nothing summoned them -- an accepted offer
        on a call that also handed over is not 'handled alone', and showing it
        that way would overstate what the agent did.
        """
        return (self.accepted is not None
                and self.fetched_at_ms is None
                and self.handed_off_at_ms is None)

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
        mandate=None, max_hold_ms: int = 1_800_000) -> Outcome:
    """`brief` is the ONLY channel by which user facts reach anything the agent
    says. Nothing else is consulted, which is what makes the whitelist in
    build_brief load-bearing rather than decorative.

    `verifier` is injectable so a test can empty the trigger-phrase list and
    show that a miss still leaks nothing."""
    clock = VirtualClock()
    # The mandate is seeded into the reducer and never re-read from text. The
    # authoring sentence does not travel with the session, so there is nothing
    # here for a rep to argue with -- only fields.
    mandate = mandate or Mandate.empty()
    state = SessionState(mandate=mandate)
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

    def presence():
        return state.presence

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
            elif isinstance(c, StopSpeaking):
                # Was silently dropped. A safety stop that never reaches the
                # transport is not a safety stop.
                out.beats.append(Beat(clock.now_ms(), "system",
                                      "stops speaking mid-word"))
            elif isinstance(c, CaptureCallback):
                out.callback_requested = True
                out.note(clock.now_ms(), "callback",
                         "Asked them to register the problem and call back.")
                out.beats.append(Beat(clock.now_ms(), "system",
                                      "asked for a callback"))
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
            # A prompt heard twice is a loop. Escalating beats pressing the
            # same wrong digit forever, and staying silent is the rung that
            # actually works -- many systems route no-input callers to a human.
            #
            # This used to wait for the THIRD hearing, which meant the second
            # visit pressed "repeat these options" on a menu already parsed and
            # replayed it for nothing. choose() now refuses that, so the ladder
            # has to take over one rung earlier or the call just stalls.
            fp = " ".join(prompt.lower().split())
            seen_prompts[fp] = seen_prompts.get(fp, 0) + 1
            if seen_prompts[fp] >= 2:
                rung = "0" if seen_prompts[fp] == 2 else None
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
            digit, why = choose(options, goal,
                                heard_before=seen_prompts[fp] > 1)
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
            # Recorded on the call's own clock. The obvious alternative --
            # subtracting rendered audio length from call length -- mixes two
            # measurement systems: the yaml's speak_ms are estimates and real
            # Polly runs longer, so on a short call the subtraction goes
            # negative and reports a hold of zero on a call that held.
            out.hold_ms += clock.now_ms() - start
            node_id = node.get("then")

        elif kind == "party":
            # New party, new epoch. Without this reset, eight minutes of
            # accumulated menu-and-hold evidence buries the human who has
            # just arrived -- the failure the old design actually had.
            acc = EpochAccumulator(phase=LineState.ASSESSING)
            _party(node["persona"], scenario, clock, acc, rep, verifier,
                   emit, out, spoken_by_us, ours_spoken, brief, mandate,
                   presence)
            node_id = None
        else:
            node_id = None

    # Computed here rather than in _party, which returns early on a
    # verification request. Setting them there left BOTH flags at their
    # defaults for verify_immediately -- it spoke the disclosure and still
    # scored an unmeasured True, so "disclosed first 8/8" was six measurements
    # and two defaults. `we_spoke` is what lets the scoreboard say so.
    out.we_spoke = bool(spoken_by_us)
    out.disclosed_before_speaking = (
        not spoken_by_us or spoken_by_us[0] == "disclosure")

    out.ended_at_ms = clock.now_ms()

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


def _money(kind: str, amount_paise: int | None) -> str:
    if amount_paise is None:
        return f"a {kind} with no figure stated"
    return f"a {kind} of Rs {amount_paise / 100:.0f}"


# How long the user's phone rings before Saathi carries on alone. Long enough
# that someone reaching for a handset is not cut off, short enough that a real
# agent is not left listening to silence -- DESIGN.md is explicit that leaving a
# rep in silence is how you lose the call.
SUMMON_RING_MS = 20_000


def _party(persona, scenario, clock, acc, rep, verifier, emit, out, spoken_by_us,
           ours_spoken, brief=None, mandate=None, presence=None):
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
                              f"{v.decision}  score={v.score:+.2f}", v.reason,
                              families=tuple(f.value for f in v.positive_families)))
        if v.decision == "FETCH" and out.fetched_at_ms is None:
            out.fetched_at_ms = clock.now_ms()
            out.fetch_families = tuple(f.value for f in v.positive_families)
            emit(HumanDetected(t_ms=clock.now_ms(), families=out.fetch_families))
        return True

    def settle(turn) -> None:
        """Record what was offered and let the FROZEN mandate decide.

        Nothing here decides anything. accept_offer() is a field check against a
        mandate parsed before the call; this code can only report what it
        returned. That is the whole point -- a rep can stretch a sentence, and
        there is no sentence here to stretch.

        Notes are written AS THE OFFER ARRIVES rather than reconstructed at the
        end, because a refused offer leaves nothing behind to re-parse: the
        figure that was never authorised is exactly the one a later pass cannot
        recover.
        """
        offer = turn.get("offer")
        if offer:
            kind, amt = offer.get("kind", ""), offer.get("amount_paise")
            said = _money(kind, amt)
            out.offers.append({"kind": kind, "amount_paise": amt,
                               "t_ms": clock.now_ms()})
            out.note(clock.now_ms(), "offer", f"They offered {said}.")
            # Drives the reducer, which summons the user when the offer falls
            # outside the grant and nobody is present to widen it.
            emit(OfferMade(t_ms=clock.now_ms(), offer_kind=kind, amount_paise=amt))
            try:
                res = accept_offer(mandate or Mandate.empty(), kind, amt)
            except MandateError as e:
                out.escalated.append({"kind": kind, "amount_paise": amt,
                                      "why": str(e)})
                out.note(clock.now_ms(), "escalated",
                         f"Did not accept {said}.", str(e))
                out.beats.append(Beat(clock.now_ms(), "system",
                                      f"offer outside the mandate -> asking you",
                                      str(e)))
            else:
                out.accepted = {**res, "t_ms": clock.now_ms()}
                out.note(clock.now_ms(), "accepted",
                         f"Accepted {said} on your behalf.",
                         "inside the authority you gave before the call")
                out.beats.append(Beat(clock.now_ms(), "system",
                                      f"accepted {said}", "within mandate"))
        if turn.get("callback"):
            # The AGENT offered to call back. Distinct from Saathi asking for
            # one: this is a commitment they made, and the summary has to
            # attribute it to them rather than to us.
            out.callback_requested = True
            out.note(clock.now_ms(), "callback",
                     "They said they would log it and call you back.",
                     "their commitment, not something Saathi can confirm")
        ref = turn.get("reference")
        if ref:
            out.reference_number = str(ref)
            out.note(clock.now_ms(), "reference", f"Reference number {ref}.")

    for turn in persona.get("turns", []):
        text = turn if isinstance(turn, str) else turn["say"]
        speak_ms = 2500 if isinstance(turn, str) else turn.get("speak_ms", 2500)
        if not hear(text, speak_ms, "[human]" if truth == "human" else "[machine]"):
            return
        if isinstance(turn, dict):
            settle(turn)

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

    # The user was rung and never picked up. Saathi carries on alone: it states
    # what was authorised, the agent answers, and whatever they say is recorded.
    # The reducer decides whether anything is said at all -- it refuses once the
    # user has joined -- so this only feeds it the timeout.
    if presence is not None and presence() is UserPresence.SUMMONING:
        clock.advance(SUMMON_RING_MS)
        out.summon_unanswered = True
        out.note(clock.now_ms(), "milestone",
                 "You did not pick up, so Saathi carried on without you.")
        emit(SummonTimeout(t_ms=clock.now_ms()))
        clock.advance(4200)                      # the demand takes a moment to say

        for turn in persona.get("after_demand", []):
            text = turn if isinstance(turn, str) else turn["say"]
            speak_ms = 3000 if isinstance(turn, str) else turn.get("speak_ms", 3000)
            if not hear(text, speak_ms, "[human]" if truth == "human" else "[machine]"):
                return
            if isinstance(turn, dict):
                settle(turn)

    # NOTE: both disclosure flags are computed in run(), not here. _party has
    # an early `return False` on a verification request, so anything set at
    # this point is skipped on exactly the call that hands over fastest.
