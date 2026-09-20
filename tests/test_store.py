"""Cross-call memory: the only genuinely stateful thing in the system.

Offline by construction -- every test here uses MemoryStore, so the suite
never needs credentials, a table, or a network. DynamoStore implements the
same Protocol and is exercised by hand against a real table.
"""

import pytest

from saathi.evidence.repetition import RepetitionDetector
from saathi.simulate import load, run
from saathi.store import MemoryStore, Store, utterance_key
from saathi.types import Family

MENU = "For orders press 1, for returns and refunds press 2, to repeat press 9."


def test_memory_store_satisfies_the_protocol():
    assert isinstance(MemoryStore(), Store)


def test_the_key_ignores_formatting_but_not_content():
    assert utterance_key(MENU) == utterance_key("  FOR ORDERS   press 1, for "
                                                "returns and refunds press 2, "
                                                "to repeat press 9!  ")
    assert utterance_key(MENU) != utterance_key("For billing press 1.")


def test_the_key_ignores_digits_that_change_between_calls():
    """'wait time is 7 minutes' and '...3 minutes' are the same announcement."""
    a = "Your call is important to us. Your estimated wait time is 7 minutes."
    b = "Your call is important to us. Your estimated wait time is 3 minutes."
    assert utterance_key(a) == utterance_key(b)


def test_remembering_is_idempotent():
    s = MemoryStore()
    for _ in range(5):
        s.remember("sim://x", MENU)
    assert len(s.known("sim://x")) == 1


def test_destinations_do_not_bleed_into_each_other():
    """A prompt learned from Zomato must not incriminate Airtel."""
    s = MemoryStore()
    s.remember("sim://zomato", MENU)
    assert s.known("sim://airtel") == []


# --------------------------------------------------------------------------- #
# The payoff
# --------------------------------------------------------------------------- #


def test_cross_call_memory_is_what_makes_the_signal_exist():
    s = MemoryStore()
    cold = RepetitionDetector(destination="sim://amazon")
    assert cold.observe(MENU, 100_000, 5200) == [], "nothing to compare against"

    s.remember("sim://amazon", MENU)
    warm = RepetitionDetector(destination="sim://amazon")
    for p in s.known("sim://amazon"):
        warm.remember(p)
    fired = warm.observe(MENU, 100_000, 5200)
    assert [o.name for o in fired] == ["cross_call_repeat"]
    assert fired[0].family is Family.REPETITION
    assert fired[0].llr <= -4.0, "the strongest cheap evidence available"


def test_a_call_teaches_the_store_what_the_machines_said():
    sc = load("recorded_human")
    scenario = {k: v for k, v in sc.items() if k != "known_prompts"}
    s = MemoryStore()
    out = run(scenario, sc["goal"], store=s)
    assert out.learned > 0
    assert s.known(sc["line"]), "the next call should start warm"


def test_only_machine_utterances_are_learned():
    """Remembering a person's words would poison the index -- the next call
    would score a live human as a recording of themselves."""
    sc = load("real_rep")
    s = MemoryStore()
    out = run(sc, sc["goal"], store=s)
    learned = s.known(sc["line"])
    human_lines = [b.text for b in out.beats
                   if b.actor == "them" and "[human]" in (b.note or "")]
    assert human_lines, "scenario should contain human speech"
    for line in human_lines:
        assert line not in learned


def test_running_without_a_store_is_unchanged():
    """The store is additive; every existing scenario behaves identically."""
    sc = load("voicebot_warm")
    a = run(sc, sc["goal"])
    b = run(sc, sc["goal"], store=MemoryStore())
    assert a.fetched_at_ms == b.fetched_at_ms
    assert [x.text for x in a.beats] == [x.text for x in b.beats]
