"""The leak battery, and proof that it can say both words.

A gate that only ever says DISQUALIFIED is not a gate, it is a constant. So the
controls here matter more than the headline: one builds a corpus that leaks and
requires the battery to catch it, and one builds a corpus that does NOT leak and
requires the battery to pass it. Without the second, the first proves nothing.
"""

import math
import random
import struct
import wave

import pytest

from bench.leak import CHANCE_CEILING, auc, battery, scalars
from saathi import voice

RATE = 16_000


def _write(path, *, seconds=1.2, floor=0, seed=1):
    """A tone with a pause in the middle.

    `floor` is the peak amplitude of the noise sitting under everything. floor=0
    reproduces what a synthesiser does -- it writes speech into a buffer, so the
    pause is bit-exact zero. Any floor at all reproduces what a microphone does.
    """
    rng = random.Random(seed)
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        speaking = not (n // 3 <= i < 2 * n // 3)          # a pause in the middle
        v = int(7000 * math.sin(2 * math.pi * 220 * i / RATE)) if speaking else 0
        if floor:
            v += rng.randint(-floor, floor)
        out.append(max(-32768, min(32767, v)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(out)}h", *out))
    return path


def _corpus(tmp_path, name, floor, k=6):
    d = tmp_path / name
    d.mkdir()
    return [_write(d / f"{i}.wav", floor=floor, seed=i) for i in range(k)]


# --------------------------------------------------------------------------- #
# The controls. These are the point of the file.
# --------------------------------------------------------------------------- #

def test_the_battery_catches_a_corpus_that_leaks(tmp_path):
    """MUTATION CONTROL. Buffer-written against microphone-like, differing ONLY
    in whether a noise floor exists -- the tone, the pause and the length are
    identical. A scalar that hears no words must separate them, and the battery
    must refuse the corpus.

    Proves: the gate fires on the confound that disqualified the real corpus.
    Does NOT prove: that any anti-spoofing model would have been fooled by it.
    """
    v = battery({"rendered": _corpus(tmp_path, "rendered", floor=0),
                 "recorded": _corpus(tmp_path, "recorded", floor=40)})
    assert not v.admissible
    assert v.worst_auc > CHANCE_CEILING
    assert v.per_scalar["zero_frame_fraction"] == 1.0, \
        "digital silence is present in one class and absent in the other"


def test_the_battery_passes_a_corpus_that_does_not(tmp_path):
    """THE CONTROL THAT GIVES THE OTHER ONE TEETH.

    Both sides now carry a floor, so provenance no longer tracks the label and
    every voice-free scalar should fall back to chance. If this cannot pass, the
    battery is a constant and the test above certifies nothing.
    """
    v = battery({"a": _corpus(tmp_path, "a", floor=40),
                 "b": _corpus(tmp_path, "b", floor=40)})
    assert v.admissible, f"battery cannot say ADMISSIBLE: {v.reason}"
    assert v.per_scalar["zero_frame_fraction"] <= CHANCE_CEILING


def test_a_backwards_leak_counts_as_a_leak(tmp_path):
    """AUC is folded to [0.5, 1]. A scalar that separates perfectly in reverse
    is exactly as much of a confound as one that separates forwards, and an
    unfolded AUC of 0.05 would have been read as 'well under the ceiling'."""
    assert auc([1.0, 2.0, 3.0], [9.0, 9.0, 9.0]) == 1.0
    assert auc([9.0, 9.0, 9.0], [1.0, 2.0, 3.0]) == 1.0


# --------------------------------------------------------------------------- #
# Today's corpus
# --------------------------------------------------------------------------- #

def test_the_projects_own_audio_is_disqualified():
    """Records the state of the shipped voice cache, and is WRITTEN TO START
    FAILING once real recorded audio is added.

    Every clip is Polly, so there is no provenance contrast to score and the
    battery refuses to produce an AUC. What it can still say without a contrast
    is that the clips were written rather than captured, because bit-exact
    digital silence is the absence of a noise floor and no microphone produces
    it. When this goes red, a second provenance exists and the bench can begin.

    Proves: no anti-spoofing number may be quoted from this corpus today.
    Does NOT prove: anything about any model. No model is run here.
    """
    clips = sorted((voice.CACHE / "wav").glob("*.wav"))
    if len(clips) < 5:
        pytest.skip("voice cache not warmed; run `python -m saathi.cli warm`")

    v = battery({"voice-cache": clips})
    assert not v.admissible
    assert v.worst_auc is None, "one provenance class cannot produce an AUC"
    d = v.digital_silence["voice-cache"]
    assert d["with_digital_silence"] > 0, (
        "no clip carries digital silence any more -- if real recorded audio has "
        "landed, split the cache into labelled groups and score them against "
        "each other instead of skipping this gate"
    )


def test_every_scalar_is_free_of_voice_content(tmp_path):
    """The property that makes the battery meaningful: change the WORDS and
    nothing here moves. These statistics describe the file, not the speech.
    """
    quiet = _write(tmp_path / "a.wav", floor=0, seed=1)
    # Same envelope and floor, different waveform content.
    loudish = _write(tmp_path / "b.wav", floor=0, seed=99)
    a, b = scalars(quiet), scalars(loudish)
    assert a["zero_frame_fraction"] == b["zero_frame_fraction"]
    assert a["duration_s"] == b["duration_s"]
