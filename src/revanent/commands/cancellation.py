"""Thread-safe cooperative cancellation source for command requests."""

from __future__ import annotations

from threading import Event


class CancellationSource:
    """Own a cancellation signal while exposing the token protocol read-only."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()
