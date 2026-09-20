"""The leak battery: can a scalar that hears no words beat your model?

Before any anti-spoofing number may be quoted, every voice-free scalar here is
computed on the SAME files through the SAME chain, and every one of them has to
be at chance. If a statistic containing no voice information separates the
classes better than the model does, you cannot tell whether the model earned its
number or inherited it, and the corpus is disqualified.

The one that already failed, measured in `research/antispoof-findings.md`:

    min-frame-level  AUC 0.954   human vs Polly
    AASIST           AUC 0.855   same files, same chain

A synthesiser writes speech into a buffer, so its pauses are bit-exact digital
zero. A microphone cannot do that -- there is always a noise floor. So the
cleanest "is this synthetic" feature on that corpus was not a voice feature at
all; it was a property of how the FILE was made. That distinction cannot exist
in production, where an undisclosed voice bot reaches Saathi down a carrier that
has already added its own floor.

Pure stdlib on purpose. `wave` reads the audio and the statistics are hand-
rolled, because this has to be runnable in the repo venv with no numpy, no
torch, and no build-time dependency creeping toward the runtime path.

    python -m bench.leak                    # score the project's own voice cache
    python -m bench.leak --group a=dir1 --group b=dir2
"""

from __future__ import annotations

import argparse
import math
import wave
from dataclasses import dataclass
from pathlib import Path

FRAME_MS = 20
SILENT_FLOOR_DBFS = -200.0   # below any real noise floor; only a buffer does this
CHANCE_CEILING = 0.60        # a leak at or under this is tolerable
FULL_SCALE = 32768.0


# --------------------------------------------------------------------------- #
# Reading, without a dependency
# --------------------------------------------------------------------------- #

