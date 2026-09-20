# You cannot channel-engineer your way to a valid corpus

**Verdict: two synthesisers stay separable by statistics that hear no words, no
matter how carefully you match the channel. Every patch exposes the next
confound. Real recorded audio is the only fix.**

Measured on this machine with `bench/leak.py`, `bench/channel.py` and
`bench/corpus.py` — all pure stdlib, no numpy, no torch.

## The corpus

70 utterances per engine, **content-matched** (both engines say the same 30
support-desk sentences) and **channel-matched** (one identical chain, line noise
on both sides or neither). 598 seconds — just under ten minutes.

- **polly** — Amazon Polly neural, voice *Kajal*
- **sapi** — Windows SAPI 5, voice *Microsoft Zira Desktop*

Neither side is human. The directory is called `sapi`, never `human`, because
labelling a synthesiser's output "human" is precisely the error that disqualified
the corpus in [antispoof-findings.md](antispoof-findings.md). This is the second
synthetic engine that doc asks for, not a human class.

## What happened, in three stages

Each row is the worst voice-free scalar — a statistic that cannot hear a word —
separating the two engines. Ceiling for admissibility is AUC ≤ 0.60.

| stage | scalars leaking | worst leak | AUC |
|---|---|---|---|
| raw, no channel | **8 of 8** | `min_frame_rel_db` | **1.000** |
| + telephone chain (band-limit, G.711, 20 dB noise) | 6 of 8 | `rms_dbfs` | **1.000** |
| + per-clip level jitter (±9 dB, same draw both sides) | 6 of 8 | `p5_frame_db` | **0.934** |

**Verdict at every stage: DISQUALIFIED.**

### Stage 1 — raw: everything leaks

All eight scalars sit at or near 1.000. The starkest is digital silence:

```
polly    0/70 clips carry bit-exact digital silence  (0%)
sapi    70/70 clips carry bit-exact digital silence  (100%)
```

SAPI writes speech into a buffer and leaves its pauses at exact zero. Polly's
output carries a low dither floor, so it does not. That single binary property
separates the classes perfectly, and it is a fact about **how the file was
written**, not about either voice.

### Stage 2 — the chain fixes silence and exposes level

The telephone chain works, and the numbers say so precisely:

| scalar | raw | channel-matched |
|---|---|---|
| `zero_frame_fraction` | 1.000 | **0.500** — exactly chance |
| `dc_offset` | 1.000 | **0.526** |

Two confounds killed outright. But `rms_dbfs` rose to **1.000**: the engines
render at systematically different loudness, and the chain deliberately does not
normalise level. That is not an oversight — [antispoof-findings.md](antispoof-findings.md)
measured RMS normalisation taking spontaneous-human-vs-Polly from AUC 0.825 to
0.451, i.e. chance, by erasing the signal along with the confound.

### Stage 3 — level jitter fixes loudness and exposes dynamics

Randomising per-clip gain (not equalising it) over ±9 dB, drawn from the same
distribution for both classes:

| scalar | channel-matched | + level jitter |
|---|---|---|
| `rms_dbfs` | 1.000 | **0.605** |
| `peak_dbfs` | 0.997 | **0.701** |
| `p5_frame_db` | 0.940 | 0.934 |
| `min_frame_rel_db` | 0.934 | 0.927 |
| `crest_factor` | 0.928 | 0.871 |
| `duration_s` | 0.902 | 0.902 |

Level is fixed. The survivors are **relative** measures — how far below speech an
engine's quiet frames sit, its crest factor, how long it takes to say a fixed
sentence. Gain cannot touch any of them, because they are ratios. They describe
each engine's intrinsic dynamics and speaking rate, and they are still perfectly
diagnostic of which synthesiser produced the clip.

## Why this is the important result

The obvious reading of stage 1 is "add a noise floor and the problem goes away."
It does not. It goes away *for that scalar*, and the next one takes over. Three
rounds of honest engineering moved the worst leak from 1.000 to 0.934 and never
got within sight of the 0.60 ceiling.

The reason is structural. Two deterministic generators produce two tight,
separable distributions on every axis you can measure. Real speech does not: a
room, a handset, a person's distance from the microphone and their own voice vary
per utterance and per speaker, and that variation is what makes the confounds
stop tracking the label. **You cannot manufacture it, because manufacturing it is
just choosing the answer.**

## What this does and does not establish

**Does:** the leak battery works, the channel chain works, and the pipeline runs
end to end on a laptop with no scientific stack. The moment real recorded audio
exists, `python -m bench.leak --group real=DIR --group polly=DIR` is one command.

**Does not:** anything about any anti-spoofing model. No model was run here. This
measures the corpus, which is the thing you have to clear *before* a model number
means anything.

**Also does not:** rule out that a well-built real corpus still leaks. It might.
The battery is the gate either way.

## Reproduce

```bash
python -m bench.corpus --build -n 70
python -m bench.leak --group polly=bench/corpus/raw/polly     --group sapi=bench/corpus/raw/sapi
python -m bench.leak --group polly=bench/corpus/chained/polly --group sapi=bench/corpus/chained/sapi
```

`bench/corpus/` is gitignored — 56 MB, rebuildable in a few minutes.

## Next

Ten minutes of genuinely recorded speech on an actual phone, on the human side.
That is the only input this pipeline is still missing, and every number above is
an argument for why nothing else substitutes for it.
