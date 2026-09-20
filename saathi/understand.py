"""One sentence in, a playbook and the facts it needs out.

Grammar first, model second -- the same discipline as IVR navigation. Company
names and problem words are a small closed vocabulary, so matching them is
deterministic, instant and testable. A model is only worth spending on the
residue, and the residue here is usually nothing.

What this deliberately does NOT do is pull credentials out of the sentence.
Facts are matched against the playbook's SAFE_TO_STATE whitelist, so a card
number typed into the box cannot become a fact -- it falls through to the
problem text, where the scrubber removes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from saathi.playbook import build_brief, load_all
from saathi.types import Playbook

PROBLEM_WORDS = {
    "delivery_delay": ("late", "delay", "delayed", "hours late", "never arrived",
                       "still waiting", "not delivered"),
    "refund_missing": ("refund", "money back", "not refunded", "refund not",
                       "promised a refund"),
    "double_charge": ("twice", "double", "duplicate", "charged two", "two times"),
}

# The digit group must not END on a comma, or "order 4471, rs 499" matches
# "4471," followed by "rs" and the order number becomes the amount.
_NUM = r"\d{1,3}(?:,\d{2,3})*|\d+"
AMOUNT = re.compile(rf"(?:rs\.?|₹|inr)\s*({_NUM})|\b({_NUM})\s*(?:rupees|rs\b)", re.I)
# The token must CONTAIN A DIGIT. Without that, "my order never arrived" parsed
# as order id "never", and a fabricated order number is worse than none: it
# travels in the brief and Saathi reads it out to the company.
ORDER = re.compile(
    r"\border(?:\s*(?:id|no|number|#))?\s*[:#]?\s*((?=[A-Z0-9-]*\d)[A-Z0-9-]{4,16})\b",
    re.I)


@dataclass(frozen=True)
class Understanding:
    playbook: Playbook | None
    facts: dict[str, str]
    confidence: str          # high | low | none
    why: str


def _score(pb: Playbook, text: str) -> tuple[int, list[str]]:
    t = text.lower()
    hits, pts = [], 0
    if pb.company.lower().split()[0] in t:
        pts += 3
        hits.append(f"named {pb.company}")
    kind = pb.id.split(".", 1)[1]
    for w in PROBLEM_WORDS.get(kind, ()):
        if w in t:
            pts += 2
            hits.append(f"said '{w}'")
            break
    return pts, hits


GENERIC_ID = "generic.unknown"

# A brand is a capitalised word that is not the first word of the sentence and
# not an ordinary one. Crude on purpose: the cost of a wrong guess here is a
# label on a screen, and the alternative was refusing to proceed at all.
_STOP = {"I", "My", "The", "A", "An", "It", "They", "We", "Their", "This",
         "There", "Order", "Rs", "INR", "Please", "Hi", "Hello"}
_BRAND = re.compile(r"^[A-Z][A-Za-z0-9&.-]{2,}$")


# A small closed vocabulary, matched case-INSENSITIVELY, because people type
# brand names in lower case constantly -- "my nykaa order never arrived" was the
# report that prompted this. The capitalisation heuristic below cannot see those
# and fell through to "the company", which then read as though Saathi had not
# understood who the user meant.
#
# A list rather than a model, for the same reason PROBLEM_WORDS is a list: this
# is a small closed vocabulary where matching is deterministic, instant and
# testable, and the residue a model would earn is close to nothing. Unknown
# brands still work -- they just rely on capitalisation.
KNOWN_BRANDS = (
    "amazon", "flipkart", "myntra", "nykaa", "ajio", "meesho", "snapdeal",
    "zomato", "swiggy", "blinkit", "zepto", "bigbasket", "dunzo", "instamart",
    "uber", "ola", "rapido", "redbus", "makemytrip", "goibibo", "ixigo",
    "paytm", "phonepe", "gpay", "razorpay", "cred", "groww", "zerodha",
    "airtel", "jio", "vodafone", "vi", "bsnl", "actfibernet", "hathway",
    "tata", "croma", "reliance", "dmart", "lenskart", "pharmeasy", "1mg",
    "urbancompany", "netflix", "hotstar", "spotify", "zomatogold",
)


def company_in(text: str) -> str | None:
    """Best guess at who the user is talking about.

    Crude on purpose: the cost of a wrong guess is a label on a screen, and the
    alternative was refusing to proceed at all. A capitalised word that is not
    an ordinary English opener is the whole heuristic.
    """
    words = [w.strip(",.!?;:'\"") for w in text.split()]

    # Closed vocabulary first: it is case-insensitive, so it catches the way
    # people actually type. Title-cased on the way out so the UI reads cleanly.
    for w in words:
        if w.lower() in KNOWN_BRANDS:
            return w if w[:1].isupper() else w.capitalize()

    cands = [w for w in words if _BRAND.match(w) and w not in _STOP]
    if not cands:
        return None
    # Prefer a brand that is not the first word; a sentence-initial capital is
    # weaker evidence, but it still beats nothing.
    for i, w in enumerate(words):
        if w in cands and i > 0:
            return w
    return cands[0]


def understand(text: str, playbooks: dict[str, Playbook] | None = None
               ) -> Understanding:
    pbs = playbooks or load_all()
    # The generic playbook is the floor, not a competitor: it matches nothing by
    # name, so scoring it alongside the real ones would let it tie at zero and
    # win on sort order.
    specific = {k: v for k, v in pbs.items() if k != GENERIC_ID}
    scored = sorted(((*_score(p, text), p) for p in specific.values()),
                    key=lambda x: -x[0]) or [(0, [], None)]
    top, hits, pb = scored[0]

    # THE COMPANY MUST BE NAMED. _score gives +3 for the company and +2 for a
    # problem word, so a bare "my order is late" scored 2 and claimed whichever
    # delivery playbook sorted first -- a Flipkart complaint was routed to
    # Amazon. Claiming the wrong company is worse than admitting we do not know
    # it, because the brief is built from the playbook.
    if top < 3:
        top = 0

    if top == 0:
        generic = pbs.get(GENERIC_ID)
        if generic is None:
            return Understanding(None, {}, "none",
                                 "no playbook matched -- name the company and "
                                 "what went wrong")
        named = company_in(text)
        if named:
            generic = replace(generic, company=named)
        # Facts still go through the generic playbook's own whitelist, so an
        # unknown company gets the same air gap as a known one.
        facts: dict[str, str] = {}
        safe = {f.field for f in generic.safe_fields()}
        if (m := ORDER.search(text)) and "order_id" in safe:
            facts["order_id"] = m.group(1)
        if (m := AMOUNT.search(text)) and "amount" in safe:
            facts["amount"] = (m.group(1) or m.group(2)).replace(",", "")
        return Understanding(
            generic, facts, "low",
            (f"no playbook for {named}" if named else "no company recognised")
            + " -- Saathi will state the problem and ask for a reference number, "
              "and will not agree to anything")

    facts: dict[str, str] = {}
    safe = {f.field for f in pb.safe_fields()}
    if (m := ORDER.search(text)) and "order_id" in safe:
        facts["order_id"] = m.group(1)
    if (m := AMOUNT.search(text)) and "amount" in safe:
        facts["amount"] = (m.group(1) or m.group(2)).replace(",", "")

    runner_up = scored[1][0] if len(scored) > 1 else 0
    conf = "high" if top >= 5 and top > runner_up else "low"
    return Understanding(pb, facts, conf, ", ".join(hits))


def brief_for(u: Understanding, text: str, login_facts: dict[str, str]):
    """Whitelisted, scrubbed, and the only channel into the call."""
    if u.playbook is None:
        raise ValueError("no playbook")
    return build_brief(u.playbook, text, {**login_facts, **u.facts})
