# Working on Saathi

Saathi phones customer support for you, sits through the menus and the hold
music, and hands you the call the moment a real person picks up.

Everything rests on one question — *is there a human on this line right now?* —
and on getting it wrong in only one direction. **Fetching the user for a machine
destroys the product** (do it twice and they stop believing the notification).
Missing a human just wastes a call. That asymmetry is the reason for most of the
design decisions below, and if you change something, check you have not
flattened it.

## Set up from a fresh clone

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd web && npm install && cd ..
.venv/bin/python -m saathi.cli warm          # renders the call audio, once
```

On **Windows** there is no `bin/` and PowerShell has no `&&`. Everything works,
but every command below needs translating, so it is written out once here:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
cd web; npm install; cd ..
.venv\Scripts\python.exe -m saathi.cli warm
```

Read `.venv/bin/python` as `.venv\Scripts\python.exe` throughout, and replace
the backgrounding `&` on `server/app.py` with a second terminal.

**Do not skip the third line, or the demo plays in silence.**

`.voice-cache/` holds ~8MB of Polly-generated speech and is gitignored, so a
fresh clone has no audio. `warm` renders all 8 scenarios in one go and is
idempotent — it is keyed on voice and text, so re-running costs nothing and it
does not re-render if the AWS account changes.

Two ways to satisfy it:

- **Copy `.voice-cache/` from a teammate.** Needs no AWS for the demo: the
  server and the UI read the cache directly, audio included. `cli voice` and
  `cli warm` still probe Polly on startup and will exit 2 without credentials,
  but nothing else needs them.
- **Or configure AWS** (see *AWS* below) and let `warm` call Polly. It tells you
  plainly if credentials are missing, and nothing else in the project depends on
  them — tests, the detector and the UI all run without.

## Run it

```bash
.venv/bin/python -m pytest -q                       # 251 tests (250 pass, 1 skip), offline
.venv/bin/python server/app.py 8787 &               # SSE stream + audio
cd web && npm run dev                               # http://localhost:3000
.venv/bin/python -m saathi.cli table                # the measured scoreboard
.venv/bin/python -m saathi.cli run --scenario voicebot_warm
.venv/bin/python -m saathi.cli voice --scenario real_rep --out call.wav
.venv/bin/python -m saathi.cli warm                 # re-render any missing audio
.venv/bin/python -m bench.leak                      # is the audio corpus quotable? (no)
.venv/bin/python -m bench.corpus --build -n 70      # two-engine channel-matched corpus
```

`pytest`, `cli table`, `cli run`, `cli playbooks`, `server/app.py` and the web
UI run offline — no credentials, no network, no DynamoDB table.

**`cli voice` and `cli warm` are the exceptions.** Both gate on
`voice.available()` (`saathi/cli.py:85`, `:122`), which shells out to a live
`aws polly describe-voices` on *every* invocation. A warm cache does not bypass
it, so they exit 2 without AWS even when every clip is already rendered — and
the AWS CLI itself is a prerequisite for those two.

## House rules

These are not style preferences. The project's only real claim is that its
numbers mean what they say, and each of these exists because the alternative
already bit us once.

1. **Every test states what it does NOT prove.** See `tests/test_air_gap.py`.
2. **Plant the secret.** A leak test that never introduces the secret proves
   only that nobody typed it into the test. The original version of
   `test_missed_trigger_still_leaks_nothing` passed for exactly that reason.
3. **Pair every denial with a mutation control.** If you cannot make the
   assertion *fail* by breaking the thing it guards, it has no teeth.
   `test_the_leak_check_has_teeth` is the pattern.
4. **Quote the interval, not the point estimate.** Zero false fetches in four
   machine calls is not 0%; it is a 52.7% upper bound, and the scoreboard says so.
5. **Ambiguity resolves toward less authority, always.**
6. **Mark what is not measured.** `SYNTHESIS` and `IDENTITY` render amber with
   "not measured yet" because they are scripted stand-ins. Do not remove that
   marker without earning it (see *In flight*).

## Architecture, and why

- `saathi/session/machine.py` — a **pure** reducer, `step(state, event) -> (state, commands)`.
  No I/O, no clock, no awaits. That purity is what makes any call replayable.
- Three orthogonal state tracks: `LineState`, `UserPresence`, and **`Floor`** (who
  holds the microphone). Floor is stored, never derived from the other two.
- `saathi/detector/core.py` — five evidence families; **three must agree**, and
  that count is an `if`, not an implication of the arithmetic. A miscalibration
  can break a sum; it cannot break a count.
  - **No term proportional to elapsed time may be positive.** The first version
    had a prior that rose with hold duration, which made "we have waited twenty
    minutes" act as evidence of humanness.
  - On deadline, decide MACHINE. Truncation is free here because the expensive
    error is unreachable by running out of time.
- `saathi/clock.py` — the **only** module allowed to read wall-clock time or
  sleep, enforced by `tests/test_clock_purity.py`. It greps docstrings too, so
  prose containing a banned call will fail the suite.
- `saathi/playbook.py` — `build_brief()` is the air gap. `NEVER`-tagged fields
  are **absent**, not masked. The brief is the only channel by which a user fact
  reaches anything the agent says.
