"""A telephone line, in pure stdlib.

Every clip on both sides of a comparison has to traverse an IDENTICAL chain, or
the comparison measures the chain instead of the voice. That is not a stylistic
preference: `research/antispoof-findings.md` measured two thirds of one model's
apparent skill evaporating once both sides were channel-matched (AUC 0.938 ->
0.69), because it had been reading recording-chain cleanliness as bonafide-ness.

The path, in order:

    band-limit 300-3400 Hz  ->  8 kHz  ->  G.711 mu-law  ->  16 kHz  ->  line noise

Two deliberate choices:

  - NO LEVEL NORMALISATION. Normalising both sides to a common RMS is the
    intuitively fair move and it is measurably destructive: the findings doc
    recorded spontaneous-human-vs-Polly falling from AUC 0.825 to 0.451, i.e.
    chance, purely from RMS normalisation. A real line has already done its own
    gain control. Do not add another.
  - LINE NOISE LAST AND ON BY DEFAULT. Band-limit and codec alone leave
    provenance recoverable, because a buffer-written pause stays bit-exact zero
    through both. The noise floor is what a microphone would have contributed.
    It is a property of the LINE, so it goes on both sides or neither.

mu-law is hand-rolled because `audioop` was removed in Python 3.13, and it is
validated bit-exact against the reference table in tests.
"""

from __future__ import annotations

import math
import random
import wave
from array import array
from pathlib import Path

FULL_SCALE = 32768
BIAS = 0x84
CLIP = 32635


# --------------------------------------------------------------------------- #
# G.711 mu-law
# --------------------------------------------------------------------------- #

def ulaw_encode(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, CLIP) + BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def ulaw_decode(byte: int) -> int:
    byte = ~byte & 0xFF
    sign, exponent, mantissa = byte & 0x80, (byte >> 4) & 0x07, byte & 0x0F
    sample = ((mantissa << 3) + BIAS) << exponent
    sample -= BIAS
    return -sample if sign else sample


def through_codec(xs):
    """The step that makes digital silence survive: |x| < 4 maps to exactly 0."""
    return array("h", (ulaw_decode(ulaw_encode(v)) for v in xs))


# --------------------------------------------------------------------------- #
# Filtering and rate conversion
# --------------------------------------------------------------------------- #

def _biquad(xs, b0, b1, b2, a1, a2):
    out = array("h", bytes(2 * len(xs)))
    x1 = x2 = y1 = y2 = 0.0
    for i, x0 in enumerate(xs):
        y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, x0
        y2, y1 = y1, y0
        out[i] = max(-FULL_SCALE, min(FULL_SCALE - 1, int(y0)))
    return out


def _highpass(xs, rate, f):
    w = 2 * math.pi * f / rate
    c, s = math.cos(w), math.sin(w)
    alpha = s / (2 * 0.707)
    a0 = 1 + alpha
    return _biquad(xs, (1 + c) / 2 / a0, -(1 + c) / a0, (1 + c) / 2 / a0,
                   -2 * c / a0, (1 - alpha) / a0)


def _lowpass(xs, rate, f):
    w = 2 * math.pi * f / rate
    c, s = math.cos(w), math.sin(w)
    alpha = s / (2 * 0.707)
    a0 = 1 + alpha
    return _biquad(xs, (1 - c) / 2 / a0, (1 - c) / a0, (1 - c) / 2 / a0,
                   -2 * c / a0, (1 - alpha) / a0)


def band_limit(xs, rate, lo=300, hi=3400):
    xs = _highpass(xs, rate, lo)
    xs = _lowpass(xs, rate, hi)
    return _lowpass(xs, rate, hi)          # two poles: a real line is steeper


def decimate(xs, factor=2):
    return array("h", xs[::factor])


def interpolate(xs, factor=2):
    """Linear interpolation, then smoothed. Crude on purpose: a real handset
    upsampler is not transparent either, and pretending otherwise would flatter
    whatever model is scored on this."""
    out = array("h", bytes(2 * len(xs) * factor))
    for i, v in enumerate(xs):
        nxt = xs[i + 1] if i + 1 < len(xs) else v
        for k in range(factor):
            out[i * factor + k] = int(v + (nxt - v) * k / factor)
    return out


def add_line_noise(xs, snr_db=20.0, seed=0):
    """A noise floor, as a microphone and a carrier would have supplied.

    Applied to BOTH sides or neither. Adding it only to the class you want to
    look 'recorded' would manufacture exactly the confound this whole bench
    exists to detect.
    """
    rms = math.sqrt(sum(v * v for v in xs) / len(xs)) if xs else 0.0
    if rms <= 0:
        return xs
    amp = rms / (10 ** (snr_db / 20.0))
    rng = random.Random(seed)
    return array("h", (max(-FULL_SCALE, min(FULL_SCALE - 1,
                 int(v + rng.gauss(0, amp)))) for v in xs))


# --------------------------------------------------------------------------- #

def apply_gain(xs, db, seed=0):
    """A per-clip level offset, drawn from the SAME distribution for every class.

    Real calls do not all arrive at one level: handset, distance and carrier all
    vary it. Without this, engine identity maps DETERMINISTICALLY onto loudness
    and rms_dbfs separates the classes perfectly while hearing nothing -- which
    is what this corpus measured (AUC 1.000).

    Note what this is NOT: RMS normalisation. Normalising both sides to a common
    level is the intuitive fix and it is the documented trap -- it took
    spontaneous-human-vs-Polly from AUC 0.825 to 0.451, i.e. chance, by erasing
    the signal along with the confound. This randomises level, it does not
    equalise it, and it is applied identically to every class.
    """
    if not db:
        return xs
    g = 10 ** (random.Random(seed ^ 0x5EED).uniform(-db, db) / 20.0)
    return array("h", (max(-FULL_SCALE, min(FULL_SCALE - 1, int(v * g))) for v in xs))


def chain(xs, rate=16_000, *, snr_db=20.0, seed=0, gain_jitter_db=0.0):
    """The whole path. Input and output are both 16 kHz so clips stay
    comparable with the untouched cache."""
    if rate != 16_000:
        raise ValueError(f"expected 16 kHz, got {rate}")
    xs = apply_gain(xs, gain_jitter_db, seed)
    xs = band_limit(xs, 16_000)
    xs = decimate(xs, 2)                       # 8 kHz, the real sample rate
    xs = through_codec(xs)
    xs = interpolate(xs, 2)                    # back to 16 kHz
    if snr_db is not None:
        xs = add_line_noise(xs, snr_db, seed)
    return xs


def read_wav(path: Path):
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path.name}: need 16-bit mono")
        return array("h", w.readframes(w.getnframes())), w.getframerate()


def write_wav(path: Path, xs, rate=16_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(xs.tobytes())


def process_file(src: Path, dst: Path, *, snr_db=20.0, seed=0, gain_jitter_db=0.0):
    xs, rate = read_wav(src)
    if rate != 16_000:
        raise ValueError(f"{src.name}: {rate} Hz, expected 16000")
    write_wav(dst, chain(xs, rate, snr_db=snr_db, seed=seed,
                         gain_jitter_db=gain_jitter_db))
    return dst
