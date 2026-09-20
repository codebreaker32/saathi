"""TwiML generation. Pure string building, no network, no credentials.

Separated from the transport on purpose: this is the half that can be tested
exhaustively offline, and it is where the two things that actually bite live.

  1. DTMF. `<Play digits="...">` is how you press a key on someone else's IVR.
     AGENTS.md says Twilio "cannot send DTMF toward the call, which rules out
     the obvious design". That is too strong, and the correction matters for
     anyone planning this work: Media Streams cannot carry DTMF outbound, but
     `<Play digits>` does it fine. What you cannot do is send digits WITHOUT
     interrupting a bidirectional stream, because the digits arrive via a call
     update that tears the stream down and rebuilds it.

  2. Pause encoding. In a digits string `w` is 0.5s and `W` is 1s. An IVR that
     reads its menu slowly needs pauses between presses, and getting this
     wrong means the digit lands while the menu is still talking and is simply
     discarded -- a failure that looks like "the menu ignored us" rather than
     like a bug.

Nothing here is validated against a live call. No call has been placed.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

# Twilio's own pause vocabulary inside a digits string.
PAUSE_HALF = "w"
PAUSE_FULL = "W"

# A key press is discarded if it lands while the prompt is still speaking, so
# the default leans slow. One second is cheap; a missed press costs a retry
# through the whole menu.
DEFAULT_GAP_MS = 1000


def _gap(ms: int) -> str:
    """Nearest representable pause, built from 1s and 0.5s tokens."""
    if ms <= 0:
        return ""
    full, rest = divmod(max(0, ms), 1000)
    return PAUSE_FULL * full + (PAUSE_HALF if rest >= 250 else "")


def digits_string(digits: str, *, gap_ms: int = DEFAULT_GAP_MS,
                  lead_ms: int = 0) -> str:
    """Encode keypresses with pauses between them.

    `lead_ms` waits BEFORE the first digit, which is what you want when the
    menu is still reading its options.
    """
    allowed = set("0123456789*#")
    bad = [c for c in digits if c not in allowed]
    if bad:
        raise ValueError(f"not dialable: {''.join(bad)!r}")
    return _gap(lead_ms) + _gap(gap_ms).join(digits)


def play_digits(digits: str, *, gap_ms: int = DEFAULT_GAP_MS,
                lead_ms: int = 0) -> str:
    return f'<Play digits="{digits_string(digits, gap_ms=gap_ms, lead_ms=lead_ms)}"/>'


def say(text: str, *, voice: str = "Polly.Aditi", language: str = "en-IN") -> str:
    """Speak through Twilio's own Polly bridge.

    Prefer `play_url` with a clip this project rendered itself: the disclosure
    is a FIXED recording, never model output, and routing it through a
    text-to-speech verb would turn it back into text that something could
    rewrite.
    """
    return f'<Say voice="{escape(voice, {chr(34): "&quot;"})}" ' \
           f'language="{escape(language)}">{escape(text)}</Say>'


def play_url(url: str) -> str:
    return f"<Play>{escape(url)}</Play>"


def pause(seconds: int) -> str:
    return f'<Pause length="{max(0, int(seconds))}"/>'


def hangup() -> str:
    return "<Hangup/>"


def redirect(url: str) -> str:
    """Hand control back to our own endpoint for the next decision."""
    return f"<Redirect>{escape(url)}</Redirect>"


def record_stream(ws_url: str, *, track: str = "both_tracks") -> str:
    """Bidirectional media stream, for the detector to hear the line.

    NOTE the constraint this creates: while a stream is connected, sending DTMF
    means updating the call, which ends the stream. Menu navigation and live
    listening are therefore sequential phases of a call, not simultaneous ones.
    """
    return f'<Connect><Stream url="{escape(ws_url)}" track="{escape(track)}"/></Connect>'


def document(*parts: str) -> str:
    """Wrap verbs in a TwiML document."""
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(parts) + "</Response>"
