# Saathi

**An AI that calls customer support for you, sits through the menus and the hold
music, and hands you the call the moment a real person picks up.**

The whole product rests on one question — *is there a human on this line right
now?* — and on getting it wrong in only one direction. Fetching you when it is
still a machine destroys the premise, because you stop believing the
notification and then the thing is worthless. Missing a human just wastes a
call.

## Results

```
  scenario             truth  fetched        t+  outcome
  -------------------------------------------------------
  bot_settles_refund   bot    False           -  correctly held
  generic_rep          human  True        12.3s  correct
  ivr_only             bot    False           -  correctly held
  menu_loop            human  True         9.7s  correct
  offer_below_mandate  bot    False           -  correctly held
  real_rep             human  True         7.2s  correct
  recorded_human       bot    False           -  correctly held
  terse_rep            human  True        10.6s  correct
  verify_immediately   human  False           -  handed over (verification)
  voicebot_disclosed   bot    False           -  correctly held
  voicebot_warm        bot    False           -  correctly held

  false fetches      0/6 machine calls
  95% upper bound    39.3%
  missed humans      1/5
  disclosed first    10/10   (ivr_only: never spoke, not measured)
```

**Read that upper bound, not the zero.** Six machine calls cannot support a
strong claim, and the interval says so. Roughly 300 machine calls with no false
fetch would be needed to claim under 1%. The scoreboard prints the bound rather
than the point estimate so that the number cannot be quoted flatteringly.

**And read why it moved.** This bound was 52.7% when there were four machine
calls. It is 39.3% because two more were added -- `bot_settles_refund` and
`offer_below_mandate`, written here to exercise the offer path. Nothing about
the detector changed. A tighter interval bought by writing yourself more
scenarios is arithmetic, not evidence, and the honest reading is still the one
above: a bound over a corpus we wrote.

`generic_rep` was added later still, for a company with no playbook of its own.
It moved the human denominator from four to five and moved nothing else.

Reproduce:

```
pytest -q
python -m saathi.cli table                          # the scoreboard above
python -m saathi.cli run --scenario voicebot_warm   # watch one call
python -m saathi.cli playbooks
```

## The hard case

```
  11:21  them    Hi there! I'm Ava. How can I help you today?      [machine]
  11:21  saathi  Hi -- quick heads up, I'm an AI assistant...      [disclosure]
  11:24  detect  MACHINE  score=-8.00                              decisive
```

Warm, named, answers in under a second, asks an open question, and never says it
is a bot. Every intuitive signal of humanness is present. It must not fetch you,
and it does not.

## How the verdict is reached

Five families of evidence, each catching a different kind of machine. **Three
must agree**, and that count is an `if` rather than an implication of the
arithmetic — a miscalibration can break a sum, but it cannot break a count.

| Family | Catches | Can vote *for* a human? |
|---|---|---|
| `SYNTHESIS` | the undisclosed generative bot | yes — **scripted stand-in today** |
| `REPETITION` | recordings, hold loops, canned greetings — in-call and **across calls** | no |
| `CONTINGENCY` | anything following a script | yes |
| `DUPLEX` | half-duplex bots that talk through you | yes |
| `DISCLOSURE` | bots that admit it — a **veto**, not a vote | no |

**Three of five is really three of three.** Every `REPETITION` and `DISCLOSURE`
weight in the package is negative, so only three families can ever produce a
positive score. With `MIN_POSITIVE_FAMILIES = 3` the quorum has exactly one
satisfiable solution — and the scripted `SYNTHESIS` is mandatory in it.
`test_synthesis_is_load_bearing_for_every_fetch` measures that by zeroing it,
which removes all three fetches. That is a limitation of this build, not
redundancy.

The `recorded_human` scenario is why there is more than one. A canned greeting
recorded from a real person is acoustically genuine, so the synthesis model
scores it human. What catches it is that the identical sentence was heard on a
previous call to that number.

## Three things it will not do

- **It announces itself first, every time.** A fixed clip, never model output,
  gated at the audio sink. The model is not asked whether to say it.
- **It cannot authenticate as you.** Not "refuses to" — the card number never
  enters the brief, and the brief is the only channel by which any user fact
  reaches something the agent says. So *"I don't have that"* is literally true.

  `tests/test_air_gap.py` empties the trigger-phrase list entirely, lets the rep
  ask for the card outright, and checks every word the agent spoke across a full
  call. It is paired with a **mutation control** that deliberately bypasses the
  whitelist and requires the same check to *fail* — because a leak test that
  cannot detect a leak proves nothing, and would read as proof.
- **It only accepts what you authorised.** You write the mandate as a sentence
  — *"accept a refund of 400 or more, or a redelivery if they can't"* — it is
  parsed once before the call, and the frozen fields are what enforce during it.
  A rep can stretch a sentence; they cannot move a field. Amounts are **floors,
  not caps**: money is offered *to* you, so a cap would quietly accept a
  derisory offer and escalate a generous one.

## What this is not

The line is **simulated** — mock company IVRs, under a virtual clock, so a
twenty-minute hold costs microseconds and the same call can run a hundred times.
Nothing here has been validated against a real support queue.

The audio-derived families are scripted values standing in for the
anti-spoofing and speaker-embedding models, marked `(scripted)` wherever they
appear. The event layer, the state machine, the gate, the mandate and the air
gap are all real.

See [DESIGN.md](DESIGN.md) for why it is built this way, and where it loses.