- `saathi/mandate.py` — parsed **once** from free text before the call, then
  frozen. The sentence does not travel with the session, so a rep arguing
  "store credit is basically a refund" has nothing to act on.
- `saathi/store.py` — cross-call memory in DynamoDB. The index loads **once per
  call**; a round trip inside the decision loop would put latency on the one
  decision where latency is trust.
- `server/app.py` — SSE on the stdlib, not a WebSocket. The stream is one-way and
  the controls are four buttons.
- `web/app/page.tsx` — a five-stage flow, not routes. The SSE connection and
  detector state must survive across call → verification → handoff → summary,
  which is one continuous call.

## Traps already hit — please don't re-lay them

- **Scrubber:** `\b\d{12,19}\b` misses `4111-1111-1111-1111`, i.e. how people
  actually write card numbers. Match on separator-tolerant clusters.
- **Digit backstop:** it fired on the rep reading *our own* order number back,
  handing over every call at the first useful moment. Digits we supplied are
  exempt; digits we did not are not.
- **Gaps are evidence.** The bot replies in 400ms and the rep in 1400ms. Clamping
  inter-beat silence rendered them identical, so the audio contradicted the thing
  it existed to demonstrate. `gap_ms` travels on the `Beat` for this reason.
- **Repetition must exempt repair.** "Hello? … Hello?" is a verbatim repeat and
  the worst possible moment to score someone a machine.
- **A local model will invent a number.** Asked to parse "accept a refund" with no
  figure it returned 100 paise — "a refund of Rs 1 or more". A figure that does
  not appear in the user's own sentence cannot enter the mandate.
- **Disclosure before anything else.** A verification request in the rep's first
  sentence used to jump ahead of the disclosure clip.
- **AWS:** `PowerUserAccess` denies all of `iam:*`, so a login profile created
  with `--password-reset-required` cannot be satisfied and fails as "incorrect
  auth info". Don't combine them.

## AWS

Only **Polly** and **DynamoDB** are used, and both are load-bearing — Polly
because the adversarial claim only lands if the bot genuinely sounds human,
DynamoDB because cross-call repetition cannot exist without persistent state.
`SAATHI_AWS_PROFILE` in `.env` selects the profile; empty means standard
resolution, which is what a deployed box wants.

Bedrock is blocked on an account-level use-case form. It is not needed: the
detector verdict deliberately has no model in it, and `saathi/llm.py` speaks to
any OpenAI-compatible endpoint (`ollama` locally, plus groq/xai/openrouter/
gemini/deepseek/together by name).

## The anti-spoofing question — settled on the amber marker only

`SYNTHESIS` and `IDENTITY` render amber because they are **scripted stand-ins**,
not measured models. We investigated whether a real anti-spoofing model could
replace them. **It cannot, on this corpus, and the amber marker must stay.**

`research/antispoof-findings.md` holds five reports: three ground
investigations and two adversarial reviews, **both of which broke the proposed
deliverable**. The ground measurements, with real corpora and real AASIST
weights:

- Pointed at today's all-Polly corpus, the model ranks the demo's **human rep as
  more synthetic than the bot** — AUC 0.317, i.e. backwards. Both voices are
  Polly, so it is doing its job correctly. Wiring it today would vote against
  the human.
- **Two thirds of the apparent skill is channel, not voice.** Studio human vs
  Polly scores AUC 0.938; both sides through one telephone chain scores 0.69.
- 96 utterances from 8 speakers is not 96 samples. ICC is 0.54-0.79, so
  intervals must be **speaker-clustered**, not utterance-bootstrapped.

Report 3 proposed shipping that as an **offline bench table** — *"AUC 0.69
[0.55, 0.83], speaker-clustered, 8 speakers, 2 generators"* — calling it "a real,
defensible, quotable result". **Two adversarial reviews then broke that table.**
Do not ship it:

- **Corpus composition swamps the interval** (high). Holding model, channel and
  synthetic side byte-identical, leave-k-speakers-out moves AUC 0.481-0.869 and
  EER 25.0-53.9% — wider than the quoted CI. A reader sees "[0.55, 0.83]" and
  infers the remaining uncertainty is sampling noise; the dominant term is a
  corpus-selection effect the interval is silent about. An amber marker can be
  discounted at zero cost; a bootstrap CI cannot.
- **A voice-free scalar beats the model** (critical). Polly renders to a buffer,
  so its pauses are bit-exact digital zero (-309 dB), while recorded speech
  always carries a noise floor. "Level of the quietest frame" — no voice
  content whatsoever — scores AUC 0.954 human-vs-Polly against AASIST's 0.855
  on identical files through an identical chain. Any number from this corpus is
  uninterpretable: it is equally consistent with a model reading synthesis
  artefacts and with one reading "this file was written, not recorded". That
  distinction cannot exist in production.

