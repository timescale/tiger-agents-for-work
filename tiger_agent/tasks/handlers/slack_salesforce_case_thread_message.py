import logfire

from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT,
    SALESFORCE_CASE_SUPPORT_EMAIL,
    SALESFORCE_INTERNAL_FROM_NAME_SUFFIX,
)
from tiger_agent.salesforce.utils import (
    add_case_email_comment,
    build_email_attachments_from_slack_files,
    replace_all_slack_mentions_with_links_to_profile,
)
from tiger_agent.slack.types import SlackSalesforceCaseThreadMessageEvent
from tiger_agent.slack.utils import (
    fetch_user_info,
    get_a_href_link_to_user_profile,
    user_is_external,
)
from tiger_agent.tasks.handlers.base import TaskHandler
from tiger_agent.tasks.types import Task


class SlackSalesforceCaseThreadMessageHandler(TaskHandler):
    """
    Syncs a Slack message posted in a Salesforce-linked thread back to the
    Salesforce case as an email comment, including any file attachments.
    """

    @logfire.instrument(
        "SlackSalesforceCaseThreadMessageHandler.handle", extract_args=False
    )
    async def handle(self, task: Task) -> None:
        hctx = self._hctx
        event: SlackSalesforceCaseThreadMessageEvent = task.event

        user_info = await fetch_user_info(hctx.app.client, user_id=event.user)
        is_external_user = user_is_external(bot_info=hctx.bot_info, user_info=user_info)
        link_to_user_profile = await get_a_href_link_to_user_profile(
            hctx=hctx, user_info=user_info
        )

        text_prefix = f"[Replied via Slack as @{user_info.name}]"
        html_prefix = f"[Replied via Slack as {link_to_user_profile}</a>]"

        attachments = await build_email_attachments_from_slack_files(
            client=hctx.app.client, event=event
        )

        [
            html_message_body,
            plain_message_body,
        ] = await replace_all_slack_mentions_with_links_to_profile(
            hctx=hctx, message=event.text
        )

        add_case_email_comment(
            hctx.salesforce_client,
            case_id=event.salesforce_case_id,
            body=f"{text_prefix}\n{plain_message_body}",
            html_body=f"<p>{html_prefix}</p><p>{html_message_body}</p>",
            from_address=user_info.profile.email,
            to_address=SALESFORCE_CASE_SUPPORT_EMAIL if is_external_user else None,
            subject=SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT,
            from_name=f"{user_info.real_name} ({SALESFORCE_INTERNAL_FROM_NAME_SUFFIX})"
            if not is_external_user
            else None,
            attachments=attachments if attachments else None,
        )

        logfire.info(
            "Synced Slack message to Salesforce",
            case_id=event.salesforce_case_id,
            comment_body=event.text,
            user_id=event.user,
            user_name=user_info.real_name,
            user_is_external=is_external_user,
        )
