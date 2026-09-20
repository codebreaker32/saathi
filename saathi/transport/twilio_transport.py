"""A CallTransport backed by Twilio Programmable Voice.

STATUS: written, unit-tested offline, and NEVER RUN AGAINST A LIVE CALL. Nothing
in this module has dialled a telephone. Every claim it makes about how Twilio
behaves comes from the documentation, not from observation, and the first real
call should be treated as the experiment it is.

Twilio inverts control, and that is the whole shape of this file. You do not
drive a Twilio call; Twilio calls YOU, by POSTing to a webhook at each decision
point. So the transport is a mailbox: webhook handlers push events into a queue,
`events()` drains it, and the outbound half (`speak`, `send_dtmf`, `hangup`)
reaches back out through the REST API.

Two consequences worth knowing before relying on it:

  - IT NEEDS A PUBLIC URL. Twilio cannot POST to localhost. A tunnel
    (ngrok, Cloudflare) is not a convenience here, it is a requirement.
  - DTMF AND LIVE AUDIO ARE SEQUENTIAL. Sending digits updates the call, which
    tears down any bidirectional Media Stream. Navigate the menu, then listen.
    See `saathi/transport/twiml.py` for the detail.

The clock is `SystemClock` because this is real time. That is also why this is
the only place a transport may be slow: everything else in the package still
runs under `VirtualClock` and still replays identically.
"""

from __future__ import annotations

import os
import queue
from dataclasses import dataclass

from saathi.clock import SystemClock
from saathi.transport import twiml
from saathi.types import (
    Answered, Dialed, EndReason, HoldMusicDetected, RemoteUtterance, Ringing,
)


# Words per minute for estimating how long an utterance took. Support-desk
# speech is brisk; the figure only has to be good enough to clear the
# repetition detector's MIN_SPEECH_MS gate, not to be accurate.
ESTIMATED_WPM = 150


def speech_ms_for(text: str, form: dict) -> int:
    """How long the far end spoke, in ms.

    THIS EXISTS BECAUSE OF A REAL BUG, and the bug fails in the direction that
    breaks the product. The first version read `Duration` straight off the
    webhook. Twilio's `<Gather input="speech">` does not send one -- it sends
    `SpeechResult` and `Confidence` -- so every utterance arrived as ZERO ms.

    Zero is not a harmless default here. `repetition.scorable()` gates on
    `speech_ms >= MIN_SPEECH_MS`, so a zero-length utterance is never scored,
    and the whole REPETITION family silently goes dark. Every weight in that
    family is negative (in-call -3.5, cross-call -4.0), so losing it moves the
    score UP, toward FETCH. A missing field would have quietly biased a live
    call toward fetching the user for a recording.

    So: use the duration if the provider sends one, and otherwise ESTIMATE from
    the text rather than defaulting to zero. An estimate is honest here because
    the consumer is a threshold, not a measurement.
    """
    for key in ("Duration", "RecordingDuration", "CallDuration"):
        raw = (form.get(key) or "").strip()
        if raw:
            try:
                secs = float(raw)
            except ValueError:
                continue
            if secs > 0:
                return int(secs * 1000)
    words = max(1, len(text.split()))
    return int(words / ESTIMATED_WPM * 60_000)


class TwilioNotConfigured(RuntimeError):
    """Raised instead of guessing. A half-configured transport that silently
    no-ops is worse than one that refuses to start."""


@dataclass(frozen=True)
class TwilioConfig:
    account_sid: str
    auth_token: str
    from_number: str
    public_base_url: str          # e.g. https://something.ngrok-free.app

    @staticmethod
    def from_env() -> "TwilioConfig":
        missing, vals = [], {}
        for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                    "TWILIO_FROM_NUMBER", "SAATHI_PUBLIC_URL"):
            v = os.environ.get(key, "").strip()
            if not v:
                missing.append(key)
            vals[key] = v
        if missing:
            raise TwilioNotConfigured(
                "missing " + ", ".join(missing) +
                ". Set them in .env (gitignored) or the environment. "
                "SAATHI_PUBLIC_URL must be reachable from the internet: "
                "Twilio cannot POST to localhost."
            )
        return TwilioConfig(vals["TWILIO_ACCOUNT_SID"], vals["TWILIO_AUTH_TOKEN"],
                            vals["TWILIO_FROM_NUMBER"], vals["SAATHI_PUBLIC_URL"].rstrip("/"))

    @staticmethod
    def available() -> bool:
        try:
            TwilioConfig.from_env()
            return True
        except TwilioNotConfigured:
            return False


