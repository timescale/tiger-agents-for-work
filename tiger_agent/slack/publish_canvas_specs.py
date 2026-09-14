from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_sdk.errors import SlackApiError

from tiger_agent.slack.utils import publish_canvas_in_thread


def _response(data: dict) -> MagicMock:
    response = MagicMock()
    response.data = data
    return response


@pytest.fixture
def client():
    client = MagicMock()
    client.canvases_create = AsyncMock(
        return_value=_response({"canvas_id": "F0CANVAS"})
    )
    client.canvases_access_set = AsyncMock(return_value=_response({"ok": True}))
    client.files_info = AsyncMock(
        return_value=_response(
            {"file": {"permalink": "https://x.slack.com/docs/T/F0CANVAS"}}
        )
    )
    client.chat_postMessage = AsyncMock(return_value=_response({"ts": "1.2"}))
    return client


class TestPublishCanvasInThread:
    async def test_creates_shares_then_links_in_order(self, client):
        calls: list[str] = []
        for name in (
            "canvases_create",
            "canvases_access_set",
            "files_info",
            "chat_postMessage",
        ):
            getattr(client, name).side_effect = _recorder(
                calls, name, getattr(client, name).return_value
            )

        permalink = await publish_canvas_in_thread(
            client, channel="C_OUT", thread_ts="1.1", title="Report", markdown="# Hi"
        )

        assert calls == [
            "canvases_create",
            "canvases_access_set",
            "files_info",
            "chat_postMessage",
        ]
        assert permalink == "https://x.slack.com/docs/T/F0CANVAS"
        create = client.canvases_create.await_args.kwargs
        assert create["document_content"] == {"type": "markdown", "markdown": "# Hi"}
        share = client.canvases_access_set.await_args.kwargs
        assert share["canvas_id"] == "F0CANVAS" and share["channel_ids"] == ["C_OUT"]
        post = client.chat_postMessage.await_args.kwargs
        assert post["thread_ts"] == "1.1"
        assert "https://x.slack.com/docs/T/F0CANVAS" in post["text"]
        # A canvas link only renders as a card when unfurled.
        assert post["unfurl_links"] is True

    async def test_a_failed_share_propagates_so_the_caller_can_fall_back(self, client):
        client.canvases_access_set.side_effect = SlackApiError(
            "missing_scope", _response({"ok": False, "error": "missing_scope"})
        )

        with pytest.raises(SlackApiError):
            await publish_canvas_in_thread(
                client,
                channel="C_OUT",
                thread_ts="1.1",
                title="Report",
                markdown="# Hi",
            )

        client.chat_postMessage.assert_not_awaited()


def _recorder(calls: list[str], name: str, value):
    async def side_effect(*args, **kwargs):
        calls.append(name)
        return value

    return side_effect
