# Plan: background refresh for the Slack assistant status

Status: **not started**. Captured when `ResponseStatus` (`tiger_agent/slack/status.py`)
landed so the trade-off and the design are on record.

## Why this might be needed

Slack removes an `assistant.threads.setStatus` status two minutes after the last time it was
sent. `ResponseStatus` is purely event-driven: it re-sends the status when the text changes and,
as a keep-alive, on any agent stream event once `STATUS_REFRESH_SECONDS` (60 s) have passed since
the last send. That covers every situation in which the agent tree is emitting events, which is
almost all of them:

| `tiger-eon-internal-prod`, 14 days to 2026-09-15 | |
|---|---|
| Tool calls (excluding `delegate_task`) | 19,714 |
| p99 tool duration | 10 s |
| Tool calls over 120 s | 9 (7 × `tiger-analyst_query_warehouse` at its 300 s timeout) |

The gap is a **single tool call that emits nothing for more than two minutes**. During it no
event reaches `ResponseStatus`, Slack drops the status, and the thread shows no activity until the
tool returns. Today that is one call in roughly two thousand, concentrated in warehouse queries
that are hitting a timeout anyway. If that changes (slow MCP servers, long-running analyses,
a tool that legitimately takes minutes) this plan closes the gap.

## Design

Add an optional background refresher to `ResponseStatus` without changing its callers:

```python
class ResponseStatus:
    async def __aenter__(self) -> ResponseStatus:
        self._refresher = asyncio.create_task(self._refresh_loop())
        return self

    async def __aexit__(self, *exc) -> None:
        self._refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._refresher

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(STATUS_REFRESH_SECONDS)
            await self.refresh()   # already throttled on _last_sent, so events and the
                                   # timer never double-send
```

- `refresh()` keeps its `_last_sent` check, so a tick that lands right after an event-driven send
  is a no-op. The timer only fires a request when the tree has been silent.
- `SlackTaskHandler.handle` wraps the `run_stream_events` block in `async with status:`. The
  non-streaming path (bot-posted mentions with no `event.user`) gets the same treatment for free.
- `set_status` already swallows Slack errors, so a failed refresh cannot end the run. Log at
  `warn` and keep ticking.
- Cancellation on exit is essential: a leaked refresher would keep the thread "responding"
  after the answer is posted. Slack clears the status on post anyway, but the next tick would
  set it again.

### Optional: finalize the intro stream during a long wait

With a timer awake during tool waits, the refresher can also `stop()` the current
`AsyncChatStream` once a coordinator tool call has been outstanding for one tick and clear the
handler's reference, so the final answer opens a fresh message instead of relying on the
`message_not_in_streaming_state` retry in `append_message_to_stream`. Requires the handler to
share its `slack_stream` with `ResponseStatus`. Any append racing the stop raises and lands in the
existing buffer-preserving retry, so ordering mistakes degrade gracefully.

## Verification

- Unit: patch `STATUS_REFRESH_SECONDS` small, enter the context with no events for a few ticks,
  assert `assistant_threads_setStatus` was awaited once per tick; then feed an event and assert
  the following tick does not send (throttle).
- Production: re-run a request whose investigator calls `tiger-analyst_query_warehouse` on a
  slow query and confirm the thread status stays visible for the full wait. Logfire check:

  ```sql
  SELECT trace_id, round(duration) AS secs
  FROM records
  WHERE span_name = 'execute_tool tiger-analyst_query_warehouse' AND duration > 120
  ```

## Decide before building

- Refresh cadence. 60 s leaves a comfortable margin under Slack's 120 s; 90 s halves the
  request volume for long runs. Either is fine; do not go above 100 s.
- Whether the Salesforce handlers should get a status at all. They post one message and never
  set a status today; leaving them out keeps this change to `SlackTaskHandler`.
