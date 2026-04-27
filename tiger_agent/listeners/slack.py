import re
from asyncio import TaskGroup
from collections.abc import Sequence
from typing import Any

import logfire
from pydantic_ai.messages import BinaryContent
from slack_bolt.adapter.socket_mode.websockets import AsyncSocketModeHandler
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.respond.async_respond import AsyncRespond

from tiger_agent.db.utils import (
    get_event_hist,
    get_salesforce_account_id_for_channel,
    get_salesforce_case_thread_case_id,
    insert_event,
    insert_handled_event,
)
from tiger_agent.listeners import Listener
from tiger_agent.salesforce.constants import (
    SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT,
    SALESFORCE_CASE_SUPPORT_EMAIL,
    SALESFORCE_INTERNAL_FROM_NAME_SUFFIX,
)
from tiger_agent.salesforce.types import (
    AgentFeedbackRatingEvent,
    SalesforceCreateNewCaseEvent,
)
from tiger_agent.salesforce.utils import (
    EmailAttachment,
    add_case_email_comment,
    get_services_for_account,
)
from tiger_agent.slack.commands import (
    handle_command,
)
from tiger_agent.slack.constants import (
    AGENT_FEEDBACK_RATING,
    CONFIRM_PROACTIVE_PROMPT,
    NEW_SALESFORCE_CASE_WORKFLOW_FORM_CANCEL,
    NEW_SALESFORCE_CASE_WORKFLOW_FORM_SUBMIT,
    NEW_SALESFORCE_CASE_WORKFLOW_FORM_TRIGGER,
    REJECT_PROACTIVE_PROMPT,
    SLACK_APP_TOKEN,
)
from tiger_agent.slack.types import BotInfo, SlackCommand, UserInfo
from tiger_agent.slack.utils import (
    download_private_file,
    fetch_bot_info,
    fetch_team_info,
    fetch_user_info,
    handle_new_salesforce_case_workflow_form_cancel,
    handle_new_salesforce_case_workflow_form_submit,
    handle_proactive_prompt,
    send_new_salesforce_case_workflow_form,
    send_proactive_prompt,
    set_status,
)
from tiger_agent.tasks.types import HarnessContext, TaskProcessor
from tiger_agent.tasks.utils import process_task


