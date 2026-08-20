import logfire
from htmlslacker import HTMLSlacker

from tiger_agent.db.utils import get_salesforce_case_thread_thread_id
from tiger_agent.salesforce.types import SalesforceFeedItemEvent
from tiger_agent.salesforce.utils import (
    download_feed_attachment,
    get_feed_attachment_ids,
)
from tiger_agent.slack.utils import add_quote_block, post_response
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


class SalesforceFeedItemHandler(TaskHandler):
    """
    Syncs a Salesforce post on a case to the linked Slack thread.
    """

    @logfire.instrument("SalesforceFeedItemHandler.handle", extract_args=["task"])
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SalesforceFeedItemEvent = task.event
        result = await get_salesforce_case_thread_thread_id(
            hctx.pool, case_id=event.feed_item.ParentId
        )

        if not result:
            # if the FeedItem's case is not associated with a Slack thread, do nothing
            return

        [channel_id, thread_ts] = result

        markdown_conversion = HTMLSlacker(event.feed_item.Body).get_output().strip()
        body = add_quote_block(markdown_conversion)
        text = f"_From_ *{event.feed_item.CreatedBy.Name}* _via Tigerdata Support_\n\n{body}"

        attachment_ids = get_feed_attachment_ids(
            hctx.salesforce_client, event.feed_item.Id
        )
        file_attachments = [
            a
            for aid in attachment_ids
            if (a := download_feed_attachment(hctx.salesforce_client, aid)) is not None
        ]

        await post_response(
            client=hctx.app.client,
            channel=channel_id,
            thread_ts=thread_ts,
            text=text,
            use_mrkdwn=True,
            file_attachments=file_attachments,
        )
