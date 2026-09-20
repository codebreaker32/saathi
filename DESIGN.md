# Design

## The problem, stated once

Getting a duplicate charge reversed takes about half an hour, and almost none of
it is the conversation. It is menus designed for the company's call-routing,
hold with no idea whether it will be four minutes or forty, and re-explaining
everything to whoever finally answers.

The conversation is the valuable part and it is a small fraction of the time.
The rest is dead loss you cannot use, because you have to stay ready.

## The thesis

Not "an AI that makes phone calls". The obvious version of this product — an
agent that handles the whole call and reports back — is wrong, and not because
it is harder. Nobody should give a bot unsupervised authority over their bank
account, and the people most tempted by the convenience are the ones who can
least afford a confidently wrong outcome.

> **The AI does the waiting. You do the deciding.**

Which makes the interesting engineering problem not *making* the call but
**knowing when to give it back**.

## The asymmetry, and where it lives in the code

Telling you a human is on the line when it is still a robot is the worse
failure. Pull someone back to their phone twice for nothing and they stop
believing the notification — and once they do, the product is worthless. This
does not degrade the experience, it removes the premise.

Missing a human is also bad — a rep hearing silence hangs up — but it is
recoverable and it fails in a direction that does not erode trust.

So the asymmetry is structural, not tuned:

| Where | How |
|---|---|
| `detector/core.py` | positive evidence clamps at `+1.5`, negative at `-4.0` |
| `detector/core.py` | `MIN_POSITIVE_FAMILIES = 3`, checked as an `if` |
| `detector/core.py` | truncation on deadline decides **MACHINE** — free, because the expensive error is unreachable by running out of time |
| `verification.py` | trigger phrases are deliberately over-broad; a false positive costs one interruption, a false negative is an identity-leak-shaped incident |
| `mandate.py` | ambiguity in an authorisation sentence resolves toward *less* authority, always |
| `mandate.py` | amounts are **floors**, so an unstated figure is refused rather than read as "take whatever they offer" |

## Two bugs inherited from the first attempt

**The prior rose with elapsed time.** `-3.5 + 2.5·(1 - e^(-t/240s))` encodes a
true base rate — a human is likelier at minute twenty than at second zero — but
it makes *"we have waited a long time"* act as evidence of humanness, so the
score drifts toward fetching with no evidence at all. The rule now is: **no term
proportional to elapsed time may be positive.** The prior is seeded once per
epoch from the phase, and only evidence moves it.
`test_no_positive_term_is_proportional_to_elapsed_time` is the regression.

**Repetition scored a distressed human as a machine.** "Hello? … Hello?" is a
verbatim repeat. Length gates plus an explicit repair list exempt it, because
that is the worst possible moment to decide someone is a recording.

## Why the fusion is twenty parameters and not a network

Not ideology — data. There is no public corpus of support calls labelled with
the second a human picked up; the real ones are proprietary or commercial. A
network trained on scenarios you wrote learns your generator and fails on the
first real call.

So models go where large public corpora already exist — speech synthesis,
speaker identity, turn-taking, language — and the small structured layer goes
where only your own data exists. The override button produces real labels from
production, and when there are enough, the fusion layer is what gets replaced.
The architecture is shaped so that is a replacement, not a rewrite.

## The agent proposes, the gate disposes

The families produce evidence. Deciding what to do about *ambiguous* evidence —
wait, probe, what to probe with, give up — is a sequential decision under
uncertainty and hand-written policy is bad at it. That is the agent's job.

Its read tools exist because a language model cannot hear: it has no access to
synthesis artefacts, breath or room tone. The tools hand it model outputs it
could never perceive itself.

`fetch_user()` validates the gate and refuses when it is unmet. The agent may
propose a fetch whenever it likes and be told no — exactly as `accept_offer()`
refuses a rep who argues that store credit is basically a refund. Reasoning is
stretchable; a field check is not.
`test_agent_cannot_force_fetch` drives it with prompt injection and asserts the
refusal, and it is what justifies letting an agent near the decision at all.

## Amounts are floors, not caps