def _samples(path: Path) -> tuple[list[int], int]:
    """16-bit mono PCM as signed ints, plus the sample rate."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: expected 16-bit, got {w.getsampwidth() * 8}")
        n, rate, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        raw = w.readframes(n)
    out = []
    for i in range(0, len(raw) - 1, 2):
        v = raw[i] | (raw[i + 1] << 8)
        out.append(v - 65536 if v >= 32768 else v)
    return (out[::ch] if ch > 1 else out), rate


def _dbfs(rms: float) -> float:
    return SILENT_FLOOR_DBFS if rms <= 0 else max(
        SILENT_FLOOR_DBFS, 20.0 * math.log10(rms / FULL_SCALE))


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[i]


# --------------------------------------------------------------------------- #
# The scalars. NOT ONE OF THESE CAN HEAR A WORD.
# --------------------------------------------------------------------------- #

def scalars(path: Path) -> dict:
    """Eight voice-free statistics. Every one is a property of the file."""
    xs, rate = _samples(path)
    if not xs:
        raise ValueError(f"{path.name}: empty")

    step = max(1, rate * FRAME_MS // 1000)
    frames = [xs[i:i + step] for i in range(0, len(xs) - step + 1, step)] or [xs]

    levels, zero_frames = [], 0
    for f in frames:
        if not any(f):
            zero_frames += 1
        levels.append(_dbfs(math.sqrt(sum(v * v for v in f) / len(f))))

    peak = max(abs(v) for v in xs)
    rms = math.sqrt(sum(v * v for v in xs) / len(xs))
    # "Speech level" is the loud end, not the mean -- a long pause would drag a
    # mean down and make a synthetic file look like it has a noise floor.
    speech = _pct(levels, 95)

    return {
        # The one that beat AASIST. How far below speech the quietest frame sits.
        "min_frame_rel_db": min(levels) - speech,
        # The one with no innocent explanation: a microphone cannot write zeroes.
        "zero_frame_fraction": zero_frames / len(frames),
        "p5_frame_db": _pct(levels, 5) - speech,
        "peak_dbfs": _dbfs(peak),
        "rms_dbfs": _dbfs(rms),
        "dc_offset": sum(xs) / len(xs) / FULL_SCALE,
        "crest_factor": (peak / rms) if rms > 0 else 0.0,
        "duration_s": len(xs) / rate,
    }


SCALAR_NAMES = ("min_frame_rel_db", "zero_frame_fraction", "p5_frame_db",
                "peak_dbfs", "rms_dbfs", "dc_offset", "crest_factor", "duration_s")


# --------------------------------------------------------------------------- #
# Separability
# --------------------------------------------------------------------------- #

def auc(pos: list[float], neg: list[float]) -> float:
    """P(a random pos ranks above a random neg), ties counted as half.

    Rank-based, so it needs no distributional assumption and no scipy. Returned
    folded to [0.5, 1.0] via max(a, 1 - a): a scalar that separates perfectly
    BACKWARDS is exactly as much of a leak as one that separates forwards.
    """
    if not pos or not neg:
        return float("nan")
    wins = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pos for b in neg)
    a = wins / (len(pos) * len(neg))
    return max(a, 1.0 - a)


@dataclass(frozen=True)
class Verdict:
    admissible: bool
    reason: str
    worst_scalar: str | None
    worst_auc: float | None
    per_scalar: dict
    digital_silence: dict


def battery(groups: dict[str, list[Path]], ceiling: float = CHANCE_CEILING) -> Verdict:
    """Score every scalar across exactly two labelled groups.

    With one group there is no contrast and therefore no AUC. It says so rather
    than returning a number, because a battery that reports a figure it could
    not compute is worse than one that reports nothing.
    """
    measured = {k: [scalars(p) for p in v] for k, v in groups.items() if v}

    # The check that needs no contrast at all. Bit-exact digital silence is not
    # a quiet noise floor, it is the absence of one, and nothing that crossed a
    # carrier can have it.
    silence = {}
    for name, rows in measured.items():
        rendered = [r for r in rows if r["zero_frame_fraction"] > 0]
        silence[name] = {
            "clips": len(rows),
            "with_digital_silence": len(rendered),
            "fraction": len(rendered) / len(rows) if rows else 0.0,
        }

    if len(measured) < 2:
        only = next(iter(silence.values()), {"fraction": 0.0})
        return Verdict(
            admissible=False,
            reason=("only one provenance group, so no AUC exists. "
                    f"{only.get('with_digital_silence', 0)}/{only.get('clips', 0)} "
                    "clips contain bit-exact digital silence, which no recorded "
                    "audio can -- this corpus is entirely synthesiser-written."),
            worst_scalar=None, worst_auc=None,
            per_scalar={}, digital_silence=silence)

    (a_name, a_rows), (b_name, b_rows) = list(measured.items())[:2]
    per = {s: auc([r[s] for r in a_rows], [r[s] for r in b_rows])
           for s in SCALAR_NAMES}
    worst = max(per, key=lambda k: (per[k] if per[k] == per[k] else 0.0))

    ok = per[worst] <= ceiling
    return Verdict(
        admissible=ok,
        reason=(f"every voice-free scalar is at chance (worst {worst} "
                f"{per[worst]:.3f} <= {ceiling})" if ok else
                f"{worst} separates {a_name} from {b_name} at AUC {per[worst]:.3f}, "
                f"above the {ceiling} ceiling, WITHOUT HEARING A WORD. Any model "
                f"number from this corpus is uninterpretable."),
        worst_scalar=worst, worst_auc=per[worst],
        per_scalar=per, digital_silence=silence)


# --------------------------------------------------------------------------- #

def _render(v: Verdict) -> str:
    out = ["", "  Leak battery -- can a scalar that hears no words beat the model?", ""]
    for name, d in v.digital_silence.items():
        out.append(f"  {name:22} {d['with_digital_silence']}/{d['clips']} clips carry "
                   f"bit-exact digital silence  ({d['fraction'] * 100:.0f}%)")
    if v.per_scalar:
        out += ["", f"  {'scalar':22}{'AUC':>8}   verdict"]
        out.append("  " + "-" * 46)
        for s in SCALAR_NAMES:
            a = v.per_scalar.get(s, float("nan"))
            mark = "leaks" if a > CHANCE_CEILING else "at chance"
            out.append(f"  {s:22}{a:>8.3f}   {mark}")
    out += ["", f"  VERDICT: {'ADMISSIBLE' if v.admissible else 'DISQUALIFIED'}",
            f"  {v.reason}", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bench.leak")
    ap.add_argument("--group", action="append", default=[], metavar="NAME=DIR",
                    help="a labelled directory of 16-bit wavs; pass twice to score")
    ap.add_argument("--ceiling", type=float, default=CHANCE_CEILING)
    args = ap.parse_args(argv)

    groups: dict[str, list[Path]] = {}
    if args.group:
        for spec in args.group:
            name, _, d = spec.partition("=")
            groups[name] = sorted(Path(d).glob("*.wav"))
    else:
        cache = Path(__file__).resolve().parent.parent / ".voice-cache" / "wav"
        groups["voice-cache"] = sorted(cache.glob("*.wav"))

    if not any(groups.values()):
        print("\n  no wavs found. Run `python -m saathi.cli warm` first.\n")
        return 2

    v = battery(groups, ceiling=args.ceiling)
    print(_render(v))
    return 0 if v.admissible else 1


if __name__ == "__main__":
    raise SystemExit(main())
