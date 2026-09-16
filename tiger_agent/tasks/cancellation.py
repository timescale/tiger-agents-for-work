"""In-process registry of Slack runs that can be cancelled while in flight.

A run registers itself while it is answering a Slack message. Two things can
then ask it to stop:

- Slack reports the message deleted (``SlackListener._on_message_deleted``):
  the run for that exact ``(channel, ts)`` is cancelled.
- The same user says "nevermind" / "cancel" in the thread (the
  ``cancel_my_requests`` agent tool): every run that user started in that
  thread is cancelled, except the one asking.

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
from dataclasses import dataclass, field
from typing import Literal

RunKey = tuple[str, str]
CancelReason = Literal["message_deleted", "user_request"]


@dataclass
class TrackedRun:
    channel: str
    ts: str
    user: str | None
    thread: str
    """The thread the run answers in: the parent ``thread_ts``, or ``ts`` itself
    when the triggering message started the thread."""
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    reason: CancelReason | None = None

    def cancel(self, reason: CancelReason) -> None:
        if self.reason is None:
            self.reason = reason
        self.cancel_requested.set()


class RunCancellations:
    def __init__(self) -> None:
        self._runs: dict[RunKey, TrackedRun] = {}

    @property
    def running(self) -> int:
        return len(self._runs)

    def is_tracked(self, channel: str, ts: str) -> bool:
        return (channel, ts) in self._runs

    @contextlib.contextmanager
    def track(
        self,
        channel: str,
        ts: str,
        *,
        user: str | None = None,
        thread_ts: str | None = None,
    ) -> Iterator[TrackedRun]:
        """Register a run answering the message ``(channel, ts)``.

        ``user`` is who asked and ``thread_ts`` the thread the message is in
        (None when the message is itself the thread root); both feed
        :meth:`cancel_for_user`. Always unregisters on exit, including when the
        run raises. If the same key is tracked twice (it should not be, but two
        workers could in theory hold two attempts of one task) the newer
        registration wins and the older one can no longer be reached.
        """
        key = (channel, ts)
        run = TrackedRun(channel=channel, ts=ts, user=user, thread=thread_ts or ts)
        self._runs[key] = run
        try:
            yield run
        finally:
            if self._runs.get(key) is run:
                del self._runs[key]

    def cancel(
        self, channel: str, ts: str, *, reason: CancelReason = "message_deleted"
    ) -> bool:
        """Ask the run answering ``(channel, ts)`` to stop.

        Returns:
            True if a run was registered for that key (and has now been told to
            stop), False if nothing on this process was answering that message.
        """
        run = self._runs.get((channel, ts))
        if run is None:
            return False
        run.cancel(reason)
        return True

    def cancel_for_user(
        self,
        *,
        channel: str,
        thread: str,
        user: str,
        except_ts: str | None = None,
        reason: CancelReason = "user_request",
    ) -> int:
        """Stop every run ``user`` started in ``thread`` of ``channel``.

        ``except_ts`` is the message asking for the cancellation: that run is
        the one calling this, and it must keep going to reply. Runs started by
        other users in the same thread are left alone, so one person cannot
        cancel another's question.

        Returns:
            How many runs were told to stop.
        """
        cancelled = 0
        for run in self._runs.values():
            if run.channel != channel or run.thread != thread:
                continue
            if run.user != user or run.ts == except_ts:
                continue
            if not run.cancel_requested.is_set():
                run.cancel(reason)
                cancelled += 1
        return cancelled
