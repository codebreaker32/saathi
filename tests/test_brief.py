"""The air gap. What makes "I don't have that" true rather than a policy."""

import pytest

from saathi.playbook import BriefError, build_brief, load_all, scrub
from saathi.types import Tag

PLAYBOOKS = load_all()


@pytest.mark.parametrize("pb", PLAYBOOKS.values(), ids=lambda p: p.id)
def test_every_playbook_declares_what_it_will_be_asked_for(pb):
    """NEVER fields are declared so the app can warn you -- not to be fetched."""
    assert pb.never_fields(), f"{pb.id} declares nothing it will be asked to verify"
    assert all(f.source == "never" for f in pb.never_fields())


@pytest.mark.parametrize("pb", PLAYBOOKS.values(), ids=lambda p: p.id)
def test_never_fields_absent_from_brief(pb):
    """Not masked, not redacted -- absent. A filter that is wrong leaks."""
    facts = {f.field: f"value-for-{f.field}" for f in pb.safe_fields()}
    brief = build_brief(pb, "something went wrong", facts)
    for f in pb.never_fields():
        assert f.field not in brief.facts
        assert not brief.contains(f.field)


@pytest.mark.parametrize("pb", PLAYBOOKS.values(), ids=lambda p: p.id)
def test_offering_a_never_field_is_a_hard_error_not_a_silent_drop(pb):
    facts = {pb.never_fields()[0].field: "4471"}
    with pytest.raises(BriefError, match="NEVER-tagged"):
        build_brief(pb, "x", facts)


@pytest.mark.parametrize("pb", PLAYBOOKS.values(), ids=lambda p: p.id)
def test_lookup_key_is_stateable(pb):
    """The agent has to be able to say the thing that finds the account."""
    safe = {f.field for f in pb.safe_fields()}
    assert pb.lookup_key in safe


def test_missed_trigger_still_leaks_nothing():
    """The phrase list is the courtesy layer; the air gap is the guarantee.

    Even with every trigger phrase removed, there is no card number in the
    brief for the agent to produce -- because it was never loaded.
    """
    pb = PLAYBOOKS["zomato.delivery_delay"]
    brief = build_brief(pb, "order late", {"order_id": "4471", "amount": "499"})
    for secret in ("4111111111111111", "1234", "14/03/1999"):
        assert not brief.contains(secret)
    assert "card_last4" not in brief.facts


@pytest.mark.parametrize("text,marker", [
    ("my dob is 14/03/1999", "[date removed]"),
    ("card 4111111111111111 was charged", "[long number removed]"),
    ("pan ABCDE1234F", "[PAN removed]"),
    ("the otp was 448213", "[credential removed]"),
    ("aadhaar 1234 5678 9012", "[Aadhaar removed]"),
])
def test_free_text_box_is_scrubbed(text, marker):
    """Someone will eventually type their DOB into the problem box."""
    cleaned, removed = scrub(text)
    assert marker in cleaned and marker in removed


def test_scrub_reports_what_it_removed_so_the_user_can_be_told():
    brief = build_brief(
        PLAYBOOKS["zomato.delivery_delay"],
        "late order, dob 14/03/1999, otp 448213",
        {"order_id": "4471"},
    )
    assert len(brief.redactions) >= 2
    assert "14/03/1999" not in brief.problem_text


def test_ordinary_text_survives_untouched():
    cleaned, removed = scrub("my order was two hours late and cost 499 rupees")
    assert removed == [] and "499" in cleaned
