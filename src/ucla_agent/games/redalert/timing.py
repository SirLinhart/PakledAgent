"""How fast the loop runs, summed up for every stretch of game time.

A match record holds the compute time of every action and the round trip of
every answer, but two figures can only be had by comparing one call with the
next: how long the agent waited for a fresh state, and how many game ticks
went by between two of its decisions. `TimingWindows` keeps all four figures
as the calls come in and, each time a window of game ticks is over, gives
their mean and maximum as the data of one `timing` event. A record cut short
by a killed process still says how the loop ran up to its last full window.

The figures are handed in as numbers. Nothing here reads a clock, so that
what a window reports is exactly what the loop measured.
"""

from __future__ import annotations

from typing import Any

_DECIMALS = 3


class _Figure:
    """The mean and the maximum of the values of one window, kept without keeping the values."""

    def __init__(self) -> None:
        self._count = 0
        self._total = 0.0
        self._largest = 0.0

    def add(self, value: float) -> None:
        self._count += 1
        self._total += value
        self._largest = value if self._count == 1 else max(self._largest, value)

    def summary(self) -> dict[str, float | None]:
        """The figure as a `timing` event holds it; both values are null for a window without any."""
        if not self._count:
            return {"mean": None, "max": None}
        return {"mean": round(self._total / self._count, _DECIMALS), "max": round(self._largest, _DECIMALS)}


class TimingWindows:
    """The loop's timing figures, window by window. See the module's note."""

    def __init__(self, window_ticks: int) -> None:
        """A window is over once `window_ticks` game ticks have passed since it began."""
        self._window_ticks = window_ticks
        self._from_tick: int | None = None
        # The state version and the time of the call before this one.
        self._last_call: tuple[int, float] | None = None
        self._start_window()

    def _start_window(self) -> None:
        self._compute_ms = _Figure()
        self._round_trip_ms = _Figure()
        self._state_age_ms = _Figure()
        self._ticks_per_call = _Figure()

    def note_call(self, tick: int, state_version: int, now_ms: float) -> dict[str, Any] | None:
        """Take in the start of one call for a state of the running game, at the time `now_ms`.

        Returns the data of a `timing` event when this call ends a window, and
        None otherwise. The first window begins with the first call.
        """
        if self._last_call is not None:
            last_version, last_ms = self._last_call
            self._state_age_ms.add(now_ms - last_ms)
            self._ticks_per_call.add(state_version - last_version)
        self._last_call = (state_version, now_ms)
        if self._from_tick is None:
            self._from_tick = tick
            return None
        if tick - self._from_tick < self._window_ticks:
            return None
        event = {
            "from_tick": self._from_tick,
            "to_tick": tick,
            "compute_ms": self._compute_ms.summary(),
            "round_trip_ms": self._round_trip_ms.summary(),
            "state_age_ms": self._state_age_ms.summary(),
            "ticks_per_call": self._ticks_per_call.summary(),
        }
        self._from_tick = tick
        self._start_window()
        return event

    def note_compute(self, compute_ms: float) -> None:
        """Take in how long one call took from its entry to its return."""
        self._compute_ms.add(compute_ms)

    def note_round_trip(self, round_trip_ms: float) -> None:
        """Take in how long the platform took to answer one batch."""
        self._round_trip_ms.add(round_trip_ms)