Money is being offered *to* you, so a bigger number is a better outcome. Writing
the bound as a cap is not a naming choice, it is a correctness bug: a cap accepts
a derisory 50 rupees and escalates a generous 600 — precisely inverted from what
anyone wants.

So `accept_refund_min_paise` means *"settle it without me if they offer at least
this much; anything less, come and ask."* The dangerous input is therefore an
**unstated** figure, because that reads as "take whatever they offer" — which is
the kind of authority you would never notice granting until a 20-rupee refund had
been accepted on your behalf. `parse()` asks for a figure rather than defaulting.

## Disclosure doubles as the probe

The AI must announce itself first. The terse human — *"Billing."* then
*"Yeah?"* — gives nothing to score, so the only way forward is to say something
and see what comes back. These are the same action.

The disclosure clip is fixed-length and identical on every call, which makes the
reply to it a clean controlled experiment, and it carries one **planted
referent** — an order number no script can have anticipated. Checking for one
specific rare token sidesteps stopword lists entirely: a number is not a
function word in any language.

When that is not enough, the **repair probe**: *"Sorry, you cut out — did you say
billing, or building?"* The most ordinary thing to say on a phone call. A person
repairs specifically; a bot replays its prompt verbatim and trips `REPETITION`.
Note that a reply to a probe bypasses the minimum-gap rule — keeping it would
suppress exactly the evidence the probe was spent to elicit.

Bounded outside the prompt at two probes per party. There is a real person on
the other end about half the time, and burning their patience to run a test is
both rude and the fastest way to get the whole category blocked. No Turing traps.

## On stopwords

For repetition fingerprinting, **keep them**. IVR prompts are formulaic and
their function-word pattern is part of what makes two renderings identical;
strip them and short fragments collapse until unrelated ones collide. Normalise,
map digit runs to a placeholder so *"wait is 7 minutes"* and *"…3 minutes"* still
match, then compare 3–5 word shingles. Word order does the discriminating.

Where stopwords genuinely bite — did they echo something we said — the answer is
not a list but a planted rare token, as above. Menu-label matching keeps a small
per-locale list, because an English list is simply wrong for *"billing ke liye 1
dabaye"*.

## Handoff is the main path, not the fallback

In India, OTP verification is the norm rather than the exception. *"We've sent a
code to your registered number"* will happen on most calls that touch an
account.

| Finishes alone | Always reaches you |
|---|---|
| status queries, chasing a ticket, stating the problem, taking a reference number, accepting inside the mandate | refunds to a card, cancellations, address changes, plan changes |

That is the thesis arriving as a practical fact, and it means **handoff quality
is the product** — summon speed, whether the rep is left in silence, how cleanly
control transfers, how gracefully a wrong fetch undoes. Anything that smooths
the handoff beats anything that makes the agent more autonomous.

A second thing real telephony will remove: some IVRs authenticate by caller ID
(*"you're calling from the registered number, so you're verified"*). The agent
dials from its own number, fails that check, and lands in manual verification
regardless. `test_caller_id_verification_scenario` exercises that path now so
the first real call is not a surprise.

## Where this loses

- **The corpus is four machine calls.** The interval is enormous and the
  scoreboard prints it rather than the point estimate.
- **Anti-spoofing models generalise poorly to unseen synthesis systems.** That
  is the open problem in the field. `SYNTHESIS` catches current-generation voice
  bots and weakens against whatever ships next, which is why it is one family of
  five. Measuring it only against the voices used to build the corpus would
  produce a beautiful, meaningless number.
- **A fully generative, full-duplex bot with injected disfluency and simulated
  room noise defeats every family here.** The honest claim is that this raises
  the cost of running one against you, never that it solves it.
- **Nothing is validated against a real support queue**, so the open questions
  about unit economics and how support organisations would react stay open.
- **Twilio Media Streams cannot send DTMF toward the call** — inbound only —
  which rules out the obvious Twilio design for menu navigation. LiveKit's SIP
  participant handles it. Domestic Indian numbers need a licensed operator
  (Exotel-class) regardless.
