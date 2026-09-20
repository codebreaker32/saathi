"""The only place in the package allowed to read wall-clock time or sleep.

tests/test_clock_purity.py enforces that by grep. The point is not tidiness: a
twenty-minute hold has to run in milliseconds in tests and replay identically,
and that is impossible if any module can reach for time.time() on its own.
"""

from __future__ import annotations

import time


class SystemClock:
    """Real time. Used only on a live call."""

    def now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def sleep_ms(self, ms: int) -> None:
        time.sleep(ms / 1000.0)


class VirtualClock:
    """Time only moves when someone advances it. A 20-minute hold costs ~0 s."""

    def __init__(self, start_ms: int = 0) -> None:
        self._now = start_ms

    def now_ms(self) -> int:
        return self._now

    def sleep_ms(self, ms: int) -> None:
        self.advance(ms)

    def advance(self, ms: int) -> int:
        if ms < 0:
            raise ValueError("time does not run backwards")
        self._now += ms
        return self._now


class ScaledClock:
    """Real time, compressed. For a demo that must show an 18-minute hold.

    The UI is required to render the scale factor next to the clock, so the
    viewer sees 18:42 AND '60x' rather than being implicitly told that eighteen
    minutes really passed.
    """

    def __init__(self, scale: float = 60.0, start_ms: int = 0) -> None:
        if scale <= 0:
            raise ValueError("scale must be positive")
        self.scale = scale
        self._t0 = time.monotonic()
        self._start = start_ms

    def now_ms(self) -> int:
        elapsed = (time.monotonic() - self._t0) * 1000.0
        return int(self._start + elapsed * self.scale)

    def sleep_ms(self, ms: int) -> None:
        time.sleep((ms / self.scale) / 1000.0)
