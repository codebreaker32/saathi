"""An Outcome, turned into frames a browser can render in sequence.

Derived from beats after the call has run rather than pushed from inside the
reducer. That keeps run() and all its tests untouched, and it means the server
holds the whole timeline up front -- so it can pace playback against call_ms
and the honest clock is a property of the data rather than of a sleep loop.
"""

from __future__ import annotations

import hashlib

from saathi.stream import PROTOCOL, SCRIPTED_FAMILIES

SPEAKER_OF = {
    "[menu]": "menu", "[queue]": "queue", "[machine]": "bot",
    "[human]": "rep", "[reply to probe]": "rep", "{rep}": "rep",
}


def _speaker(beat) -> str | None:
    if beat.actor == "saathi":
        if beat.text.startswith("presses") or beat.text.startswith("("):
            return None
        return "saathi"
    if beat.actor != "them":
        return None
    note = beat.note or ""
    if "[hold]" in note:
        return None            # music, not speech -- the UI loops its own
    if "{rep}" in note:
        return "rep"
    for tag, who in SPEAKER_OF.items():
        if tag in note:
            return who
    return "rep"


def audio_id(text: str, speaker: str) -> str:
    return hashlib.sha256(f"{speaker}|{text}".encode()).hexdigest()[:20]


def _kind(beat) -> str:
    note = beat.note or ""
    if beat.actor == "detector":
        return "detector"
    if beat.actor == "system":
        return "handoff" if "verification" in beat.text else "summon"
    if beat.actor == "saathi":
        if beat.text.startswith("presses"):
            return "dtmf"
        if "[probe" in note:
            return "probe"
        return "utterance"
    if "[hold]" in note:
        return "hold"
    if "[menu]" in note:
        return "menu"
    if "[queue]" in note:
        return "announcement"
    return "utterance"


def build(outcome, scenario: dict, *, speed: float = 40.0) -> list[dict]:
    frames: list[dict] = []
    seq = 0

    def add(kind: str, call_ms: int, **body):
        nonlocal seq
        seq += 1
        frames.append({"v": PROTOCOL, "seq": seq, "type": kind,
                       "call_ms": call_ms, "speed": speed, **body})

    add("hello", 0,
        scenario=scenario.get("id"), org=scenario.get("org"),
        goal=scenario.get("goal"), line=scenario.get("line"),
        families_needed=3, truth=outcome.truth,
        scripted_families=sorted(SCRIPTED_FAMILIES))

    for b in outcome.beats:
        kind = _kind(b)
        who = _speaker(b)
        body: dict = {"text": b.text, "note": b.note or ""}

        if kind == "detector":
            # "FETCH  score=+1.10" -> the parts the UI actually needs
            head = b.text.split()
            body = {
                "decision": head[0] if head else "UNDECIDED",
                "score": float(head[-1].split("=")[-1]) if "=" in b.text else 0.0,
                "reason": b.note or "",
                "voted": [f for f in ("SYNTHESIS", "IDENTITY", "REPETITION",
                                      "CONTINGENCY", "DUPLEX")
                          if f in (b.note or "")],
                "veto": "vetoed" in (b.note or ""),
            }
            body["scripted"] = sorted(SCRIPTED_FAMILIES & set(body["voted"]))
        elif who:
            body["speaker"] = who
            body["audio"] = audio_id(b.text, who)
        add(kind, b.t_ms, **body)

    add("ended", outcome.beats[-1].t_ms if outcome.beats else 0,
        fetched=outcome.fetched_at_ms is not None,
        fetched_at_ms=outcome.fetched_at_ms,
        families=list(outcome.fetch_families),
        handed_off=outcome.handed_off_at_ms is not None,
        false_fetch=outcome.false_fetch,
        truth=outcome.truth,
        probes=outcome.probes_used)
    return frames
