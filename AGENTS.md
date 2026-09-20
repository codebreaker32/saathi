# Working on Saathi

Saathi phones customer support for you, sits through the menus and the hold
music, and hands you the call the moment a real person picks up.

Everything rests on one question — *is there a human on this line right now?* —
and on getting it wrong in only one direction. **Fetching the user for a machine
destroys the product** (do it twice and they stop believing the notification).
Missing a human just wastes a call. That asymmetry is the reason for most of the
design decisions below, and if you change something, check you have not
flattened it.

## Run it

```bash
.venv/bin/python -m pytest -q                       # 246 tests, offline, no credentials
.venv/bin/python server/app.py 8787 &               # SSE stream + audio
cd web && npm run dev                               # http://localhost:3000
.venv/bin/python -m saathi.cli table                # the measured scoreboard
.venv/bin/python -m saathi.cli run --scenario voicebot_warm
.venv/bin/python -m saathi.cli voice --scenario real_rep --out call.wav
```

The whole demo runs with **no AWS credentials** — Polly output is content-hashed
into `.voice-cache/`, so all 8 scenarios are already voiced.

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

## In flight

A workflow is measuring whether `SYNTHESIS` can become a real anti-spoofing
model. **Current answer: no, not on this corpus.** Real AASIST ranks the demo's
human rep as *more* synthetic than the bot (AUC 0.317) because both voices are
Polly, and two-thirds of the apparent skill in a naive setup is channel
provenance rather than voice (0.938 studio vs 0.69 channel-matched).

So: keep the amber marker, and ship the model as a **bench with an honest table**
rather than as a family. See the workflow transcript under
`.claude/projects/*/subagents/workflows/wf_93f3f1a4-119/`.

## Next, roughly in order

1. Publish the anti-spoofing bench table in `README.md` (do **not** turn the bar green).
2. Deploy for a URL — Lightsail for the engine, Amplify for `web/`, CloudFront for TLS.
3. Wire Strands Agents SDK for the detection agent; it runs on local Ollama and
   `fetch_user()` already refuses when fewer than three families agree.
4. Real telephony is a `CallTransport` implementation away. Note: **Twilio Media
   Streams cannot send DTMF toward the call**, which rules out the obvious design;
   LiveKit's SIP participant handles it. Indian numbers need a licensed operator.
