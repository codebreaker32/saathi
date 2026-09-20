"""The Twilio transport, tested without credentials and without a phone.

WHAT THESE TESTS DO NOT PROVE, and it is the most important line in the file:
no call has ever been placed. Everything here checks that we build the right
TwiML and translate webhooks into the right events. Whether Twilio then behaves
as documented is unobserved. The first live call is an experiment, not a
regression run.
"""

import pytest

from saathi.transport import twiml
from saathi.transport.twilio_transport import (
    TwilioConfig, TwilioNotConfigured, TwilioTransport,
)
from saathi.types import (
    Answered, CallTransport, Clock, Dialed, RemoteUtterance, Ringing,
)

CFG = TwilioConfig("AC_test", "token", "+15550001111", "https://example.test")


class FakeCalls:
    """Records what would have been sent, instead of sending it."""

    def __init__(self, store):
        self.store = store

    def create(self, **kw):
        self.store.append(("create", kw))
        return type("Call", (), {"sid": "CA_fake"})()

    def __call__(self, sid):
        store = self.store

        class Live:
            def update(self, **kw):
                store.append(("update", sid, kw))
        return Live()


class FakeClient:
    def __init__(self):
        self.sent = []
        self.calls = FakeCalls(self.sent)


@pytest.fixture
def t():
    return TwilioTransport(CFG, client=FakeClient())


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #

def test_it_satisfies_the_call_transport_protocol(t):
    """CallTransport was declared and nothing implemented it.

    Does NOT prove much on its own: isinstance() against a runtime-checkable
    Protocol only checks that the ATTRIBUTE NAMES exist. A class whose seven
    methods all take zero arguments and whose `clock` is the integer 42 passes
    it. The clock check below is the half with teeth -- isinstance(42, Clock)
    is False -- so it is asserted separately rather than being assumed to
    follow from the first line.
    """
    assert isinstance(t, CallTransport)
    assert isinstance(t.clock, Clock), "a transport must expose a real Clock"


def test_a_shape_only_impostor_shows_the_protocol_check_is_weak():
    """MUTATION CONTROL for the test above, and the reason it is worded that
    way. This deliberately-broken class satisfies the Protocol."""
    class Impostor:
        def dial(self): ...
        def send_dtmf(self): ...
        def speak(self): ...
        def cancel_playback(self): ...
        def set_capabilities(self): ...
        def hangup(self): ...
        def events(self): ...
        clock = 42

    assert isinstance(Impostor(), CallTransport), "isinstance is shape-only"
    assert not isinstance(Impostor().clock, Clock), "but the clock check catches it"


# --------------------------------------------------------------------------- #
# The duration bug
# --------------------------------------------------------------------------- #

def test_a_missing_duration_does_not_silently_disable_repetition():
    """REGRESSION, and it failed in the direction that breaks the product.

    The first version read `Duration` off the webhook. Twilio's speech gather
    does not send one -- it sends SpeechResult and Confidence -- so every remote
    utterance arrived as ZERO ms. repetition.scorable() gates on
    speech_ms >= MIN_SPEECH_MS, so the whole REPETITION family went dark, and
    every weight in that family is negative. Losing it moves the score UP,
    toward FETCH: a missing field would have biased a live call toward fetching
    the user for a recording.

    Proves: a realistic Twilio speech payload still produces scorable speech.
    Does NOT prove: that the estimate is accurate. It is an estimate, and the
    consumer is a threshold rather than a measurement.
    """
    from saathi.evidence.repetition import scorable
    from saathi.transport.twilio_transport import speech_ms_for

    prompt = "Thank you for calling. For orders press one, for payments press two."
    real_payload = {"SpeechResult": prompt, "Confidence": "0.91"}   # no Duration
    ms = speech_ms_for(prompt, real_payload)
    assert ms > 0, "a missing Duration must not become zero"
    assert scorable(prompt, ms), "REPETITION must still be able to score this"


def test_a_provider_supplied_duration_wins_over_the_estimate():
    from saathi.transport.twilio_transport import speech_ms_for
    assert speech_ms_for("hello there", {"Duration": "7"}) == 7000


def test_a_zero_or_junk_duration_falls_back_to_the_estimate():
    from saathi.transport.twilio_transport import speech_ms_for
    for form in ({"Duration": "0"}, {"Duration": ""}, {"Duration": "abc"}):
        assert speech_ms_for("one two three four five six", form) > 0


def test_it_uses_the_clock_module_rather_than_reading_time_itself():
    """clock.py is the only module allowed near wall time, enforced by grep in
    tests/test_clock_purity.py. A transport is real-time, which makes it the
    most likely place for that rule to get broken."""
    import saathi.transport.twilio_transport as m
    src = open(m.__file__, encoding="utf-8").read()
    assert "import time" not in src
    assert "SystemClock" in src


