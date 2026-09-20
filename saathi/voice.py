"""Turning a call into something you can actually hear.

The point of voicing this is not decoration. The product's hardest claim is
that a warm, named, fluent voice bot must NOT fetch the user -- and that claim
is unconvincing in a transcript, where every line reads the same. You have to
hear Ava sound completely human and watch the detector decline anyway.

So the adversarial bot and the real rep both get natural neural voices. Making
the bot sound robotic would be staging a win.

Polly output is cached on disk by (voice, engine, text) hash: re-running a
scenario costs nothing and produces byte-identical audio, which keeps the demo
reproducible and the bill near zero.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000          # Polly 'pcm' is 16-bit signed, mono
CACHE = Path(__file__).resolve().parent.parent / ".voice-cache"


@dataclass(frozen=True)
class Speaker:
    key: str
    voice: str
    engine: str
    note: str


# Who sounds like what. The two that matter are BOT and REP: both natural,
# both plausible, because the whole difficulty is that you cannot tell.
SPEAKERS = {
    "menu": Speaker("menu", "Salli", "standard",
                    "flat and clipped -- an IVR that is not pretending"),
    "queue": Speaker("queue", "Salli", "standard", "same system, same voice"),
    "bot": Speaker("bot", "Danielle", "neural",
                   "warm, natural, friendly. Sounds completely human. It is not."),
    "rep": Speaker("rep", "Kajal", "neural",
                   "an actual person at a support desk"),
    "saathi": Speaker("saathi", "Matthew", "neural",
                      "measured and clearly an assistant -- never impersonating"),
}


class VoiceError(RuntimeError):
    pass


def _cache_path(text: str, sp: Speaker) -> Path:
    h = hashlib.sha256(f"{sp.voice}|{sp.engine}|{text}".encode()).hexdigest()[:24]
    return CACHE / f"{sp.key}-{h}.pcm"


def synthesize(text: str, speaker: str, *, profile: str = "saathi") -> bytes:
    """Raw 16-bit PCM for one utterance. Cached, so a re-run is free."""
    sp = SPEAKERS.get(speaker) or SPEAKERS["saathi"]
    path = _cache_path(text, sp)
    if path.exists():
        return path.read_bytes()

    CACHE.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    cmd = ["aws", "polly", "synthesize-speech", "--profile", profile,
           "--engine", sp.engine, "--voice-id", sp.voice,
           "--output-format", "pcm", "--sample-rate", str(SAMPLE_RATE),
           "--text", text, str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise VoiceError(f"polly failed for {sp.voice}: {r.stderr.strip()[:200]}")
    tmp.replace(path)
    return path.read_bytes()


def silence(ms: int) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * ms / 1000)


def hold_music(ms: int, *, level: float = 0.06) -> bytes:
    """A dull four-note loop, deliberately mixed low and deliberately dull.

    Real hold music is tinny, repetitive and slightly compressed, and that
    texture is part of what the call feels like. It also has to be obviously
    non-speech so the ear can tell 'still waiting' from 'someone is talking'.
    """
    import math
    notes = [392.0, 349.2, 329.6, 293.7]          # G F E D, falling, resigned
    note_ms, out = 700, bytearray()
    total = int(SAMPLE_RATE * ms / 1000)
    n = 0
    while n < total:
        f = notes[(n // int(SAMPLE_RATE * note_ms / 1000)) % len(notes)]
        for _ in range(min(int(SAMPLE_RATE * note_ms / 1000), total - n)):
            t = n / SAMPLE_RATE
            env = 0.6 + 0.4 * math.sin(2 * math.pi * 1.2 * t)
            v = math.sin(2 * math.pi * f * t) * 0.7 + math.sin(2 * math.pi * f * 2 * t) * 0.3
            out += struct.pack("<h", int(v * env * level * 32767))
            n += 1
    return bytes(out)


def write_wav(path: Path, chunks: list[bytes]) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(chunks))
    return path


def available(profile: str = "saathi") -> bool:
    r = subprocess.run(["aws", "sts", "get-caller-identity", "--profile", profile],
                       capture_output=True, text=True)
    return r.returncode == 0


# --------------------------------------------------------------------------- #
# Rendering a whole call
# --------------------------------------------------------------------------- #

HOLD_CAP_MS = 7000
"""How much of a hold actually plays. An eleven-minute wait cannot be heard in
full and must not be silently deleted either, so it is capped and the
compression is REPORTED next to the file rather than hidden inside it."""

GAP_MS = 550
"""Unused for real pauses -- see below. Kept only as a floor for missing gaps."""


def speaker_for(beat) -> str | None:
    """Which voice says this beat, or None if it is not speech."""
    if beat.actor == "saathi":
        if beat.text.startswith("presses") or beat.text.startswith("("):
            return None
        return "saathi"
    if beat.actor != "them":
        return None                       # detector/system lines are screen-only
    note = beat.note or ""
    if "{rep}" in note:
        return "rep"
    if "[menu]" in note:
        return "menu"
    if "[queue]" in note:
        return "queue"
    if "[hold]" in note:
        return None                       # music, handled separately
    if "[machine]" in note:
        return "bot"
    return "rep"


LONG_GAP_MS = 3000
"""Any silence longer than this is waiting, not a pause between sentences."""


def render_call(outcome, *, profile: str = "saathi") -> tuple[list[bytes], dict]:
    """Assemble the audio, accounting for EVERY millisecond of the call.

    The first version of this only treated an explicit hold beat as waiting and
    let the long gaps between queue announcements fall through to the 550ms
    inter-sentence pause -- so nine of eleven minutes vanished without being
    counted, and the compression figure printed next to the file was wrong in
    the flattering direction. Every gap is now classified and reported.
    """
    chunks: list[bytes] = []
    real_wait = rendered_wait = 0
    prev_t = 0

    for b in outcome.beats:
        who = speaker_for(b)
        is_hold_marker = b.actor == "them" and "[hold]" in (b.note or "")
        if who is None and not is_hold_marker:
            continue                      # detector/system lines are screen-only

        gap = max(0, b.t_ms - prev_t)
        if gap >= LONG_GAP_MS:
            real_wait += gap
            play = min(gap, HOLD_CAP_MS)
            rendered_wait += play
            chunks.append(hold_music(play))
        elif gap:
            # Play the pause at its TRUE length, never clamped.
            #
            # These gaps are the detector's own evidence: the adversarial bot
            # answers in 400ms, the human rep takes 1400ms. Clamping both to
            # 550ms renders them identical, so a listener cannot hear the very
            # difference the detector is scoring -- the audio would quietly
            # contradict the thing it is meant to demonstrate. Everything below
            # LONG_GAP_MS is under three seconds, so honesty is nearly free.
            chunks.append(silence(gap))

        if who is not None:
            chunks.append(synthesize(b.text, who, profile=profile))
            # The party's real reply delay, played at true length.
            if b.gap_ms:
                chunks.append(silence(min(b.gap_ms, LONG_GAP_MS)))
            prev_t = b.t_ms + int(len(chunks[-1]) / 2 / SAMPLE_RATE * 1000)
        else:
            prev_t = b.t_ms

    # trailing wait, if the call ended while still holding
    if outcome.beats:
        tail = max(0, outcome.beats[-1].t_ms - prev_t)
        if tail >= LONG_GAP_MS:
            real_wait += tail
            play = min(tail, HOLD_CAP_MS)
            rendered_wait += play
            chunks.append(hold_music(play))

    dur = sum(len(c) for c in chunks) / 2 / SAMPLE_RATE
    call_ms = outcome.beats[-1].t_ms if outcome.beats else 0
    return chunks, {
        "seconds": round(dur, 1),
        "real_wait_s": round(real_wait / 1000, 1),
        "rendered_wait_s": round(rendered_wait / 1000, 1),
        "call_clock_s": round(call_ms / 1000, 1),
        "accounted_s": round((real_wait + sum(
            len(c) for c in chunks) / 2 / SAMPLE_RATE * 1000 - rendered_wait) / 1000, 1),
    }