class SlackListener(Listener):
    """Listens for Slack events and enqueues tasks for processing.

    Args:
        ctx: Shared context providing app, pool, trigger, and optional salesforce client
        task_processor: Callback to process tasks triggered by interactive actions
        slack_app_token: Slack app-level token for Socket Mode
        proactive_prompt_channels: Optional set of channel IDs for proactive prompting
    """

    def __init__(
        self,
        hctx: HarnessContext,
        task_processor: TaskProcessor,
    ):
        self._hctx = hctx
        self._pool = hctx.pool
        self._app = hctx.app
        self._trigger = hctx.trigger
        self._task_processor = task_processor
        self._proactive_prompt_channels = (
            set(hctx.proactive_prompt_channels)
            if hctx.proactive_prompt_channels
            else None
        )
        self._bot_info: BotInfo | None = None

    async def start(self, tasks: TaskGroup):
        self._bot_info = await fetch_bot_info(self._app.client)
        self._hctx.bot_info = self._bot_info
        self._app.action(CONFIRM_PROACTIVE_PROMPT)(self._handle_proactive_prompt)
        self._app.action(REJECT_PROACTIVE_PROMPT)(self._handle_proactive_prompt)
        self._app.action(AGENT_FEEDBACK_RATING)(self._handle_agent_feedback_rating)
        self._app.action(NEW_SALESFORCE_CASE_WORKFLOW_FORM_SUBMIT)(
            self._handle_new_salesforce_case_workflow_form_submit
        )
        self._app.action(NEW_SALESFORCE_CASE_WORKFLOW_FORM_CANCEL)(
            self._handle_new_salesforce_case_workflow_form_cancel
        )
        self._app.action(NEW_SALESFORCE_CASE_WORKFLOW_FORM_TRIGGER)(
            self._handle_new_salesforce_case_workflow_form_trigger
        )
        self._app.event("message")(self._on_message)
        self._app.command(re.compile(r"\/.*"))(self._on_slack_admin_command)
        self._app.event("app_mention")(self._on_slack_event)

        handler = AsyncSocketModeHandler(self._app, app_token=SLACK_APP_TOKEN)
        tasks.create_task(handler.start_async())

    async def _on_slack_event(self, ack: AsyncAck, event: dict[str, Any]):
        await set_status(
            self._app.client,
            channel_id=event.get("channel"),
            thread_ts=event.get("thread_ts") or event.get("ts"),
            is_busy=True,
        )
        await insert_event(self._pool, event)
        await ack()
        await self._trigger.put(True)

    async def _on_slack_admin_command(
        self, ack: AsyncAck, respond: AsyncRespond, command: dict[str, Any]
    ):
        slack_command = SlackCommand(**command)
        await ack()
        response = await handle_command(
            command=slack_command, hctx=self._hctx, bot_info=self._bot_info
        )
        await respond(text=response, response_type="ephemeral", delete_original=True)

    async def get_reply_prefix_for_sender(self, user_info: UserInfo) -> tuple[str, str]:
        """Returns (text_prefix, html_prefix) for use in Salesforce email comments."""
        in_same_team_as_bot = self._bot_info.team_id == user_info.team_id
        profile_workspace_url: str | None = None
        if in_same_team_as_bot:
            profile_workspace_url = self._bot_info.url.strip("/")
        else:
            team_info = await fetch_team_info(
                self._app.client, team_id=user_info.team_id
            )
            if team_info:
                profile_workspace_url = f"https://{team_info.domain}.slack.com"

        text_prefix = f"[Replied via Slack as @{user_info.name}]"
        if profile_workspace_url:
            user_profile_url = f"{profile_workspace_url}/team/{user_info.id}"
            html_prefix = f'[Replied via Slack as <a href="{user_profile_url}">@{user_info.name}</a>]'
        else:
            html_prefix = text_prefix

        return text_prefix, html_prefix

    async def _on_message(self, ack: AsyncAck, event: dict[str, Any]):
        await ack()

        # agent should ignore its own messages
        user = event.get("user")
        if user == self._bot_info.user_id or user is None:
            return

        event["subtype"] = event["channel_type"]
        channel = event.get("channel")
        thread_ts = event.get("thread_ts")
        files = event.get("files", [])

        # if the message was in an im to the agent, respond (even though agent was not mentioned)
        if event["subtype"] in ("im"):
            await self._on_slack_event(ack, event)
            return

        # if the message is in a thread that is correlated to a Salesforce case
        # and Salesforce is configured
        if (
            thread_ts
            and self._hctx.salesforce_client
            and (
                salesforce_case_id_for_slack_thread
                := await get_salesforce_case_thread_case_id(
                    self._pool, thread_ts=thread_ts, channel_id=channel
                )
            )
        ):
            if not (text := event.get("text", "")):
                logfire.info("No text in Slack message, not syncing to Salesforce")
                return

            user_info = await fetch_user_info(self._app.client, user_id=user)
            user_is_external = (
                user_info.is_external or user_info.team_id != self._bot_info.team_id
            )
            text_prefix, html_prefix = await self.get_reply_prefix_for_sender(user_info)

            attachments: list[EmailAttachment] = []

            for file in files:
                type = file.get("mimetype")
                url = file.get("url_private_download")
                name = file.get("name")

                file_content = await download_private_file(
                    url_private_download=url,
                )
                if isinstance(file_content, BinaryContent):
                    attachments.append(
                        EmailAttachment(
                            name=name,
                            body=file_content.data,
                            content_type=type,
                        )
                    )

            add_case_email_comment(
                self._hctx.salesforce_client,
                case_id=salesforce_case_id_for_slack_thread,
                body=f"{text_prefix}\n{text}",
                html_body=f"<p>{html_prefix}</p><p>{text}</p>",
                from_address=user_info.profile.email,
                to_address=SALESFORCE_CASE_SUPPORT_EMAIL if user_is_external else None,
                subject=SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT,
                from_name=f"{user_info.real_name} ({SALESFORCE_INTERNAL_FROM_NAME_SUFFIX})"
                if not user_is_external
                else None,
                attachments=attachments if attachments else None,
            )

            logfire.info(
                "Synced Slack message to Salesforce",
                case_id=salesforce_case_id_for_slack_thread,
                comment_body=text,
                user_id=user,
                user_name=user_info.real_name,
                user_is_external=user_is_external,
            )

        # if proactive prompting is enabled for channel and agent is not mentioned
        # then offer a proactive prompt
        elif (
            self._proactive_prompt_channels
            and channel in self._proactive_prompt_channels
            and not re.search(
                rf"<@{re.escape(self._bot_info.user_id)}>", event.get("text", "")
            )
            and not thread_ts
        ):
            user = event.get("user")

            event_hist_id = await insert_handled_event(pool=self._pool, event=event)

            await send_proactive_prompt(
                client=self._app.client,
                channel=channel,
                user=user,
                event_hist_id=event_hist_id,
            )

    async def _handle_new_salesforce_case_workflow_form_submit(
        self, ack: AsyncAck, body: dict[str, Any], respond: AsyncRespond
    ):
        form_data = await handle_new_salesforce_case_workflow_form_submit(
            ack=ack, body=body, respond=respond
        )
        if form_data is None:
            return

        user = (body.get("user") or {}).get("id")
        channel = (body.get("channel") or {}).get("id")
        service_id: str | None = None
        project_id: str | None = None
        maybe_project_and_service = form_data.get("service")

        if maybe_project_and_service:
            # we can get either "<project id>" or "<project id>|<service id>"
            items = maybe_project_and_service.split("|")
            if len(items) == 1:
                project_id = items[0]
            elif len(items) == 2:
                project_id = items[0]
                service_id = items[1]

        if not user or not channel:
            logfire.error(
                "Could not determine user or channel from new Salesforce case form submission",
                body=body,
            )
            return

        await insert_event(
            self._pool,
            SalesforceCreateNewCaseEvent(
                subject=form_data["subject"],
                description=form_data["description"],
                user=user,
                channel=channel,
                severity="Severity 3 - Medium",  # for now, this will be hardcoded
                project_id=project_id,
                service_id=service_id,
            ).model_dump(),
        )
        await self._trigger.put(True)

    async def _handle_new_salesforce_case_workflow_form_cancel(
        self, ack: AsyncAck, respond: AsyncRespond
    ):
        await handle_new_salesforce_case_workflow_form_cancel(ack=ack, respond=respond)

    async def _handle_new_salesforce_case_workflow_form_trigger(
        self, ack: AsyncAck, body: dict[str, Any]
    ):
        await ack()
        channel = body.get("channel", {}).get("id")
        user = body.get("user", {}).get("id")
        if not channel or not user:
            return
        salesforce_account_id_for_channel = await get_salesforce_account_id_for_channel(
            self._pool, channel_id=channel
        )
        if not salesforce_account_id_for_channel:
            return
        services_and_projects = get_services_for_account(
            self._hctx.salesforce_client, salesforce_account_id_for_channel
        )
        await send_new_salesforce_case_workflow_form(
            client=self._app.client,
            channel=channel,
            user=user,
            services=services_and_projects,
        )

    async def _handle_proactive_prompt(
        self, ack: AsyncAck, body: dict[str, Any], respond: AsyncRespond
    ):
        event_hist_id = await handle_proactive_prompt(
            ack=ack, body=body, respond=respond, bot_info=self._bot_info
        )

        if not event_hist_id:
            return

        event = await get_event_hist(self._pool, event_hist_id)

        if event is None:
            logfire.error(
                "Could not find event_hist record", event_hist_id=event_hist_id
            )
            return

        await process_task(self._task_processor, self._hctx, event)

    async def _handle_agent_feedback_rating(
        self, ack: AsyncAck, body: dict[str, Any], respond: AsyncRespond
    ):
        await ack()

        actions = body.get("actions")
        if actions is None or not isinstance(actions, Sequence) or len(actions) != 1:
            logfire.error("Actions was not an expected payload", event=body)
            return

        selected_option = actions[0].get("selected_option")
        if selected_option is None:
            logfire.error("No selected option in feedback rating action", event=body)
            return

        value = selected_option.get("value")
        if value is None:
            logfire.error("No value in selected option", event=body)
            return

        parts = value.split("|")
        if len(parts) != 4:
            logfire.error("Unexpected value format in feedback rating", value=value)
            return

        agent_message_ts, channel, user, rating = parts

        await respond(
            response_type="ephemeral",
            text="",
            replace_original=True,
            delete_original=True,
        )

        logfire.info(
            "Agent feedback rating received",
            agent_message_ts=agent_message_ts,
            channel=channel,
            user=user,
            rating=rating,
        )

        await insert_handled_event(
            pool=self._pool,
            event=AgentFeedbackRatingEvent(
                message_ts=agent_message_ts,
                channel=channel,
                rating=int(rating),
            ).model_dump(),
        )