def test_it_refuses_to_start_half_configured(monkeypatch):
    """A transport that silently no-ops on missing credentials would look like
    a call that simply never connected."""
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
              "TWILIO_FROM_NUMBER", "SAATHI_PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    assert not TwilioConfig.available()
    with pytest.raises(TwilioNotConfigured) as e:
        TwilioConfig.from_env()
    assert "localhost" in str(e.value), "the public-URL requirement must be stated"


def test_speaking_before_dialling_is_an_error(t):
    with pytest.raises(TwilioNotConfigured):
        t.speak("hello")


# --------------------------------------------------------------------------- #
# DTMF -- the part AGENTS.md said was impossible
# --------------------------------------------------------------------------- #

def test_digits_carry_pauses_so_a_keypress_is_not_swallowed():
    """A digit that lands while the menu is still talking is discarded, and the
    failure looks like 'the IVR ignored us' rather than like a bug. W is 1s and
    w is 0.5s in Twilio's digits vocabulary."""
    assert twiml.digits_string("12", gap_ms=1000) == "1W2"
    assert twiml.digits_string("12", gap_ms=500) == "1w2"
    assert twiml.digits_string("1", lead_ms=2000) == "WW1"


def test_only_dialable_characters_are_accepted():
    for bad in ("1a", "hello", "1;2", "1 2"):
        with pytest.raises(ValueError):
            twiml.digits_string(bad)
    assert twiml.digits_string("0123456789*#", gap_ms=0) == "0123456789*#"


def test_sending_dtmf_produces_a_play_digits_verb(t):
    t._call_sid = "CA_x"
    t.send_dtmf("2", gap_ms=1000)
    kind, sid, kw = t.client.sent[-1]
    assert (kind, sid) == ("update", "CA_x")
    assert '<Play digits="2"/>' in kw["twiml"]


# --------------------------------------------------------------------------- #
# Webhooks in, events out
# --------------------------------------------------------------------------- #

def test_dialling_emits_dialed_and_asks_twilio_to_call_us_back(t):
    sid = t.dial("+15557654321", session_id="s1")
    assert sid == "CA_fake"
    kind, kw = t.client.sent[0]
    assert kind == "create"
    assert kw["to"] == "+15557654321" and kw["from_"] == CFG.from_number
    assert kw["url"].startswith("https://example.test/twilio/answer")
    assert [type(e) for e in t.events()] == [Dialed]


def test_status_webhooks_become_events(t):
    t.on_webhook("/twilio/status", {"CallStatus": "ringing"})
    t.on_webhook("/twilio/status", {"CallStatus": "in-progress"})
    assert [type(e) for e in t.events()] == [Ringing, Answered]


def test_a_transcript_webhook_becomes_a_remote_utterance(t):
    t.on_webhook("/twilio/transcript",
                 {"SpeechResult": "Billing, how can I help", "Duration": "2"})
    got = list(t.events())
    assert [type(e) for e in got] == [RemoteUtterance]
    assert got[0].text == "Billing, how can I help"
    assert got[0].t_end_ms >= got[0].t_ms


def test_an_empty_transcript_emits_nothing(t):
    """Silence is not an utterance. Emitting one would hand the detector a
    turn that never happened."""
    t.on_webhook("/twilio/transcript", {"SpeechResult": "   "})
    assert list(t.events()) == []


def test_draining_events_twice_does_not_repeat_them(t):
    t.on_webhook("/twilio/status", {"CallStatus": "ringing"})
    assert len(list(t.events())) == 1
    assert list(t.events()) == []


def test_events_does_not_block_on_an_empty_queue(t):
    """A blocking iterator would make the decision loop uninterruptible, and a
    live call has no end-of-stream until someone hangs up."""
    assert list(t.events()) == []


# --------------------------------------------------------------------------- #
# The leak in the abstraction
# --------------------------------------------------------------------------- #

def test_set_capabilities_is_a_documented_no_op(t):
    """Caps is an SFU concept and Twilio has no SFU. Recorded rather than
    quietly swallowed: a Protocol member one provider cannot implement is a
    leak in the abstraction, not a gap in the implementation."""
    assert t.set_capabilities("a", None) is None
    src = open(__import__("saathi.transport.twilio_transport",
                          fromlist=["x"]).__file__, encoding="utf-8").read()
    assert "LiveKit-shaped" in src, "the leak must stay documented in the source"


def test_the_disclosure_path_does_not_go_through_text_to_speech(t):
    """The disclosure is a fixed recording, never model output. play() exists so
    it never becomes text that something downstream could rewrite."""
    t._call_sid = "CA_x"
    t.play("https://example.test/audio/disclosure.wav")
    _, _, kw = t.client.sent[-1]
    assert "<Play>" in kw["twiml"] and "<Say" not in kw["twiml"]
