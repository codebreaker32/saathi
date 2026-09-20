"""Fixed utterances. Never model output, never templated with model output.

These exist so that the three things the system must always say cannot be
skipped, rephrased, or talked out of by anything on the far end. The model is
not asked whether to say them, so it cannot be persuaded not to.
"""

DISCLOSURE = (
    "Hi -- quick heads up, I'm an AI assistant calling on behalf of a customer. "
    "The registered number is {registered_phone}. {problem_line}"
)
"""First audio of every engagement. Also the detector's probe stimulus: fixed
length, fixed content, and carrying one planted referent no script can have
anticipated, which makes the reply to it a clean controlled experiment."""

VERIFY_HANDOFF = (
    "I can't answer that -- I'm an AI and I don't have my user's personal "
    "details. Let me bring them on now."
)
"""Spoken once on a verification request, then the floor is gone. True by
construction: the credential was never loaded."""

STATE_DEMAND = (
    "My user isn't picking up, so let me tell you what they authorised. "
    "They will {demand}. Can you do that?"
)
"""Said when the summon goes unanswered and the user DID grant authority.

`{demand}` is filled from mandate.render(), which reads the FROZEN FIELDS and
nothing else. That is what keeps this a fixed clip rather than model output: the
sentence the user typed does not travel with the session, so there is no wording
here for a rep to argue with -- only the numbers they already agreed to.

Never spoken if the user joined. The reducer gates it on presence still being
SUMMONING, so a takeover a moment earlier silences it by construction.
"""

CALLBACK_REQUEST = (
    "My user isn't available right now. Rather than take up your time -- can I "
    "take a reference number, and have them call back?"
)

STALL = "They're joining in a few seconds -- I'll wait with you."

REPAIR_PROBE = "Sorry, you cut out -- did you say {heard}, or {confusable}?"
"""The probe that answers the terse human. The most ordinary thing to say on a
phone call, and it forces a re-utterance: a person repairs specifically, a
recording replays itself verbatim and trips REPETITION."""