class TwilioTransport:
    """Satisfies saathi.types.CallTransport. See module docstring for caveats."""

    def __init__(self, config: TwilioConfig, *, client=None) -> None:
        self.config = config
        self._clock = SystemClock()
        self._events: "queue.Queue" = queue.Queue()
        self._call_sid: str | None = None
        self._session_id: str | None = None
        self._client = client            # injectable, so tests never touch network

    # -- outbound ---------------------------------------------------------- #

    @property
    def client(self):
        if self._client is None:
            from twilio.rest import Client          # imported late: optional dep
            self._client = Client(self.config.account_sid, self.config.auth_token)
        return self._client

    def dial(self, to: str, *, session_id: str) -> str:
        """Place the call. Returns Twilio's call SID.

        The answer URL points back at us, so the first thing that happens on
        pickup is OUR decision, not a canned greeting. That ordering is what
        keeps the disclosure first.
        """
        self._session_id = session_id
        call = self.client.calls.create(
            to=to,
            from_=self.config.from_number,
            url=f"{self.config.public_base_url}/twilio/answer?session={session_id}",
            status_callback=f"{self.config.public_base_url}/twilio/status?session={session_id}",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            machine_detection="Enable",
        )
        self._call_sid = call.sid
        self._events.put(Dialed(t_ms=self._clock.now_ms()))
        return call.sid

    def _update(self, *verbs: str) -> None:
        if not self._call_sid:
            raise TwilioNotConfigured("no live call; dial() first")
        self.client.calls(self._call_sid).update(twiml=twiml.document(*verbs))

    def speak(self, text: str) -> None:
        self._update(twiml.say(text),
                     twiml.redirect(f"{self.config.public_base_url}/twilio/next"))

    def play(self, url: str) -> None:
        """Play a clip we rendered ourselves. The disclosure goes through here,
        never through speak(): it is a fixed recording and must not become text
        that anything downstream could rewrite."""
        self._update(twiml.play_url(url),
                     twiml.redirect(f"{self.config.public_base_url}/twilio/next"))

    def send_dtmf(self, digits: str, *, gap_ms: int = 120) -> None:
        self._update(twiml.play_digits(digits, gap_ms=gap_ms),
                     twiml.redirect(f"{self.config.public_base_url}/twilio/next"))

    def cancel_playback(self) -> None:
        """Barge-in: replace whatever is playing with a silent wait."""
        self._update(twiml.pause(1),
                     twiml.redirect(f"{self.config.public_base_url}/twilio/next"))

    def set_capabilities(self, leg: str, caps) -> None:
        """No-op on Twilio, and the no-op is the finding.

        `Caps` is an SFU concept -- "the SFU enforces it; we project it". Twilio
        Programmable Voice has no equivalent: there is no selective forwarding
        unit and no per-participant publish grant to set. This member of the
        Protocol is LiveKit-shaped, and any Twilio implementation will be a
        no-op here. Recorded rather than quietly swallowed, because a Protocol
        member that one provider cannot implement is a leak in the abstraction,
        not a gap in this file.
        """
        return None

    def hangup(self, reason: EndReason) -> None:
        if self._call_sid:
            self.client.calls(self._call_sid).update(status="completed")

    # -- inbound ----------------------------------------------------------- #

    def on_webhook(self, path: str, form: dict) -> str:
        """Called by the HTTP layer. Translates a Twilio POST into an Event and
        returns the TwiML to answer it with."""
        now = self._clock.now_ms()
        status = (form.get("CallStatus") or "").lower()

        if path.endswith("/status"):
            if status == "ringing":
                self._events.put(Ringing(t_ms=now))
            elif status in ("in-progress", "answered"):
                self._events.put(Answered(t_ms=now))
            return twiml.document()

        if path.endswith("/answer"):
            self._events.put(Answered(t_ms=now))
            # Hold the line open and hand the next decision back to us. The
            # disclosure is played by the caller, not inlined here, so the
            # "announce first" rule stays in one place.
            return twiml.document(
                twiml.pause(1),
                twiml.redirect(f"{self.config.public_base_url}/twilio/next"))

        if path.endswith("/transcript"):
            text = (form.get("SpeechResult") or "").strip()
            if text:
                self._events.put(RemoteUtterance(
                    t_ms=now, text=text, t_end_ms=now + speech_ms_for(text, form)))
            return twiml.document(
                twiml.redirect(f"{self.config.public_base_url}/twilio/next"))

        return twiml.document()

    def events(self):
        """Drain what has arrived. Non-blocking: a live call has no end of
        stream until it hangs up, and a blocking iterator here would make the
        decision loop uninterruptible."""
        while True:
            try:
                yield self._events.get_nowait()
            except queue.Empty:
                return

    @property
    def clock(self):
        return self._clock
