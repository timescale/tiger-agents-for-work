"""In-process registry of Slack runs that can be cancelled by (channel, ts).

A run registers itself while it is answering a Slack message. When Slack
reports that message deleted, the listener looks the key up here and asks the
run to stop, so the agent loop does not keep spending model calls on a question
nobody can see the answer to.

Concurrency model: the listener and the ``num_workers`` worker tasks are all
asyncio tasks on one event loop (``TaskHarness.run`` and ``SlackListener.start``
both create their tasks in the ``TaskGroup`` that ``app.py`` owns). Coroutines on
one loop only interleave at ``await``, and nothing in this class awaits, so the
plain dict needs no lock. That invariant is what keeps this correct; if the
registry is ever reached from another thread, ``asyncio.Event.set()`` is not
thread-safe either and the whole thing needs ``loop.call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

RunKey = tuple[str, str]


class RunCancellations:
    def __init__(self) -> None:
        self._runs: dict[RunKey, asyncio.Event] = {}

    @property
    def running(self) -> int:
        return len(self._runs)

    def is_tracked(self, channel: str, ts: str) -> bool:
        return (channel, ts) in self._runs

    @contextlib.contextmanager
    def track(self, channel: str, ts: str) -> Iterator[asyncio.Event]:
        """Register a run for ``(channel, ts)``; yields the Event a cancel sets.

        Always unregisters on exit, including when the run raises. If the same
        key is tracked twice (it should not be, but two workers could in theory
        hold two attempts of one task) the newer registration wins and the older
        Event can no longer be reached by :meth:`cancel`.
        """
        key = (channel, ts)
        cancel_requested = asyncio.Event()
        self._runs[key] = cancel_requested
        try:
            yield cancel_requested
        finally:
            if self._runs.get(key) is cancel_requested:
                del self._runs[key]

    def cancel(self, channel: str, ts: str) -> bool:
        """Ask the run answering ``(channel, ts)`` to stop.

        Returns:
            True if a run was registered for that key (and has now been told to
            stop), False if nothing on this process was answering that message.
        """
        cancel_requested = self._runs.get((channel, ts))
        if cancel_requested is None:
            return False
        cancel_requested.set()
        return True
