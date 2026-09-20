"""The scoreboard, and an interval honest enough to quote.

The headline number is the false-fetch rate, and it is reported as an UPPER
BOUND rather than a point estimate. With a handful of scenarios, zero false
fetches does not mean "zero percent" -- it means "we could not rule out
something in the low tens of percent", and saying so is the difference between
a measurement and a claim.

Rule of three, for intuition: N machine calls with no false fetch gives a 95%
upper bound of roughly 3/N. Claiming under 1% needs ~300 such calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb


def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k + 1))


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """Exact one-sided upper bound on a rate, given k events in n trials."""
    if n == 0:
        return 1.0
    if k >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


@dataclass
class Row:
    name: str
    truth: str
    fetched: bool
    false_fetch: bool
    missed: bool
    time_to_fetch_ms: int | None
    handed_off: bool
    probes: int
    disclosed_first: bool


@dataclass
class Summary:
    rows: list[Row]

    @property
    def machine_calls(self) -> int:
        return sum(1 for r in self.rows if r.truth != "human")

    @property
    def human_calls(self) -> int:
        return sum(1 for r in self.rows if r.truth == "human")

    @property
    def false_fetches(self) -> int:
        return sum(1 for r in self.rows if r.false_fetch)

    @property
    def false_fetch_upper(self) -> float:
        return clopper_pearson_upper(self.false_fetches, self.machine_calls)

    @property
    def missed(self) -> int:
        return sum(1 for r in self.rows if r.missed)

    def render(self) -> str:
        w = max((len(r.name) for r in self.rows), default=8) + 2
        out = [f"  {'scenario':{w}}{'truth':7}{'fetched':9}{'t+':>8}  outcome",
               "  " + "-" * (w + 34)]
        for r in self.rows:
            t = f"{r.time_to_fetch_ms/1000:.1f}s" if r.time_to_fetch_ms else "-"
            if r.false_fetch:
                verdict = "FALSE FETCH"
            elif r.missed and r.handed_off:
                verdict = "handed over (verification)"
            elif r.missed:
                verdict = "missed"
            elif r.fetched:
                verdict = "correct"
            else:
                verdict = "correctly held"
            out.append(f"  {r.name:{w}}{r.truth:7}{str(r.fetched):9}{t:>8}  {verdict}")

        n, k = self.machine_calls, self.false_fetches
        out += [
            "",
            f"  false fetches      {k}/{n} machine calls",
            f"  95% upper bound    {self.false_fetch_upper*100:.1f}%"
            f"   <- quote THIS, not {k/n*100 if n else 0:.0f}%",
            f"  missed humans      {self.missed}/{self.human_calls}"
            f"   (recoverable: the rep hangs up, we retry)",
            f"  disclosed first    {sum(r.disclosed_first for r in self.rows)}"
            f"/{len(self.rows)}",
            "",
            f"  With {n} machine call{'s' if n != 1 else ''} the interval is wide "
            f"by construction.",
            f"  Claiming under 1% would need roughly 300 of them with none.",
        ]
        return "\n".join(out)


def summarise(named_outcomes) -> Summary:
    rows = []
    for name, o in named_outcomes:
        rows.append(Row(
            name=name, truth=o.truth, fetched=o.fetched_at_ms is not None,
            false_fetch=o.false_fetch, missed=o.missed_human,
            time_to_fetch_ms=o.time_to_fetch_ms,
            handed_off=o.handed_off_at_ms is not None,
            probes=o.probes_used, disclosed_first=o.disclosed_before_speaking))
    return Summary(rows)
