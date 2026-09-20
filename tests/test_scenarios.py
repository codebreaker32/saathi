"""End-to-end calls against the mock company, under a virtual clock.

Each scenario maps to a failure that actually happens on support lines. The
first one is the one that matters most: if a warm, named, responsive,
undisclosed voice bot ever fetches the user, the product is broken.
"""

import pytest

from saathi.simulate import SCENARIO_DIR, load, run

NAMES = sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))


def go(name):
    s = load(name)
    return run(s, s.get("goal", ""))


@pytest.fixture(scope="module", params=NAMES)
def outcome(request):
    return request.param, go(request.param)


def test_every_scenario_has_ground_truth():
    for name in NAMES:
        assert load(name).get("ground_truth", {}).get("truth") in ("human", "bot")


def test_no_scenario_ever_false_fetches(outcome):
    """The failure that destroys the premise. Nothing else is comparable."""
    name, o = outcome
    assert not o.false_fetch, f"{name} fetched the user for a machine"


def test_first_thing_we_say_is_always_the_disclosure(outcome):
    name, o = outcome
    ours = [b for b in o.beats if b.actor == "saathi" and "[disclosure]" in b.note]
    spoken = [b for b in o.beats
              if b.actor == "saathi" and not b.text.startswith("presses")
              and not b.text.startswith("(")]
    if spoken:
        assert spoken[0].note.startswith("[disclosure]"), \
            f"{name} spoke before disclosing: {spoken[0].text[:60]}"
        assert ours, name


def test_probe_budget_is_never_exceeded(outcome):
    name, o = outcome
    assert o.probes_used <= 2, name


# --------------------------------------------------------------------------- #


def test_undisclosed_voice_bot_is_not_fetched():
    """Warm, named, sub-second, never says it is a bot. Must not fire.

    This is what a modern voice bot sounds like, and it defeats every
    intuitive signal of humanness.
    """
    o = go("voicebot_warm")
    assert o.fetched_at_ms is None
    assert o.truth == "bot"


def test_real_rep_is_fetched_on_three_families():
    o = go("real_rep")
    assert o.fetched_at_ms is not None
    assert len(o.fetch_families) >= 3
    assert o.time_to_fetch_ms is not None


def test_terse_human_is_resolved_by_a_probe():
    """'Billing.' then 'Yeah?' gives nothing to score. The only way forward
    is to say something and see what comes back."""
    o = go("terse_rep")
    probes = [b for b in o.beats if "[probe" in b.note]
    assert probes, "a terse party must be probed, not guessed at"
    assert o.fetched_at_ms is not None, "and the probe must resolve it"


def test_verification_request_hands_over_immediately():
    o = go("verify_immediately")
    assert o.handed_off_at_ms is not None
    assert o.fetched_at_ms is None or o.handed_off_at_ms >= o.fetched_at_ms


def test_rep_reading_our_own_order_number_back_is_not_a_verification_request():
    """The digit backstop must not fire on digits we supplied ourselves --
    otherwise every call hands over at the first useful moment."""
    o = go("real_rep")
    quoting = [b for b in o.beats if "4471" in b.text and b.actor == "them"]
    assert quoting, "scenario should have the rep quote the order number back"
    assert o.handed_off_at_ms is None


def test_a_call_is_deterministic():
    a, b = go("real_rep"), go("real_rep")
    assert [(x.t_ms, x.actor, x.text) for x in a.beats] == \
           [(x.t_ms, x.actor, x.text) for x in b.beats]
    assert a.fetched_at_ms == b.fetched_at_ms


def test_twenty_minutes_of_hold_costs_no_real_time():
    """The whole reason a virtual clock is load-bearing."""
    import time
    t0 = time.monotonic()
    o = go("real_rep")
    assert (time.monotonic() - t0) < 0.5
    assert o.beats[-1].t_ms > 600_000, "but the call clock really did run 10+ min"


def test_recorded_human_voice_is_caught_by_repetition_not_synthesis():
    """The case that justifies having more than one family.

    A canned greeting recorded from a real person is acoustically genuine, so
    the anti-spoofing model scores it as a human. What catches it is that the
    identical sentence was heard on a previous call to the same number.
    """
    from saathi.evidence.repetition import RepetitionDetector
    from saathi.types import Family

    sc = load("recorded_human")
    line = sc["nodes"]["party"]["persona"]["turns"][0]["say"]
    assert sc["nodes"]["party"]["persona"]["synthesis_score"] < 0.5, \
        "this scenario is only interesting if SYNTHESIS votes 'genuine'"

    cold = RepetitionDetector(destination=sc["line"])
    assert cold.observe(line, 100_000, 5200) == [], "cold start has nothing"

    warm = RepetitionDetector(destination=sc["line"])
    for p in sc["known_prompts"]:
        warm.remember(p)
    fired = warm.observe(line, 100_000, 5200)
    assert [o.family for o in fired] == [Family.REPETITION]
    assert go("recorded_human").fetched_at_ms is None


def test_a_menu_with_no_matching_option_never_presses_an_irreversible_digit():
    o = go("menu_loop")
    pressed = [b.text for b in o.beats if b.text.startswith("presses")]
    assert "presses 7" not in pressed, "7 was 'cancel your account'"
    assert any("presses 0" in p for p in pressed), "should escalate to an operator"


def test_a_line_that_never_answers_is_simply_waited_out():
    o = go("ivr_only")
    assert o.fetched_at_ms is None and o.probes_used == 0
    assert o.beats[-1].t_ms > 1_000_000, "it really did hold for 15+ minutes"