`tests/test_scenarios.py::test_synthesis_is_load_bearing_for_every_fetch` records
the consequence. Only `CONTINGENCY`, `DUPLEX` and `SYNTHESIS` can ever vote
positive, so with `MIN_POSITIVE_FAMILIES = 3` the scripted family is required by
**every** fetch — zeroing it removes all three. The previous version of that
test asserted the opposite and could not fail: it computed its baseline inside
its own sabotage window, comparing a sabotaged run against a sabotaged run.

## Next, roughly in order

1. **Do not publish a bench table yet.** Two controls disqualify the corpus,
   and they are prerequisites rather than footnotes:
   - **Leak battery — BUILT.** `bench/leak.py`, pure stdlib, no numpy and no
     build-time dependency near the runtime path. It scores eight voice-free
     scalars and refuses any corpus where one of them beats chance.

     ```
     python -m bench.leak                                  # the shipped cache
     python -m bench.leak --group a=dirA --group b=dirB    # two provenances
     ```

     **Its verdict on this repo's own audio is DISQUALIFIED.** 24 of 37 cached
     clips carry bit-exact digital silence, which is the absence of a noise
     floor rather than a quiet one; no microphone produces it and nothing that
     crossed a carrier can have it. With one provenance class it refuses to
     emit an AUC at all rather than report a figure it could not compute.
     Pointed at a real contrast it is decisive: on eight buffer-written against
     eight microphone-like clips, identical but for the floor, six of the eight
     scalars separate at AUC 1.000 without hearing a word.

     `tests/test_leak_battery.py` carries the control that matters — a corpus
     the battery must PASS. A gate that can only ever say DISQUALIFIED is a
     constant, and the catching test would certify nothing without it.
     `test_the_projects_own_audio_is_disqualified` is written to start failing
     once real recorded audio lands.
   - **Channel chain and two-engine corpus — BUILT.** `bench/channel.py`
     (band-limit 300-3400, G.711 mu-law hand-rolled because `audioop` is gone
     in 3.13, 20 dB line noise, optional level jitter) and `bench/corpus.py`
     (content-matched, channel-matched, 70 utterances per engine).

     **It was run, and the result is worth reading before you plan any more
     channel work.** Polly against Windows SAPI, same sentences, same chain:
     raw, all eight voice-free scalars leak at ~1.000. The chain kills the
     digital-silence confound outright (`zero_frame_fraction` 1.000 -> 0.500,
     exactly chance). Level jitter then kills the loudness confound
     (`rms_dbfs` 1.000 -> 0.605). And the corpus is STILL DISQUALIFIED, now by
     relative measures gain cannot touch — `p5_frame_db` 0.934,
     `crest_factor` 0.871, `duration_s` 0.902.

     Three rounds of honest engineering moved the worst leak from 1.000 to
     0.934 and never came near the 0.60 ceiling. Two deterministic generators
     produce two tight distributions on every axis you can measure; real speech
     does not, and that variation cannot be manufactured because manufacturing
     it is choosing the answer. Full numbers in
     `research/channel-matching-findings.md`.

   - **Corpus-composition sensitivity gate — still to do.** Recompute the
     headline on every leave-k-speakers-out subset and assert
     `span < width(cluster_CI)`. It needs per-speaker labels, so it is blocked
     on the same real audio as everything else. Today the span is 0.481-0.869
     against a CI width of 0.28. Make it a test that fails the build, not a
     line in a risk list.

   The fix for both is **real recorded audio on the human side**; the research
   doc's cheapest honest start is ~10 lines of the rep script recorded on an
   actual phone. Injecting a synthetic noise floor is not a fix — it patches
   the one scalar that got caught and leaves the others leaking. The corpus
   recipe and stdlib channel chain are in `research/antispoof-findings.md`;
   torch belongs in a separate `requirements-bench.txt`, not the main install.
2. Deploy for a URL — Lightsail for the engine, Amplify for `web/`, CloudFront for TLS.
3. Wire Strands Agents SDK for the detection agent; it runs on local Ollama and
   `fetch_user()` already refuses when fewer than three families agree.
4. Real telephony is a `CallTransport` implementation away. Note: **Twilio Media
   Streams cannot send DTMF toward the call**, which rules out the obvious design;
   LiveKit's SIP participant handles it. Indian numbers need a licensed operator.


## State at handover

251 tests, all offline — no credentials, no network, no table required.
250 pass and 1 skips: `ivr_only` never speaks, so it cannot demonstrate
disclosure ordering, and it is skipped rather than counted as a pass.
Committed and pushed to `main`. Two servers run locally: `server/app.py` on 8787
and Next.js on 3000.

What is real: the detector and its five families, the pure state machine, the
air gap, the mandate freeze, cross-call memory in DynamoDB, Polly voices, the
SSE stream, and the five-stage UI. Note that only three of the five families can
vote *for* a human, so the three-family gate is unanimity rather than a quorum,
and the scripted `SYNTHESIS` is load-bearing in every fetch. What is not: `SYNTHESIS` and `IDENTITY` are
scripted (marked amber in the UI and in `stream.SCRIPTED_FAMILIES`), the line is
simulated, and nothing has been validated against a real support queue.

The honest README claim today is the one already there: zero false fetches in
four machine calls, quoted as a **52.7% upper bound** rather than 0%.
