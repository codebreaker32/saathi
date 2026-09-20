"""The company is ASKED FOR, not inferred.

Inferring it from prose kept losing. Capitalisation missed "nykaa"; a closed
vocabulary then missed "savana"; and every brand that does not exist yet defeats
both. Reported twice, from two different directions, which is the signal that
the approach rather than the list was wrong.

One input box is always right. The extraction heuristic stays as a fallback for
callers that have only prose, but a typed name beats it every time.
"""

import pytest

from saathi.understand import understand


def co(company, text="charged twice"):
    return understand(text, company=company).playbook


# --------------------------------------------------------------------------- #
# What the guesser could never do
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["savana", "nykaa", "quibblr", "zeptonow",
                                  "MegaMart", "a1 services", "9to5 cleaners"])
def test_any_company_at_all_is_accepted(name):
    """Proves: no vocabulary, no capitalisation rule, no list to fall off.
    Does NOT prove: that a playbook exists for them. It will not -- they get the
    generic line, which is the honest outcome for a company nobody has written
    an IVR map for.
    """
    pb = co(name)
    assert pb.id == "generic.unknown"
    assert pb.company.lower().startswith(name.split()[0].lower())


def test_lower_case_is_tidied_but_odd_shapes_are_left_alone():
    assert co("savana").company == "Savana"
    assert co("BigBasket").company == "BigBasket"
    assert co("1mg").company == "1mg"


# --------------------------------------------------------------------------- #
# A typed name still routes to a real playbook
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,expected", [
    ("Zomato", "zomato.delivery_delay"),
    ("zomato", "zomato.delivery_delay"),
    ("Amazon", "amazon."),
])
def test_a_known_company_still_reaches_its_own_line(name, expected):
    """Asking must not throw away the playbooks that DO exist -- naming Zomato
    has to reach Zomato's IVR, not the generic one."""
    assert co(name, "order was late and I want a refund").id.startswith(expected)


def test_the_typed_name_beats_a_name_buried_in_the_prose():
    """MUTATION CONTROL for the whole change. If the prose still won, the input
    box would be decoration: a user correcting a bad guess could not.
    """
    pb = understand("my Zomato order never arrived", company="Savana").playbook
    assert pb.company == "Savana"
    assert pb.id == "generic.unknown"


def test_prose_extraction_still_works_when_nothing_was_typed():
    """The fallback is kept, not replaced. Callers with only prose -- a future
    transport, a test -- still get a best guess."""
    assert understand("my Zomato order was late").playbook.company == "Zomato"


def test_an_empty_company_does_not_pretend_to_know():
    pb = understand("something went wrong", company="").playbook
    assert pb.company == "the company"


def test_whitespace_is_not_a_company():
    assert understand("charged twice", company="   ").playbook.company == "the company"
