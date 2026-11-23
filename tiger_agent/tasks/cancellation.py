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
from collections.abc import AsyncIterator, Iterator
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
        self._session_counts: dict[tuple[str, str], int] = {}

    @property
    def running(self) -> int:
        return len(self._runs)

    def is_tracked(self, channel: str, ts: str) -> bool:
        return (channel, ts) in self._runs

    def enter_session(self, channel: str, thread: str) -> int:
        """Increment the count of active requests in this session.

        Returns the new count so callers know whether this is the first
        request (count == 1) and should set the session to busy.
        """
        key = (channel, thread)
        count = self._session_counts.get(key, 0) + 1
        self._session_counts[key] = count
        return count

    def exit_session(self, channel: str, thread: str) -> int:
        """Decrement the count of active requests in this session.

        Returns the new count so callers know whether the session is now
        empty (count == 0) and should transition back to active.
        """
        key = (channel, thread)
        count = self._session_counts.get(key, 1) - 1
        if count <= 0:
            self._session_counts.pop(key, None)
            return 0
        self._session_counts[key] = count
        return count

    def get_session_count(self, channel: str, thread: str) -> int:
        """Return how many requests are currently active in this session."""
        return self._session_counts.get((channel, thread), 0)

    @contextlib.asynccontextmanager
    async def session(
        self,
        channel: str,
        thread_ts: str,
        client,
    ) -> AsyncIterator[int]:
        """Enter the session, count it, and exit on scope end.

        Sets the session to ``processing`` on first entry and back to
        ``active`` after the last exit, so overlapping requests do not
        erase each other's indicator.
        """
        from tiger_agent.slack.utils import set_status

        count = self.enter_session(channel, thread_ts)
        try:
            if count == 1:
                await set_status(
                    client=client,
                    channel_id=channel,
                    thread_ts=thread_ts,
                    is_busy=True,
                )
            yield count
        finally:
            remaining = self.exit_session(channel, thread_ts)
            if remaining == 0:
                await set_status(
                    client=client,
                    channel_id=channel,
                    thread_ts=thread_ts,
                    is_busy=False,
                )

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
