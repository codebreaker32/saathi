"""The telephone chain, and the traps it must not fall into.

The chain exists so that two sides of a comparison differ by voice rather than
by provenance. Most of what is asserted here is therefore about what the chain
must NOT do -- normalise level, treat the classes differently, or quietly erase
the signal along with the confound.
"""

import math
from array import array

import pytest

from bench.channel import (
    add_line_noise, apply_gain, chain, through_codec, ulaw_decode, ulaw_encode,
)
from bench.leak import scalars

RATE = 16_000


def _speech(seconds=1.0, amp=6000, pause=True, seed=1):
    """A tone with a bit-exact-zero pause, i.e. what a synthesiser writes."""
    n = int(RATE * seconds)
    return array("h", [
        int(amp * math.sin(2 * math.pi * 220 * i / RATE))
        if not (pause and n // 3 <= i < 2 * n // 3) else 0
        for i in range(n)
    ])


def _rms(xs):
    return math.sqrt(sum(v * v for v in xs) / len(xs)) if xs else 0.0


# --------------------------------------------------------------------------- #
# G.711
# --------------------------------------------------------------------------- #

def test_mu_law_is_a_valid_byte_code():
    assert all(0 <= ulaw_encode(v) <= 255 for v in range(-32768, 32768, 7))


def test_mu_law_floors_the_quietest_samples_to_exact_zero():
    """The documented mechanism by which digital silence survives a phone line:
    ulaw_decode(ulaw_encode(k)) == 0 for k in 0..3. A band-limit and a codec are
    therefore NOT enough to erase how a file was made -- which is why the chain
    adds a noise floor, and why doing so is a property of the line rather than a
    favour to one class.
    """
    assert [k for k in range(8) if ulaw_decode(ulaw_encode(k)) == 0] == [0, 1, 2, 3]


def test_mu_law_round_trip_error_stays_bounded():
    worst = max(abs(v - ulaw_decode(ulaw_encode(v))) for v in range(-30000, 30000, 13))
    assert worst < 0.02 * 32768


def test_the_codec_alone_preserves_digital_silence():
    """The negative result that motivates the noise stage."""
    before = scalars_of(_speech())
    after = scalars_of(through_codec(_speech()))
    assert before["zero_frame_fraction"] > 0
    assert after["zero_frame_fraction"] > 0, \
        "codec alone must NOT be credited with removing the confound"


def scalars_of(xs, tmp=None):
    import tempfile, wave, os
    from pathlib import Path
    fd, p = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(p, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(xs.tobytes())
    try:
        return scalars(Path(p))
    finally:
        os.unlink(p)


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #

def test_the_chain_removes_digital_silence():
    """Proves: a clip that went through the chain no longer advertises that it
    was written rather than recorded.
    Does NOT prove: that the chain removes every provenance cue. Measured in
    research/channel-matching-findings.md, six of eight scalars still leaked
    after channel matching.
    """
    out = chain(_speech(), RATE, snr_db=20.0)
    assert scalars_of(out)["zero_frame_fraction"] == 0.0


def test_the_chain_is_deterministic_for_a_given_seed():
    a = chain(_speech(), RATE, snr_db=20.0, seed=7)
    b = chain(_speech(), RATE, snr_db=20.0, seed=7)
    assert a.tobytes() == b.tobytes()


def test_noise_is_relative_to_the_clip_so_it_cannot_encode_the_class():
    """A fixed absolute noise amplitude would make loud clips look cleaner than
    quiet ones, which would hand the battery a brand-new confound."""
    quiet, loud = _speech(amp=1200, pause=False), _speech(amp=12000, pause=False)
    q = _rms(add_line_noise(quiet, 20.0, 0)) / max(1e-9, _rms(quiet))
    l = _rms(add_line_noise(loud, 20.0, 0)) / max(1e-9, _rms(loud))
    assert abs(q - l) < 0.05, "noise must scale with the clip, not sit at a fixed level"


# --------------------------------------------------------------------------- #
# The level fix must not become the documented trap
# --------------------------------------------------------------------------- #

def test_gain_jitter_randomises_level_and_does_not_equalise_it():
    """MUTATION CONTROL for the trap. RMS normalisation is the intuitive fix for
    a loudness leak and it is measurably destructive -- the findings doc recorded
    spontaneous-human-vs-Polly going 0.825 -> 0.451, i.e. chance, purely from
    normalising. So this asserts the OPPOSITE of normalisation: after jitter the
    clips must still differ in level, and must differ MORE than before, not less.

    A normaliser would drive the spread toward zero and pass any test that only
    checked "levels changed".
    """
    base = [_speech(amp=a, pause=False) for a in (2000, 6000, 12000)]
    before = [_rms(x) for x in base]
    after = [_rms(apply_gain(x, 9.0, seed=i)) for i, x in enumerate(base)]

    spread = lambda v: max(v) / max(1e-9, min(v))
    assert spread(after) > 1.5, "levels were flattened -- this is normalisation"
    assert any(abs(a - b) > 1 for a, b in zip(before, after)), "gain did nothing"


def test_gain_jitter_draws_from_one_distribution_for_every_class():
    """The jitter is keyed only on the clip index, never on which engine produced
    it. If it could see the class it would be manufacturing the confound it is
    meant to remove."""
    x = _speech(pause=False)
    assert apply_gain(x, 9.0, seed=3).tobytes() == apply_gain(x, 9.0, seed=3).tobytes()
    assert apply_gain(x, 9.0, seed=3).tobytes() != apply_gain(x, 9.0, seed=4).tobytes()


def test_a_zero_jitter_setting_is_a_no_op():
    x = _speech(pause=False)
    assert apply_gain(x, 0.0, seed=1).tobytes() == x.tobytes()


# --------------------------------------------------------------------------- #

def test_the_chain_rejects_an_unexpected_sample_rate():
    with pytest.raises(ValueError):
        chain(_speech(), 8_000)
