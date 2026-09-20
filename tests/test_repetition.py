import pytest

from saathi.evidence.repetition import (
    RepetitionDetector, is_repair, normalise, scorable, similarity,
)

MENU = "For orders press 1, for payments press 2, to repeat these options press 9"
HOLD = "Your call is important to us. Your estimated wait time is 7 minutes."
HOLD_LATER = "Your call is important to us. Your estimated wait time is 3 minutes."


def test_digits_collapse_so_wait_announcements_match_each_other():
    assert similarity(HOLD, HOLD_LATER) >= 0.90


def test_verbatim_menu_repeat_is_caught():
    assert similarity(MENU, MENU) == 1.0


def test_shingle_ignores_stopword_overlap():
    """Two unrelated sentences sharing only function words must score ~nothing."""
    a = "I am going to look into the account for you now"
    b = "It is going to be the case that we have to do this"
    assert similarity(a, b) < 0.10


def test_different_menus_do_not_collide():
    other = "For technical support press 3, for billing press 4, to hear this again press 8"
    assert similarity(MENU, other) < 0.30


@pytest.mark.parametrize("text", ["Hello?", "hello", "Sorry?", "Are you there?",
                                  "Can you hear me?", "Yeah"])
def test_repetition_exempts_repair(text):
    """A human repeating 'Hello?' is exactly when NOT to score them a machine."""
    assert is_repair(text)
    assert not scorable(text, speech_ms=5000)


def test_short_utterances_are_not_scorable_however_long_the_pause():
    assert not scorable("Billing.", speech_ms=9000)


def test_long_utterance_needs_real_speech_behind_it():
    assert not scorable(MENU, speech_ms=400)
    assert scorable(MENU, speech_ms=4000)


def test_in_call_repeat_needs_a_gap():
    d = RepetitionDetector(destination="sim://zomato")
    assert d.observe(HOLD, t_ms=0, speech_ms=4000) == []
    assert d.observe(HOLD, t_ms=5_000, speech_ms=4000) == [], "too soon to mean anything"
    out = d.observe(HOLD, t_ms=40_000, speech_ms=4000)
    assert [o.name for o in out] == ["in_call_repeat"] and out[0].llr < 0


def test_cross_call_repeat_fires_on_a_first_hearing_this_call():
    d = RepetitionDetector(destination="sim://zomato")
    d.remember(MENU)
    out = d.observe(MENU, t_ms=1000, speech_ms=4000)
    assert [o.name for o in out] == ["cross_call_repeat"]
    assert out[0].llr <= -4.0, "the strongest cheap evidence we have"


def test_a_human_saying_something_new_scores_nothing():
    d = RepetitionDetector(destination="sim://zomato")
    d.remember(MENU)
    out = d.observe(
        "Right, I can see the order here, it went out at half seven and arrived late",
        t_ms=1000, speech_ms=5000)
    assert out == []
