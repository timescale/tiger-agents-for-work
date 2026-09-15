from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)

from tiger_agent.slack import status as status_module
from tiger_agent.slack.status import (
    DELEGATE_TOOL_NAME,
    ResponseStatus,
    make_subagent_status_handler,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _tool_start(tool_name: str) -> PartStartEvent:
    return PartStartEvent(index=0, part=ToolCallPart(tool_name=tool_name, args={}))


def _tool_end(tool_name: str) -> PartEndEvent:
    return PartEndEvent(index=0, part=ToolCallPart(tool_name=tool_name, args={}))


def _text_delta() -> PartDeltaEvent:
    return PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="more"))


def _delegate_call(
    call_id: str, agent_name: str = "investigator"
) -> FunctionToolCallEvent:
    return FunctionToolCallEvent(
        part=ToolCallPart(
            tool_name=DELEGATE_TOOL_NAME,
            args={"agent_name": agent_name, "task": "look into it"},
            tool_call_id=call_id,
        )
    )


def _delegate_result(call_id: str) -> FunctionToolResultEvent:
    return FunctionToolResultEvent(
        part=ToolReturnPart(
            tool_name=DELEGATE_TOOL_NAME, content="done", tool_call_id=call_id
        )
    )


def _loading_messages(client) -> list[str] | None:
    return client.assistant_threads_setStatus.await_args.kwargs["loading_messages"]


def _status_text(client) -> str:
    return client.assistant_threads_setStatus.await_args.kwargs["status"]


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def client(make_async_web_client_mock):
    return make_async_web_client_mock()


@pytest.fixture
def status(client, clock) -> ResponseStatus:
    return ResponseStatus(
        client=client, channel_id="C_CHAN", thread_ts="1.0", clock=clock
    )


class TestCoordinatorEvents:
    async def test_tool_call_start_names_the_tool(self, status, client):
        await status.on_event(_tool_start("tigerlabs_call_tool"))

        assert _loading_messages(client) == ["Calling Tool: tigerlabs_call_tool"]
        assert _status_text(client) == "is responding..."

    async def test_tool_call_end_returns_to_generic_loading_messages(
        self, status, client
    ):
        await status.on_event(_tool_start("tigerlabs_call_tool"))
        await status.on_event(_tool_end("tigerlabs_call_tool"))

        assert status.message is None
        assert len(_loading_messages(client)) > 1

    async def test_other_events_refresh_only_when_stale(self, status, client, clock):
        await status.on_event(_text_delta())
        assert client.assistant_threads_setStatus.await_count == 1

        clock.now += status_module.STATUS_REFRESH_SECONDS - 1
        await status.on_event(_text_delta())
        assert client.assistant_threads_setStatus.await_count == 1

        clock.now += 1
        await status.on_event(_text_delta())
        assert client.assistant_threads_setStatus.await_count == 2

    async def test_refresh_repeats_the_current_message(self, status, client, clock):
        await status.on_event(_tool_start("tigerlabs_call_tool"))
        clock.now += status_module.STATUS_REFRESH_SECONDS

        await status.on_event(PartStartEvent(index=1, part=TextPart(content="")))

        assert client.assistant_threads_setStatus.await_count == 2
        assert _loading_messages(client) == ["Calling Tool: tigerlabs_call_tool"]

    async def test_clear_removes_the_status(self, status, client):
        await status.on_event(_tool_start("tigerlabs_call_tool"))
        await status.clear()

        assert _status_text(client) == ""
        assert status.message is None


class TestSubtaskLifecycle:
    async def test_delegation_events_track_running_subtasks(self, status, client):
        await status.on_event(_delegate_call("c1"))
        await status.on_event(_delegate_call("c2", agent_name="investigator"))
        assert status.running_subtasks == 2
        # bookkeeping only: nothing is sent until a sub-agent does something
        assert client.assistant_threads_setStatus.await_count == 0

        await status.on_event(_delegate_result("c1"))
        assert status.running_subtasks == 1
        assert client.assistant_threads_setStatus.await_count == 0

        await status.on_event(_delegate_result("c2"))
        assert status.running_subtasks == 0
        # the last delegation returning hands the status back to the coordinator
        assert client.assistant_threads_setStatus.await_count == 1
        assert status.message is None

    async def test_unrelated_tool_results_are_ignored(self, status, client):
        await status.on_event(
            FunctionToolResultEvent(
                part=ToolReturnPart(
                    tool_name="tigerlabs_call_tool", content="x", tool_call_id="zz"
                )
            )
        )
        assert client.assistant_threads_setStatus.await_count == 0

    async def test_single_subtask_tool_call_names_the_agent(self, status, client):
        await status.on_event(_delegate_call("c1"))

        await status.on_event(
            _tool_start("tigerlabs_call_tool"), subtask="investigator"
        )

        assert _loading_messages(client) == [
            "Sub-task (investigator): tigerlabs_call_tool"
        ]

    async def test_parallel_subtasks_show_the_running_count(self, status, client):
        await status.on_event(_delegate_call("c1"))
        await status.on_event(_delegate_call("c2"))
        await status.on_event(_delegate_call("c3"))

        await status.on_event(_tool_start("tigerlabs_discover"), subtask="investigator")

        assert _loading_messages(client) == [
            "Sub-tasks (3 running): tigerlabs_discover"
        ]

    async def test_subtask_tool_end_shows_it_is_thinking(self, status, client):
        await status.on_event(_delegate_call("c1"))

        await status.on_event(_tool_end("tigerlabs_discover"), subtask="investigator")

        assert _loading_messages(client) == ["Sub-task (investigator): thinking..."]

    async def test_subtask_messages_fit_the_slack_display_limit(self, status, client):
        await status.on_event(_delegate_call("c1"))
        await status.on_event(_delegate_call("c2"))

        await status.on_event(
            _tool_start("tigerlabs_get_tool_details"), subtask="investigator"
        )

        message = _loading_messages(client)[0]
        assert len(message) <= 50, message

    async def test_missing_agent_name_falls_back_to_a_generic_label(
        self, status, client
    ):
        await status.on_event(
            FunctionToolCallEvent(
                part=ToolCallPart(
                    tool_name=DELEGATE_TOOL_NAME, args={"task": "x"}, tool_call_id="c1"
                )
            )
        )
        assert status.running_subtasks == 1


class TestSubagentHandler:
    async def test_forwards_events_tagged_with_the_agent_name(self, status, client):
        await status.on_event(_delegate_call("c1"))
        handler = make_subagent_status_handler(status)
        ctx = SimpleNamespace(agent=SimpleNamespace(name="investigator"))

        async def events():
            yield _tool_start("tigerlabs_call_tool")
            yield _text_delta()
            yield _tool_end("tigerlabs_call_tool")

        await handler(ctx, events())

        calls = [
            c.kwargs["loading_messages"]
            for c in client.assistant_threads_setStatus.await_args_list
        ]
        assert calls == [
            ["Sub-task (investigator): tigerlabs_call_tool"],
            ["Sub-task (investigator): thinking..."],
        ]

    async def test_unnamed_agent_gets_a_generic_label(self, status, client):
        handler = make_subagent_status_handler(status)
        ctx = SimpleNamespace(agent=None)

        async def events():
            yield _tool_start("tigerlabs_call_tool")

        await handler(ctx, events())

        assert _loading_messages(client) == ["Sub-task (sub-task): tigerlabs_call_tool"]
