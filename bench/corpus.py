"""Build a two-engine, content-matched, channel-matched corpus.

WHAT THIS IS NOT: a human corpus. Every clip here is written into a buffer by a
synthesiser. Windows SAPI is not a person, and labelling its output "human"
would rebuild the exact machine-vs-machine corpus that
`research/antispoof-findings.md` spent three investigations disqualifying. The
directory is called `sapi`, never `human`, for that reason.

WHAT IT IS: the second synthetic engine the findings doc asks for --

    "add at least two more TTS engines to the synthetic side ... and hold out by
     engine: train/calibrate on engines A,B, evaluate on unseen engine C. The
     number that matters is unseen-engine accuracy."

Two things are held constant so the comparison means something:

  - CONTENT. Both engines say the SAME sentences. Vary the words and the engine
    at once and you cannot tell which one the scalar is reading.
  - CHANNEL. Both sides go through one identical chain in bench/channel.py, with
    line noise on both or neither.

    python -m bench.corpus --build          # render and channel both engines
    python -m bench.corpus --build -n 40    # smaller, for a quick pass
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from bench.channel import process_file

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"

# Support-desk lines, varied in length and shape so the corpus is not one
# sentence repeated. Digits are spelled out: a spoken digit run would trip the
# verification backstop if these were ever replayed down a live call.
LINES = [
    "Billing department, this is Priya speaking, how can I help you today",
    "Thanks for holding, I know it has been a while, let me pull that up",
    "Right, I can see the order here, it was delivered well outside the window",
    "Sorry, could you say that again, the line dropped for a second there",
    "Let me just check what the system will let me do for you on this one",
    "That does look like a duplicate charge, I can see two identical entries",
    "I am going to put you on a brief hold while I check with my supervisor",
    "Okay, I am back with you, thank you for waiting so patiently",
    "We can process a refund to the original payment method, that takes a few days",
    "Alternatively I can offer store credit, which would be available immediately",
    "I understand that is frustrating, and I would feel the same in your position",
    "Can you confirm the address the order was delivered to, just the street name",
    "It looks like the driver marked it delivered but never actually handed it over",
    "I have escalated this to the delivery partner and flagged it as a priority",
    "Is there anything else I can help you with while I have you on the line",
    "You should receive a confirmation email within the next ten minutes or so",
    "Unfortunately that is outside what I am able to authorise at my level",
    "Let me transfer you to the team that handles those requests directly",
    "Your patience is appreciated, we are experiencing higher than usual volume",
    "I am noting on the account that you called about this today",
    "The system is running slowly this morning, please bear with me",
    "That charge should drop off your statement within three working days",
    "I can see previous contact about this, so let me read through the notes",
    "We do apologise for the inconvenience this has caused you",
    "I will send you a summary of what we discussed on this call",
    "Could you spell the surname for me, I want to make sure I have it right",
    "There is a note here saying the item was out of stock at the warehouse",
    "I have applied that to your account and you should see it reflected shortly",
    "If it does not arrive by then, please do call us back and ask for me",
    "Thank you for being a customer with us, and sorry again for the trouble",
]


def _sapi_render(pairs: list[tuple[str, Path]]) -> int:
    """Render through Windows SAPI at 16 kHz mono, in one process.

    One PowerShell invocation for the whole batch: per-line process spawn costs
    far more than the synthesis itself.
    """
    manifest = [{"text": t, "path": str(p)} for t, p in pairs]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(manifest, fh)
        mpath = fh.name

    ps = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$items = Get-Content -Raw -Encoding UTF8 '{mpath}' | ConvertFrom-Json
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$n = 0
foreach ($it in $items) {{
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SetOutputToWaveFile($it.path, $fmt)
    $s.Speak($it.text)
    $s.SetOutputToNull()
    $s.Dispose()
    $n++
}}
Write-Output $n
"""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True)
    Path(mpath).unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"SAPI render failed: {r.stderr[:400]}")
    return sum(1 for _, p in pairs if p.exists())


def _polly_render(pairs: list[tuple[str, Path]], voice_key="rep") -> int:
    from saathi import voice as V
    made = 0
    for text, dest in pairs:
        if dest.exists():
            made += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        V.write_wav(dest, [V.synthesize(text, voice_key)])
        made += 1
    return made


def build(n: int = 30, snr_db: float = 20.0) -> dict:
    lines = (LINES * ((n // len(LINES)) + 1))[:n]
    raw, out = CORPUS / "raw", CORPUS / "chained"
    stats = {}

    sapi_pairs = [(t, raw / "sapi" / f"{i:03d}.wav") for i, t in enumerate(lines)]
    (raw / "sapi").mkdir(parents=True, exist_ok=True)
    todo = [(t, p) for t, p in sapi_pairs if not p.exists()]
    if todo:
        _sapi_render(todo)
    stats["sapi_raw"] = sum(1 for _, p in sapi_pairs if p.exists())

    polly_pairs = [(t, raw / "polly" / f"{i:03d}.wav") for i, t in enumerate(lines)]
    stats["polly_raw"] = _polly_render(polly_pairs)

    # One chain, one seed policy, both sides. The seed varies per clip so the
    # noise is not identical across files, but the DISTRIBUTION is the same for
    # both engines -- noise that differed by class would be the confound itself.
    for engine, pairs in (("sapi", sapi_pairs), ("polly", polly_pairs)):
        done = 0
        for i, (_, src) in enumerate(pairs):
            if not src.exists():
                continue
            process_file(src, out / engine / src.name, snr_db=snr_db, seed=i)
            done += 1
        stats[f"{engine}_chained"] = done

    stats["seconds"] = _duration(out)
    return stats


def _duration(root: Path) -> float:
    import wave
    total = 0.0
    for p in root.rglob("*.wav"):
        try:
            with wave.open(str(p)) as w:
                total += w.getnframes() / w.getframerate()
        except Exception:
            pass
    return round(total, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bench.corpus")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("-n", type=int, default=30, help="utterances per engine")
    ap.add_argument("--snr", type=float, default=20.0)
    args = ap.parse_args(argv)

    if not args.build:
        ap.print_help()
        return 0

    print(f"\n  building {args.n} utterances per engine, channel-matched at "
          f"{args.snr} dB SNR ...\n", flush=True)
    s = build(args.n, args.snr)
    for k, v in s.items():
        print(f"    {k:16} {v}")
    print(f"\n  corpus at {CORPUS}")
    print("  NOTE: both engines are synthesisers. Neither side is human.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
