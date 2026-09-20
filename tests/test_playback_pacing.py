"""Speech must play at full length; only waiting may be compressed.

The bug this guards: the stream was paced purely on the scenario's call clock
divided by the speed control, but the scenario's `speak_ms` are ESTIMATES and
real Polly runs longer. So a clip was regularly handed less time than it needed
and the next frame cut it off mid-word. Measured on real_rep before the fix:

    speaker   slot     audio
    rep       0.0s     3.1s   cut off
    saathi    4.0s    13.1s   cut off   <- the disclosure, chopped after 4s
    rep       5.6s     7.1s   cut off

`cli voice` already promised this behaviour in its output ("every spoken word
plays at full length; only waiting is compressed"); the SSE path did not.
"""

import wave

import pytest

from saathi import voice as V
from saathi.frames import build
from saathi.simulate import SCENARIO_DIR, load, run

CACHE = V.CACHE / "wav"


def _with_audio_ms(frames):
    """What server.annotate_audio_ms does, without importing the HTTP layer."""
    for f in frames:
        aid = f.get("audio")
        p = CACHE / f"{aid}.wav" if aid else None
        if p and p.exists():
            with wave.open(str(p)) as w:
                f["audio_ms"] = int(w.getnframes() / w.getframerate() * 1000)
    return frames


def _worst_truncation(frames, speed):
    """Seconds of speech the pacing would cut off. 0.0 means none.

    Mirrors the server's loop: a frame goes out at its compressed due time or at
    the floor left by the previous clip, whichever is later.
    """
    floor = 0.0
    worst = 0.0
    prev_due, prev_ms = None, None
    for f in frames:
        due = max(f["call_ms"] / 1000.0 / speed, floor)
        if prev_ms:
            gap = due - prev_due
            worst = max(worst, prev_ms / 1000.0 - gap)
        if f.get("audio_ms"):
            floor = due + f["audio_ms"] / 1000.0
        prev_due, prev_ms = due, f.get("audio_ms")
    return max(0.0, worst)


def _frames(name, speed):
    sc = load(name)
    return _with_audio_ms(build(run(sc, sc.get("goal", "")), sc, speed=speed)), sc


@pytest.mark.parametrize("speed", [10, 20, 40])
@pytest.mark.parametrize("name", ["real_rep", "terse_rep", "voicebot_warm"])
def test_no_clip_is_ever_cut_off(name, speed):
    """Proves: at every speed, each clip gets at least its own real duration
    before the next frame is sent.
    Does NOT prove: that the browser plays it. The client queues clips as a
    second line of defence; that is not covered here.
    """
    frames, _ = _frames(name, speed)
    if not any(f.get("audio_ms") for f in frames):
        pytest.skip(f"{name}: no rendered audio; run `python -m saathi.cli warm`")
    # A millisecond, not zero: the arithmetic carries float noise around 1e-15s,
    # and asserting exact equality would fail on rounding rather than on
    # anything audible.
    assert _worst_truncation(frames, speed) < 0.001


def test_the_old_pacing_would_have_cut_speech_off():
    """MUTATION CONTROL. Without the floor, the test above is vacuous -- it
    would pass on any scenario whose estimates happened to be generous. This
    reproduces the original rule and requires it to FAIL, which is what shows
    the guard is doing work.
    """
    frames, _ = _frames("real_rep", 10)
    if not any(f.get("audio_ms") for f in frames):
        pytest.skip("no rendered audio")

    worst, prev_due, prev_ms = 0.0, None, None
    for f in frames:                       # the old rule: compressed clock only
        due = f["call_ms"] / 1000.0 / 10
        if prev_ms:
            worst = max(worst, prev_ms / 1000.0 - (due - prev_due))
        prev_due, prev_ms = due, f.get("audio_ms")
    assert worst > 1.0, (
        "the old pacing no longer truncates anything, so the guard above proves "
        "nothing on this corpus -- check whether scenario timings changed"
    )


def test_waiting_is_still_compressed():
    """The floor must not defeat the speed control. Hold is the thing the
    product exists to absorb, and it has to stay compressed."""
    frames, _ = _frames("real_rep", 40)
    span = max(f["call_ms"] for f in frames) / 1000.0
    floor = 0.0
    for f in frames:
        due = max(f["call_ms"] / 1000.0 / 40, floor)
        if f.get("audio_ms"):
            floor = due + f["audio_ms"] / 1000.0
    assert floor < span / 4, (
        f"playback takes {floor:.0f}s of a {span:.0f}s call; hold is no longer "
        "being compressed"
    )
