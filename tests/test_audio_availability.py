"""A frame must never advertise audio the server cannot serve.

The bug: clip ids are content-hashed from the text, so a problem the USER TYPED
produces a disclosure line no cache can already contain. The frame still carried
the id, the browser dutifully fetched it, and the console filled with

    GET https://.../api/audio/5521a7e....wav  404 (Not Found)

on a deployment that was otherwise healthy. On the deployed host there is no way
to render it either: Polly needs credentials, and attaching a role to the
instance requires iam:CreateInstanceProfile, which this account denies.

So the frame drops the id and sets `audio_missing` instead. The line still
appears in the transcript; it simply has no recording, and says so.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import app                                            # noqa: E402
from saathi.frames import build                       # noqa: E402
from saathi.playbook import build_brief, load_all     # noqa: E402
from saathi.simulate import SCENARIO_DIR, load, run   # noqa: E402

TYPED = "my internet has been down since morning and nobody called back"


def _frames(brief=None, name="real_rep"):
    sc = load(name)
    out = run(sc, sc.get("goal", ""), brief=brief)
    return app.annotate_audio_ms(build(out, sc)), sc


def _dead(frames):
    """Frames claiming a clip that is not on disk -- every one is a 404."""
    return [f for f in frames
            if f.get("audio")
            and not (app.AUDIO_DIR / f"{f['audio']}.wav").exists()]


def test_a_user_typed_problem_does_not_advertise_a_clip_that_404s():
    """Proves: the exact case from the console. A brief built from free text
    produces an utterance with no cached recording, and the frame says so
    instead of pointing at a file that is not there.
    Does NOT prove: that the line is audible. It is silent, by construction, on
    any host without Polly.
    """
    pb = load_all()["generic.unknown"]
    brief = build_brief(pb, TYPED, {"registered_phone": "9876541182"})
    frames, _ = _frames(brief)
    assert _dead(frames) == []
    assert any(f.get("audio_missing") for f in frames), (
        "a typed problem should produce at least one unrenderable line; if it "
        "no longer does, this test has stopped covering the case it was written "
        "for -- check whether the disclosure still carries {problem_line}"
    )


def test_the_check_would_have_caught_the_original_bug():
    """MUTATION CONTROL. Without dropping the id, _dead() must find it. If this
    passes, the guard above is vacuous and proves nothing.
    """
    pb = load_all()["generic.unknown"]
    brief = build_brief(pb, TYPED, {"registered_phone": "9876541182"})
    sc = load("real_rep")
    out = run(sc, sc.get("goal", ""), brief=brief)
    raw = build(out, sc)                      # NOT annotated: the old behaviour
    assert _dead(raw), (
        "an un-annotated build no longer references a missing clip, so the "
        "annotation is not doing any work on this corpus"
    )


@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml")))
def test_no_shipped_scenario_advertises_a_missing_clip(name):
    """The scenarios ship with their audio, so nothing here should be missing at
    all. This is the canary for a cache that was never warmed."""
    frames, _ = _frames(name=name)
    if not any(f.get("audio") for f in frames):
        pytest.skip(f"{name}: no audio at all; run `python -m saathi.cli warm`")
    assert _dead(frames) == []


def test_audio_ms_is_present_whenever_audio_is():
    """The pacing loop reads audio_ms to stop a clip being cut off. A frame with
    an id and no duration would silently fall back to the compressed clock."""
    frames, _ = _frames()
    for f in frames:
        if f.get("audio"):
            assert f.get("audio_ms", 0) > 0, f"{f['audio']} has no duration"
