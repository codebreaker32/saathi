# Can SYNTHESIS become a real measurement?

**Verdict: not on this corpus. Keep the amber `not measured yet` marker.**

Findings from a multi-agent investigation (three ground agents that actually
downloaded corpora and ran models, plus adversarial review). Stopped early once
the conclusion was settled; the remaining agents were refining the bench design,
not the verdict.


## Ground: I sourced real human speech (OpenSLR SLR83: 96 utterances, 8 speakers, CC-BY-
SA), built a stdlib-only telephone channel chain, ran a real anti-spoofing
model (AASIST, ASVspoof2019-LA weights) on CPU end to end, and added a second
unseen generator (Piper/VITS) saying the exact same sentences. The
measurements say the amber "(scripted)" marker is currently the honest
rendering and must stay.  FOUR FINDINGS DECIDE IT.  (1) The trap is real and
measurable. Point AASIST at today's all-Polly corpus and it ranks the demo's
"human rep" (Polly-Kajal) as MORE synthetic than the "bot" (Polly-Danielle):
AUC(bot>rep) = 0.317 raw, 0.383 channel-matched; 11/12 rep clips called
synthetic. Wiring the model today does not merely fail to help, it inverts.
(2) Channel matching costs two thirds of the apparent skill. Studio human vs
Polly: AUC 0.938, EER 7.7%. Both sides through one chain: AUC 0.675-0.689, EER
36%. Polly scores barely move (median +2.17 -> +2.16); human scores move +4.5
(median -3.07 -> +1.48). Asymmetric movement means the model was reading
recording-chain cleanliness as bonafide-ness,

