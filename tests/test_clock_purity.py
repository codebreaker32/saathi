"""Wall-clock time and RNG are banned outside saathi/clock.py.

This is not tidiness. A twenty-minute hold has to run in milliseconds and
replay identically, and one stray time.time() anywhere in the package makes
both impossible -- silently, and usually only under load.
"""

import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "saathi"
BANNED = re.compile(
    r"\b(time\.time|time\.monotonic|datetime\.(now|today|utcnow)|random\.|time\.sleep)"
)
EXEMPT = {"clock.py"}


def _sources():
    return [p for p in PKG.rglob("*.py") if p.name not in EXEMPT]


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_wall_clock_outside_clock_module(path):
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if BANNED.search(line) and not line.strip().startswith("#")
    ]
    assert not offenders, "wall-clock/RNG outside clock.py:\n" + "\n".join(offenders)
