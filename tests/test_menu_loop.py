"""Pressing "repeat these options" twice is a loop, not a strategy.

The bug: on a menu where nothing matched the goal, the navigator took the
reversible "repeat these options" branch EVERY time it heard that menu, and the
escalation ladder only fired on the third hearing. So a caller sat through the
identical recording three times:

    Recorded menu   For orders press 1, ... to repeat these options press 9.
    presses 9       nothing matched; taking reversible 'repeat these options'
    Recorded menu   For orders press 1, ... to repeat these options press 9.
    presses 9       nothing matched; taking reversible 'repeat these options'   <-- pointless
    Recorded menu   For orders press 1, ... to repeat these options press 9.
    presses 0       stuck: escalating to an operator

The second press cannot work. Repeat replays a prompt whose every branch has
already been parsed and scored; there is no option it could reveal that it did
not have the first time. Reversibility is worth something once and is a pure
loop after that, and on a real line each replay costs fifteen seconds of the
same recording.
"""

import pytest

from saathi.navigate import Option, Risk, choose, parse_menu
from saathi.simulate import SCENARIO_DIR, load, run

PROMPT = ("For orders press 1, for payments press 2, to cancel your account "
          "press 7, to repeat these options press 9.")


def _menu_beats(name):
    sc = load(name)
    out = run(sc, sc.get("goal", ""))
    heard = [b for b in out.beats if "[menu]" in (b.note or "")]
    pressed = [b for b in out.beats
               if b.actor == "saathi" and b.text.startswith("presses")]
    return heard, pressed


# --------------------------------------------------------------------------- #
# The navigator
# --------------------------------------------------------------------------- #

def test_repeat_is_offered_the_first_time():
    """Once is defensible: speech recognition may genuinely have missed an
    option, and repeat is the only branch that costs nothing."""
    digit, why = choose(parse_menu(PROMPT), "wedding catering enquiry")
    assert digit == "9"
    assert "reversible" in why


def test_repeat_is_refused_once_the_menu_has_been_heard():
    """Proves: the escape hatch is one-shot.
    Does NOT prove: that escalating is the right next move -- that is the
    caller's ladder, tested below.
    """
    digit, why = choose(parse_menu(PROMPT), "wedding catering enquiry",
                        heard_before=True)
    assert digit is None
    assert "already" in why


def test_a_matching_option_is_still_taken_on_a_repeated_menu():
    """MUTATION CONTROL. `heard_before` must gate ONLY the nothing-matched
    branch. If it suppressed a real match too, a caller who looped once would
    never press the right digit again -- a far worse bug than the one fixed.
    """
    for hb in (False, True):
        digit, why = choose(parse_menu(PROMPT), "payments problem", heard_before=hb)
        assert digit == "2", f"heard_before={hb} suppressed a genuine match"


def test_an_irreversible_menu_is_still_refused():
    opts = (Option("7", "cancel your account", Risk.ACTION_VERB),)
    digit, why = choose(opts, "anything", heard_before=True)
    assert digit is None


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #

def test_menu_loop_does_not_replay_the_same_recording_three_times():
    """The scenario this was found on. Two hearings, then escalate."""
    heard, pressed = _menu_beats("menu_loop")
    texts = [b.text for b in heard]
    assert len(texts) == 2, f"heard the menu {len(texts)} times: {texts}"
    assert [b.text for b in pressed] == ["presses 9", "presses 0"]


@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml")))
def test_no_scenario_hears_the_same_menu_more_than_twice(name):
    """Proves: the loop cannot come back anywhere else.
    Does NOT prove: that two hearings is optimal. It is a bound, not a target --
    a real IVR that reprompts on no-input may legitimately repeat itself.
    """
    heard, _ = _menu_beats(name)
    counts = {}
    for b in heard:
        k = " ".join(b.text.lower().split())
        counts[k] = counts.get(k, 0) + 1
    worst = max(counts.values(), default=0)
    assert worst <= 2, f"{name} heard one menu {worst} times"


def test_the_caller_still_escalates_rather_than_stalling():
    """Refusing to press repeat must not turn into doing nothing. The whole
    point of the ladder is that a stuck call reaches a human."""
    _, pressed = _menu_beats("menu_loop")
    assert pressed[-1].text == "presses 0"
    assert "escalating" in (pressed[-1].note or "")