**Recommendation.** DO NOT remove SYNTHESIS from SCRIPTED_FAMILIES. Not yet, and not on this
corpus. The marker is currently the most honest pixel in the UI, and replacing
it with a measured number would trade a stated limitation for an unstated one.
Do three things instead.  1. SHIP THE MODEL AS A BENCH, NOT AS A FAMILY. Build
the corpus, build the chain, run AASIST offline, and publish a table in README
with intervals and the generator list. That is a real, defensible, quotable
result: "on 96 channel-matched utterances from 8 speakers against 2
generators, AUC 0.69 [0.55, 0.83] speaker-clustered; at the operating point
that never calls a synthetic clip human, it supplies its vote for a real human
18% of the time [4%, 35%]." That is a much better artefact than a green bar,
because it is the number a skeptical reader would have asked for.  2. KEEP THE
DEMO'S SYNTHESIS SCRIPTED AND SAY WHY, WITH THE BENCH AS THE REASON. Change
the tooltip from "scripted stand-in, not a measured model" to "scripted; the
measured model is in the bench and would vote here only ~18% of the time for a
real rep - see README". That converts the amber marker from an apology into a
finding.  3. FIX THE THING THE COUNTERFACTUAL EXPOSED. The scoreboard's 3/4
human fetches are a function of a number typed into a yaml: all three fetches
need exactly {CONTINGENCY, DUPLEX, SYNTHESIS}, and no audio family can
currently survive honest measurement. That is a fusion problem, not an audio
problem. Either the gate needs a fifth non-audio family that can actually vote
for a live human (prosodic turn-taking latency jitter, backchann

**Costs.** DISK - torch CPU venv (torch + numpy + soundfile + onnxscript + piper): 1.1
GB, of which torch alone is 769 MB. Build-time only if you export to ONNX. -
Runtime alternative: onnxruntime 67 MB + numpy ~25 MB. AASIST as ONNX is 1.6
MB (1.28 MB as .pth, 0.30M params). - Corpus: SLR83 zips 98-166 MB each; 96
extracted 48 kHz wavs = 56 MB; 96 Piper utterances = 20 MB; after the chain
each utterance is ~3.5x smaller (426 KB -> 124 KB). A 30-speaker x
10-utterance channel-matched corpus lands around 40

**Risks and theatre warnings.**

- THEATRE: test_synthesis_separates_the_bot_from_the_rep on today's corpus. It
  would fail today - I measured AUC 0.317, the rep scores MORE synthetic
  than the bot - but the tempting fix is to flip the sign or the threshold
  until it passes, at which point you have a test that certifies the model
  for distinguishing Kajal from Danielle. Both are Polly.
- THEATRE: any fixed threshold on AUC/EER computed on the same data the
  decision threshold was tuned on. Circular, and it will look excellent. The
  suite must assert that the eval generator and eval speakers are disjoint
  from the dev ones, by manifest, not by comment.
- THEATRE: bootstrapping over utterances instead of speakers. Measured here:
  utterance bootstrap gives AUC CI [0.58, 0.80], speaker bootstrap [0.55,
  0.83], and on the decision-relevant rate the speaker-clustered CI is [4%,
  52%] on a point estimate of 18%. ICC is 0.54-0.79, so 96 utterances are
  worth 10-14. An utterance-denominated interval is a manufactured tight
  number, and it is the default every stats library gives you.
- THEATRE: test_model_loads, test_score_is_a_float, test_score_in_range. All
  pass with random.random() in place of the model. Only meaningful paired
  with the audio-swap mutation control.
- THEATRE: test_scenarios_still_do_not_false_fetch after wiring the model. It
  passes because the other four families carry it - I verified that forcing
  SYNTHESIS to say 'human' on all three bot scenarios still produces zero
  false fetches. Green here tells you nothing about SYNTHESIS.
- THEATRE: test_real_rep_still_fetches. Measured: all three fetches need
  exactly {CONTINGENCY, DUPLEX, SYNTHESIS}, so this test is really asserting
  that the scripted 0.11 is still in the yaml. After honest wiring it will
  go red for a correct reason and the temptation will be to fix the test.
- THEATRE: golden-score snapshots (assert score == -3.07). They pin the
  implementation, not the claim, and they stay green through a corpus swap
  that invalidates everything.
- THEATRE: quoting the model's published ASVspoof2019-LA EER (~0.8%) anywhere
  near the product's numbers. That is measured on the data it was trained to
  fit, in studio conditions, and I measured 36% EER on channel-matched real
  speech - a 45x gap.


## Ground: Real human speech is downloaded, converted, and in the corpus: 32 human
utterances (16 LibriSpeech US read speech, 16 Common Voice Indian-accented
English) plus the 16 Polly files, all pushed through one identical telephone
channel and written as mono 16-bit 16000 Hz WAV — byte-format identical to the
existing Polly cache.  Solved without ffmpeg/sox: FLAC and MP3 both decode
through the `soundfile` wheel, which bundles libsndfile 1.2.2 (build-time
only; the channel code itself is pure stdlib). G.711 mu-law is implemented in
pure Python and validated bit-exact against `audioop` over all 65536 int16
values, so the pipeline does not die when audioop is removed in 3.13.
Bandwidth trick that matters: both corpora are tar archives read as sequential
streams and aborted early. LibriSpeech gave 80 FLAC files from 7.3 MB of a
337.9 MB archive. Common Voice gave 48 Indian-accented clips from 180 MB of a
717 MB tar, filtered on the accent column of dev.tsv.  The channel chain
demonstrably reshapes the spectrum: the 3400-8000 Hz band drops from
-19.6/-23.4/-20.6 dB (source-dependent, 3.8 dB spr

**Recommendation.** Take the corpus and the channel chain; do NOT turn the SYNTHESIS bar green
yet.  1. Adopt the corpus and channel code. LibriSpeech dev-clean (CC BY 4.0,
attribution required) plus Common Voice 17 en filtered to "India and South
Asia" (CC0-1.0, no attribution required). Both licences permit this use, and
the Indian-accented half is the India-facing bonus.  2. Ship the channel chain
with line noise ON. The measured point is that band-limit + G.711 alone leaves
channel provenance at 75% recoverable on a same-speaker control. Default to 20
dB SNR (59.4%); use 15 dB if you want channel leakage at chance and can accept
the extra noise. Re-tune this once a real model is attached — the operating
point is a measured curve, not a constant.  3. Do NOT train a synthesis
detector on this corpus. Every synthetic sample is Polly. A model trained here
learns Polly, and the case that matters is an undisclosed bot on an engine you
have never seen. Instead use this corpus as a CALIBRATION and VALIDATION set
for a pretrained anti-spoofing model (AASIST or RawNet2 trained on ASVspoof
2019 LA). That model has seen 19 attack types; this corpus tells you where its
threshold sits on your channel.  4. Before SYNTHESIS counts as a real vote,
add at least two more TTS engines to the synthetic side (Azure, ElevenLabs,
Coqui — anything not Polly) through the identical chain, and hold out by
engine: train/calibrate on engines A,B, evaluate on unseen engine C. The
number that matters is unseen-engine accuracy, not in-corpus accuracy.  5.
Interim product call: keep SYNTHESIS amber and keep it out of the th

**Costs.** DOWNLOAD (one-time, already done): ~192 MB total — LibriSpeech 7.3 MB (of a
337.9 MB archive, aborted early), Common Voice audio 180.1 MB (of a 717.4 MB
tar, aborted early), cv_dev.tsv 4.9 MB. Well inside the "few hundred MB at
most" bar, and the early-abort streaming is what keeps it there — a naive
download would have been 1.06 GB.  DISK: corpus/ 15 MB (48 utterances x2
versions). Retained source audio 10 MB (raw_librispeech 7.5 MB +
raw_commonvoice_in 2.5 MB). Build-time venv 91 MB (soundfile

**Risks and theatre warnings.**

- The corpus-level confound is NOT fixed and is the dominant risk. Every
  synthetic sample is Polly; every human sample is LibriSpeech or Common
  Voice. A detector trained on this separates Polly from two specific human
  corpora. Against an undisclosed bot on an unseen engine — the exact case
  SYNTHESIS is supposed to catch — its accuracy is unknown and could be near
  chance. The channel work removes one confound and leaves this one
  untouched.
- Channel equalisation is improved but not complete. Same-speaker channel-only
  separability is 59.4% at the shipped 20 dB default against a 50% chance
  floor. A strong model can still read some channel. Only the 15 dB setting
  reached chance (45.8%), and that costs extra noise.
- Control-experiment sample sizes are small: n=16 per class for
  control_channel.py, n=12 for the sweep. Binomial noise on 24-32 samples is
  roughly +/-10 points, so 59.4% vs 50% and 45.8% vs 50% are both weakly
  separated from chance. These numbers indicate direction; they are not
  tight estimates. Rerun at n>=100 before quoting them anywhere load-
  bearing.
- The 'VOICE SIGNAL' column in the sweep is a 24-band nearest-centroid
  classifier, NOT an anti-spoofing model. It shows a coarse spectral cue
  survives the noise; it says nothing about whether a real model's cues
  survive. In the full analyse.py run at 15 dB that same proxy fell to
  54.2%, which is a warning that aggressive noise may erode the very cues
  the real model needs. The operating point must be re-derived once a real
  model exists.
- Line noise at 15-20 dB SNR is realistic for a mobile call but is noisier
  than ASVspoof LA training data. A pretrained AASIST/RawNet2 may degrade on
  it. Mitigation is to calibrate the threshold on this corpus rather than
  assuming the published EER transfers — but that calibration has not been
  done.
- Adding noise partially refills the 3400-8000 Hz band (-38 dB, up from -50 dB
  with noise off) because the post-upsample filter is deliberately gentler
  (1 HP + 2 LP sections). It is uniform across all three sources (1.3 dB
  spread) so it is channel, not source, information — but if a future model
  turns out to key on it, tighten the final band_limit.
- LibriSpeech is CC BY 4.0 and REQUIRES attribution. Common Voice is CC0-1.0
  and does not. If these files ship in any artifact, the LibriSpeech
  attribution obligation travels with them.
- soundfile/libsndfile is a build-time dependency for FLAC and MP3. It is not
  in the repo venv and must not silently become a runtime one — the runtime
  chain reads WAV via stdlib wave only. If someone points build_corpus.py at
  the live path, they will drag numpy into production.


## Ground: AASIST runs on this CPU box and genuinely discriminates humans from machines -
but the headline number is dominated by preprocessing, not by the model, and
that is the finding that matters.  I sourced three real human corpora
(LibriSpeech dev-clean; OpenSLR-83 Irish English; AMI headset = spontaneous
meeting speech) and synthesised a modern non-Polly TTS control with Piper/VITS
speaking the actual Saathi scenario lines. Every source goes through one
identical channel chain.  THE TRAP IS REAL AND MEASURED. AASIST scores polly-
rep (the "human rep", Polly Kajal) at median P(human)=0.0275 and polly-bot
(Polly Danielle) at 0.0215, and cannot tell them apart (AUC 0.650, 95% CI
[0.317,0.933] - includes chance). It separates humans from machines, not
generators. Wiring it in today flips real_rep's SYNTHESIS from llr +1.5 to
-2.0: a 3.5-point swing on a gate needing score >= 0.5 AND 3 agreeing
families. real_rep stops fetching. The demo breaks.  THE BIGGER FINDING -
PREPROCESSING BEATS THE MODEL. My first chain RMS-normalised both sides to -26
dBov, which is what "same channel, same level" se

**Recommendation.** Do NOT turn the SYNTHESIS bar green yet. The amber "scripted" marker is
currently MORE honest than any number I can produce, and DESIGN.md already
says why ("Measuring it only against the voices used to build the corpus would
produce a beautiful, meaningless number").  Three things are true at once:  1.
A real anti-spoofing model works here. AASIST via ONNX is the right pick: MIT-
licensed, weights ship in the repo, 1.48 MB, 156-307 ms/utt on CPU, 398 MB
RSS, no torch at runtime. It is calibrated (graded scores, not saturated), it
passes both falsification controls, and it detects modern neural TTS
(Piper/VITS, AUC 0.950) better than it detects Polly.  2. It cannot be
switched on until the corpus changes. polly-rep scores 0.0275 -
indistinguishable from polly-bot at 0.0215. The model is right and the corpus
is wrong. Turning this on today makes real_rep stop fetching, which is a worse
failure than the stand-in.  3. The number you get is decided by your gain
stage, not your model. This is the part I would flag hardest, because it is
invisible and it looks like a modelling result. Normalising both sides to a
common level is the obviously-correct fairness move, and it is exactly what
destroys the model on real-world audio: spontaneous-human-vs-Polly AUC goes
0.825 -> 0.451 (chance) purely from RMS normalisation. Band-limit and codec
both sides; do NOT add your own AGC. A real telephone line has already done
that.  Sequencing I would suggest: (a) record ~10 lines of the rep script with
an actual person on an actual phone - ten minutes of work, and it is the only
thing that makes

**Costs.** DOWNLOAD (one-time, build machine only) - torch+torchaudio CPU wheels 196 MB,
needed ONLY to export the .onnx; never on the runtime box. - AASIST repo incl.
pretrained weights ~2 MB. - Corpora, only for the calibration set: LibriSpeech
dev-clean 337 MB, OpenSLR-83 Irish 164 MB, AMI 4 headsets 294 MB. Not needed
in production.  INSTALL SIZE (runtime) - onnxruntime 67 MB + numpy 43 MB (+
soundfile, small). A clean venv with exactly these measured 161 MB total,
torch and transformers verifiably abs

**Risks and theatre warnings.**

- THE ONE THAT WOULD BITE FIRST: switching the family on before the human-
  truth personas have real audio actively breaks the demo. AASIST scores
  polly-rep at 0.0275, so real_rep's SYNTHESIS flips +1.5 -> -2.0, costing a
  family and 3.5 points on a gate needing score >= 0.5 and 3 agreeing
  families. real_rep stops fetching. This is not a tuning issue; it is the
  model being correct about a corpus that is wrong.
- Preprocessing silently decides the answer. RMS-normalising both sides - the
  intuitively correct fairness move - drops spontaneous-human-vs-Polly from
  AUC 0.825 to 0.451 (chance). Anyone who later adds an AGC or a loudness-
  normalisation step anywhere upstream will destroy this family without any
  test failing. Pin the chain and regression-test it on AMI specifically.
- EER 16.9% (AUC 0.898) in the recommended config is mediocre, and n on the
  Polly side is only 28 files (5 bot, 12 rep). The rep-vs-bot control CI is
  [0.317,0.933] - wide enough that it confirms nothing strongly, it merely
  fails to contradict. Do not quote a point estimate to anyone.
- One of four real spontaneous speakers (AMI IS1000a) still scores 0.09 - i.e.
  a real human confidently called synthetic even in the good configuration.
  At 1-in-4 speaker-level failure the family must never be able to veto a
  fetch alone, which is why the +/-2.0 cap matters.
- My human corpora are proxies, not the target domain: LibriSpeech and SLR-83
  are read speech, AMI is meeting speech on a close-talk headset. None is a
  support rep on a real telephone line with real network codec, packet loss
  and hold-music bleed. The simulated G.711 chain is not the same as audio
  that genuinely traversed a carrier.
- Generalisation to unseen generators remains the open problem DESIGN.md
  already names. I tested exactly two generators (Polly, Piper/VITS). Piper
  was detected better than Polly, which is encouraging but is two data
  points; whatever a well-funded adversary ships next is untested by
  construction.
- AASIST's pretrained weights are trained on ASVspoof2019 LA, whose bonafide
  half is VCTK - read studio speech. That is a domain match with my
  LibriSpeech/SLR-83 evaluation and may flatter the read-speech numbers. The
  AMI result is the more trustworthy one and it is lower (0.824 vs 0.875).
- Three candidate models that a reasonable engineer would reach for first are
  actively harmful here and all of them look fine if you only score Polly:
  MelodyMachine-V2 is inverted, motheecreator separates two Polly voices at
  AUC 0.133 while posting the best headline EER of any model tested, and
  AST-ASVspoof5 calls the IVR menu human. A headline number without the rep-
  vs-bot and corpus-vs-corpus controls is worse than no number.


## Adversarial: BROKE IT — severity High on the deliverable, not on the safety call. The recommendation to keep SYNTHESIS amber and out of the 3-of-5 quorum is correct and my work reinforces it. What breaks is the artifact proposed as the replacement — report 3's README bench table, explicitly offered as "a real, defensible, quotable result" and "a much better artefact than a green bar." It is neither: holding model, channel and synthetic side fixed, corpus composition moves AUC 0.481-0.869 and EER 25.0%-53.9%, spans wider than every quoted interval. Severity is high because this is the next thing that would ship, it would ship under the authority of a measurement, and it would be harder to detect than the hardcoded 0.88 it replaces — a reader can discount an amber "scripted" marker correctly at zero cost, but cannot discount a bootstrap CI whose dominant error term is absent from it. Compounding factors: the corpus has zero Indian speakers for an India-facing product whose demo rep is Kajal, and dialect group is the dominant effect; the failure mode is per-person and permanent (4 of 8 speakers refused 12/12) rather than per-call and random, so it will surface as an accessibility complaint from specific users rather than as a degraded metric. Mitigating: no production code is affected, nothing under /home/aman/projects/saathi was modified, the corpus and channel work is sound and reusable, and the single sensitivity test in control #1 catches the whole class in under a second.

**Attack.** I attacked the statistics, not the audio. The three reports agree on "keep the
bar amber" — that call survives. What does not survive is the artifact report
3 proposes shipping *instead*: a README bench table quoting "AUC 0.69 [0.55,
0.83] speaker-clustered" and "supplies its vote for a real human 18% of the
time [4%, 35%]," described as "a real, defensible, quotable result." I broke
that table. All code in /tmp/claude-1000/-home-aman-
projects/9b9c1c9b-916b-42a2-9c98-5a24a032c59f/scratchpad/stat/ (icc.py,
auc.py, gen.py, spk.py, mix.py, power.py, robust.py, repro.py), run against
the agents' own score dumps.  FIRST, I VALIDATED I WAS RUNNING THEIR PIPELINE.
repro.py on aasist_absaa.json reproduces report 3's headline to the decimal:
pooled rate 17.7% (they said 18%), speaker-bootstrap 95% CI [4%, 34%] (they
said [4%, 35%]). Same data, same estimator. Everything below is therefore an
attack on their number, not on a number of mine.  (1) EFFECTIVE SAMPLE SIZE IS
5-19, NOT 96-140. One-way random-effects ANOVA on the logit scores, AMI group
(the only one with real per-speaker labels): MS_between 397.85 vs MS_within
4.76, ICC(1) = 0.846. Design effect 1+(m-1)*ICC = 12.85 at m=15. **60 AMI
utterances carry the information of 4.7 independent draws.** On the 8-speaker
SLR83 set the pass-rate variance across speakers is 0.0885 against a binomial
expectation of 0.0172 — **overdispersion 5.1x, so 96 utterances are worth
18.7.** Report 3 estimated ICC 0.54-0.79 and said "96 utterances are worth
10-14," so it saw this; it then published intervals anyway.  (2) THE CIs ARE
ON THE WRONG DE

**Why the number would be meaningless.** Because the published interval would quantify the one source of variation that
barely matters and stay silent on the one that dominates.  A confidence
interval is a statement about resampling. "[4%, 35%]" answers: if I drew 8
more speakers *from the same population by the same process*, where would the
estimate land? But the 8 speakers are a convenience sample — whatever SLR83
and AMI happened to contain — and the process that produced them is not the
process that produces Saathi's callers. The interval is conditional on a
corpus composition that was never sampled, and I showed that composition moves
the point estimate across 0%-35% (and AUC across 0.48-0.87) while the model,
channel and synthetic side are held byte-identical. A reader sees "[4%, 35%]"
and infers the remaining uncertainty is 31 points of sampling noise. The
actual dominant term is a 35-point corpus-selection effect carry

**Control that catches it.** The single control that catches all of this, and that none of the three agents
ran: **recompute the headline statistic on every leave-k-speakers-out subset
of the corpus and print the span next to the confidence interval. If the span
exceeds the CI width, the CI is not reportable.** Here that test fails loudly
and cheaply — 70 subsets, sub-second, AUC 0.481-0.869 against a quoted CI
width of 0.28. Make it a test that fails the build, not a paragraph in a risk
list.  Five concrete controls, in the order I would add them:  1. CORPUS-
COMPOSITION SENSITIVITY GATE (the one above). Assert `span(leave-one-group-
out) < width(cluster_CI)`. Publish both numbers or neither. This is the test
that would have stopped the bench table.  2. REPORT THE EFFECTIVE n, NOT THE
NOMINAL n, NEXT TO EVERY NUMBER. Compute ICC and the design effect in the same
function that computes the statistic, and have the repo


## Adversarial: BROKE IT — severity critical

**Attack.** DIGITAL SILENCE SURVIVES THE CODEC. G.711 mu-law floors |x|<4 LSB to exactly
zero: ulaw2lin(lin2ulaw(k))==0 for k=0,1,2,3 (verified). Polly renders speech
into a buffer, so its pauses are exact digital zero, and the recommended chain
(band-limit 300-3400 -> 8kHz -> mu-law -> 16kHz, no level norm) passes them
through bit-exactly.  MEASURED after the full recommended chain, fraction of
bit-exactly-zero 20ms frames / level of quietest frame relative to speech:
Polly        0.068  /  -309 dB   LibriSpeech  0.006  /   -54 dB   Irish SLR-83
0.000  /   -66 dB   AMI spont.   0.000  /   -31 dB   Piper/VITS   0.000  /
-68 dB -309 dB is not a noise floor, it is the absence of one. No audio that
crossed a carrier can have it.  HEAD-TO-HEAD, identical files, identical chain
(n_human=140, n_polly=28, n_piper=20):
AASIST(0.30M params)   min-frame-level (1 scalar, no voice content)   AUC
human vs Polly                    0.855                     0.954   AUC human
vs Piper (unseen)           0.959                     0.856 A scalar measuring
"does this file contain digital silence" BEATS the anti-spoofing model by 10
AUC points on the exact comparison that would be quoted as the headline.  THE
MODEL ITSELF RIDES IT. AASIST median P(human) bucketed by that same voice-free
scalar:   digitally silent (<-200dB) 0.035 | -200..-60dB 0.067 | -60..-40dB
0.274 | >-40dB 0.990 Spearman(P(human), min-frame-level) = 0.454 over 188
clips. The 0.990 bucket is 48/48 human (AMI room tone).  UNSEEN-ENGINE
COLLAPSE. Threshold picked on Polly-vs-human: 94.6% in-corpus accurac

**Why the number would be meaningless.** Because on this corpus the best-performing detector is one that contains no
voice information at all, you cannot tell whether a model number was earned or
inherited. A single scalar - the level of the quietest frame relative to
speech - scores AUC 0.954 human-vs-Polly, beating AASIST's 0.855 on the
identical files through the identical chain. Any number produced here is
therefore uninterpretable: it is consistent with a model reading synthesis
artifacts AND with a model reading "this file was written by a synthesiser
rather than recorded by a microphone."  The confound is not merely correlated
with the label, it is PERFECTLY aligned with it and it is a property of the
FILE, not the VOICE. Every synthetic clip was rendered to a buffer; every
human clip was captured by a transducer with a noise floor. That distinction
cannot exist in production, where an undisclosed voice bot reaches Saath

**Control that catches it.** A LEAK BATTERY THAT GATES THE MODEL NUMBER, plus a floor injection and an
engine holdout. Concretely:  1. LEAK BATTERY (the control that would have
caught this). Before any model number may be quoted, run a fixed set of voice-
free scalars through the IDENTICAL chain on the IDENTICAL split and require
every one to be at chance (AUC <= 0.60), speaker-clustered: min-frame-level
relative to speech, fraction of bit-exactly-zero frames, 5th-percentile frame
level, peak dBFS, RMS dBFS, DC offset, crest factor, duration. Assert as a
test: max(leak AUC) must be BELOW the model's AUC by a stated margin, and
ideally near 0.5. On today's corpus min-frame-level = 0.954 vs AASIST 0.855,
so the battery fails and the corpus is disqualified - which is exactly the
signal that is missing today. This is cheap (pure numpy, no inference) and it
is the single highest-value artifact from this whole investigatio

