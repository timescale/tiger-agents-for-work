from typing import Any

import logfire
from psycopg_pool import AsyncConnectionPool
from simple_salesforce.api import Salesforce
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.respond.async_respond import AsyncRespond
from slack_sdk.web.async_client import (
    AsyncWebClient,
)

from tiger_agent.agent.assess_case_for_spam_agent import assess_case_for_spam
from tiger_agent.agent.summarize_new_case_agent import summarize_new_case
from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.types import AgentResponseContext
from tiger_agent.db.utils import (
    add_salesforce_case_thread,
    get_salesforce_account_id_for_channel,
)
from tiger_agent.salesforce.constants import (
    CLOUD_IMPACT_FIELD,
    SALESFORCE_CASE_CHANNEL,
    SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD,
    SALESFORCE_SLACK_THREAD_FIELD,
)
from tiger_agent.salesforce.types import (
    CaseData,
    SalesforceCaseCreatedEvent,
    ServiceRecord,
)
from tiger_agent.salesforce.utils import (
    add_internal_case_post,
    create_case_url,
    get_pick_list_values,
    get_services_for_account,
    update_case,
)
from tiger_agent.slack.constants import (
    NEW_SALESFORCE_CASE_WORKFLOW_FORM_CANCEL,
    NEW_SALESFORCE_CASE_WORKFLOW_FORM_SUBMIT,
)
from tiger_agent.slack.types import (
    SlackMessage,
)
from tiger_agent.slack.utils import add_quote_block, post_response, request_feedback
from tiger_agent.tasks.handlers.constants import (
    CUSTOMER_IMPACT_ACTION_ID,
    CUSTOMER_IMPACT_BLOCK_ID,
    DESCRIPTION_ACTION_ID,
    DESCRIPTION_BLOCK_ID,
    SERVICE_ACTION_ID,
    SERVICE_BLOCK_ID,
    SUBJECT_ACTION_ID,
    SUBJECT_BLOCK_ID,
)
from tiger_agent.tasks.handlers.types import NewSalesforceCaseFormSubmission
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext


async def create_slack_thread_for_case(
    hctx: HarnessContext, case: CaseData, channel: str, submitter: str | None = None
) -> None:
    subject = case.Subject or ""
    short_description = await summarize_new_case(
        subject=subject, description=case.Description or ""
    )
    submitter = (
        submitter or case.SuppliedName or case.SuppliedEmail or case.ContactEmail
    )
    response = await post_response(
        client=hctx.app.client,
        channel=channel,
        thread_ts=None,
        text="\n".join(
            [
                "*Support Case Created*",
                *([f"_Submitter:_ {submitter}"] if submitter else []),
                f"_Case Number:_ `{case.CaseNumber}`",
                f"_Subject:_ `{subject[0:1000]}`",
                *(
                    [f"_Project Id:_: `{case.Cloud_Project_ID__c}`"]
                    if case.Cloud_Project_ID__c
                    else []
                ),
                *(
                    [f"_Service Id:_: `{case.Cloud_Service_ID__c}`"]
                    if case.Cloud_Service_ID__c
                    else []
                ),
                *(
                    [f"_Cloud Impact:_: `{case.Cloud_Impact__c}`"]
                    if case.Cloud_Impact__c
                    else []
                ),
                *([f"_Severity:_: `{case.Severity__c}`"] if case.Severity__c else []),
                "_Description:_",
                add_quote_block(short_description),
            ]
        ),
    )

    if not response:
        logfire.error("Failed to post message, aborting")
        return

    new_case_thread_ts = response.data.get("ts", None)
    if not new_case_thread_ts:
        raise Exception(
            "Could not create a thread for the customer-created Salesforce case"
        )

    await add_salesforce_case_thread(
        hctx.pool,
        thread_ts=new_case_thread_ts,
        channel_id=channel,
        case_id=case.Id,
    )

    if not SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD:
        logfire.error("SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD not specified, skipping")
        return

    result = await hctx.app.client.chat_getPermalink(
        channel=channel,
        message_ts=new_case_thread_ts,
    )
    permalink = result.data.get("permalink")

    update_case(
        hctx.salesforce_client,
        case.Id,
        {SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD: permalink},
    )

    logfire.info(
        "Updated Salesforce case to include the customer thread link",
        extra={"permalink": permalink},
    )


async def detect_spam_case(hctx: HarnessContext, task: Task, agent: TigerAgent) -> None:
    event: SalesforceCaseCreatedEvent = task.event

    # at this time we only care about filtering email messages
    if (event.case.Origin or "").lower() != "email":
        return

    if hctx.bot_info is None:
        logfire.error("Cannot assess a case for spam without bot info, aborting")
        return

    output = await assess_case_for_spam(
        agent=agent,
        ctx=AgentResponseContext(task=task, mention=event, bot=hctx.bot_info),
        case=event.case,
    )

    if not output.is_spam:
        return

    logfire.info(
        "Salesforce case identified as spam",
    )

    original_message = await post_response(
        client=hctx.app.client,
        channel=SALESFORCE_CASE_CHANNEL,
        thread_ts=None,
        text=f"*Spam Detected* <{create_case_url(event.case.Id)}|{event.case.CaseNumber}> - _{event.case.Subject}_",
    )

    if not original_message:
        logfire.error("Failed to post message, aborting")
        return

    message_thread = original_message.data.get("ts")
    if not message_thread:
        raise Exception(
            "Could not create a thread for the customer-created Salesforce case"
        )

    message_to_link_to = SlackMessage(
        channel_id=SALESFORCE_CASE_CHANNEL,
        ts=message_thread,
        text=output.message,
        thread_ts=None,
    )

    if SALESFORCE_SLACK_THREAD_FIELD:
        result = await hctx.app.client.chat_getPermalink(
            channel=message_to_link_to.channel_id,
            message_ts=message_to_link_to.ts,
        )
        permalink = result.data.get("permalink")
        update_case(
            hctx.salesforce_client,
            event.case.Id,
            {SALESFORCE_SLACK_THREAD_FIELD: permalink},
        )

        logfire.info(
            "Updated Salesforce case to include the thread link",
            extra={"permalink": permalink},
        )

    add_internal_case_post(
        salesforce_client=hctx.salesforce_client,
        case_id=event.case.Id,
        body=output.short_description,
    )
    request_feedback(
        hctx.app.client,
        channel=message_to_link_to.channel_id,
        thread_ts=message_to_link_to.ts,
    )

    update_case(
        hctx.salesforce_client,
        event.case.Id,
        {"Status": "Spam", "Type": "Spam"},
    )


def _build_static_select_block(
    *,
    block_id: str,
    action_id: str,
    label: str,
    placeholder: str,
    options: list[dict[str, Any]],
    selected_value: str | None,
) -> dict[str, Any] | None:
    """Build a Slack Block Kit input block wrapping a static_select element.

    Returns ``None`` when ``options`` is empty. If ``selected_value`` matches
    one of the option values, that option is set as the element's
    ``initial_option``; otherwise the prefill is silently dropped.
    """
    if not options:
        return None

    initial_option = next(
        (opt for opt in options if opt["value"] == selected_value), None
    )
    return {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": {
            "type": "static_select",
            "action_id": action_id,
            "placeholder": {"type": "plain_text", "text": placeholder},
            "options": options,
            **({"initial_option": initial_option} if initial_option else {}),
        },
    }


def _build_cloud_impact_dropdown(
    cloud_impact_values: list[str], selected_value: str | None
) -> dict[str, Any] | None:
    """Build the Cloud Impact static_select block from Salesforce picklist values."""
    options = [
        {
            "text": {"type": "plain_text", "text": cloud_impact},
            "value": cloud_impact,
        }
        for cloud_impact in cloud_impact_values
    ]
    return _build_static_select_block(
        block_id=CUSTOMER_IMPACT_BLOCK_ID,
        action_id=CUSTOMER_IMPACT_ACTION_ID,
        label="Impact",
        placeholder="Selected value used to determine severity. If none given, defaults to medium severity.",
        options=options,
        selected_value=selected_value,
    )


def _build_new_case_form(
    *,
    subject: str | None,
    description: str | None,
    customer_impact_block: dict[str, Any] | None,
    service_block: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build the Block Kit blocks for the new Salesforce case ephemeral form."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*New Support Case*\nPlease fill out the details below.",
            },
        },
        {
            "type": "input",
            "block_id": SUBJECT_BLOCK_ID,
            "label": {"type": "plain_text", "text": "Title"},
            "element": {
                "type": "plain_text_input",
                "action_id": SUBJECT_ACTION_ID,
                "placeholder": {
                    "type": "plain_text",
                    "text": "Brief summary of the case",
                },
                "max_length": 200,
                **({"initial_value": subject[:200]} if subject else {}),
            },
        },
        {
            "type": "input",
            "block_id": DESCRIPTION_BLOCK_ID,
            "label": {"type": "plain_text", "text": "Description"},
            "element": {
                "type": "plain_text_input",
                "action_id": DESCRIPTION_ACTION_ID,
                "multiline": True,
                "placeholder": {
                    "type": "plain_text",
                    "text": "Detailed description of the issue",
                },
                **({"initial_value": description} if description else {}),
            },
        },
        *([customer_impact_block] if customer_impact_block else []),
        *([service_block] if service_block else []),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": NEW_SALESFORCE_CASE_WORKFLOW_FORM_SUBMIT,
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Submit"},
                },
                {
                    "type": "button",
                    "action_id": NEW_SALESFORCE_CASE_WORKFLOW_FORM_CANCEL,
                    "text": {"type": "plain_text", "text": "Cancel"},
                },
            ],
        },
    ]


def _build_service_dropdown(
    services: list[ServiceRecord] | None, selected_value: str | None
) -> dict[str, Any] | None:
    """Build the Service static_select block from an account's ServiceRecords.

    Project-only options are inserted at the top so the user can pick a project
    without drilling into a specific service; ``project_id|service_id`` options
    are appended below.
    """
    options: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    for s in services or []:
        if s.project_id and s.project_id not in seen_projects:
            seen_projects.add(s.project_id)
            options.insert(
                len(seen_projects) - 1,
                {
                    "text": {
                        "type": "plain_text",
                        "text": f"Project: {s.project_id}",
                    },
                    "value": s.project_id,
                },
            )
        options.append(
            {
                "text": {
                    "type": "plain_text",
                    "text": f"Project: {s.project_id}, Service: {s.service_id}",
                },
                "value": f"{s.project_id}|{s.service_id}",
            }
        )
    return _build_static_select_block(
        block_id=SERVICE_BLOCK_ID,
        action_id=SERVICE_ACTION_ID,
        label="Service",
        placeholder="Select a service",
        options=options,
        selected_value=selected_value,
    )


@logfire.instrument(
    "send_new_salesforce_case_workflow_form",
    extract_args=["channel", "user", "services"],
)
async def send_new_salesforce_case_workflow_form(
    slack_client: AsyncWebClient,
    salesforce_client: Salesforce | None,
    pool: AsyncConnectionPool,
    channel: str,
    user: str | None,
    subject: str | None = None,
    description: str | None = None,
    customer_impact: str | None = None,
    service: str | None = None,
):
    """Send an ephemeral message with a form to collect new Salesforce case details.

    Looks up the Salesforce account linked to ``channel``, pulls the account's
    services/projects to populate the project dropdown, and posts an ephemeral
    Block Kit form visible only to ``user``. Any of the form fields can be
    prefilled by passing the corresponding argument.

    Args:
        slack_client: Slack AsyncWebClient for API calls
        salesforce_client: Salesforce API client. If ``None``, the form is not
            sent and the function logs a warning and returns.
        pool: Database connection pool used to resolve the channel's linked
            Salesforce account id.
        channel: Slack channel ID to post the form in.
        user: Slack user ID to send the ephemeral message to.
        subject: Optional prefill for the Title input.
        description: Optional prefill for the Description input.
        customer_impact: Optional prefill for the Impact dropdown. Only applied
            when the value matches one of the picklist options.
        service: Optional prefill for the Service dropdown. Expected to be
            either a project id or a ``"<project_id>|<service_id>"`` string,
            and only applied when the value matches one of the built options.

    Raises:
        Exception: If ``user`` is falsy, or the channel is not linked to a
            Salesforce account.
    """

    if not salesforce_client:
        logfire.warn("Salesforce client not configured, skipping")
        return

    if not user:
        raise Exception("Cannot show case form: no user is associated with this event.")

    account_id = await get_salesforce_account_id_for_channel(
        pool=pool, channel_id=channel
    )
    if not account_id:
        raise Exception(
            "This Slack channel is not linked to a Salesforce account, "
            "so I cannot open a support case from here. "
            "Please contact an admin to link this channel to a Salesforce account."
        )

    services = get_services_for_account(
        salesforce_client=salesforce_client, account_id=account_id
    )

    service_block = _build_service_dropdown(services=services, selected_value=service)

    cloud_impact_values = get_pick_list_values(
        salesforce_client.Case, CLOUD_IMPACT_FIELD
    )

    customer_impact_block = _build_cloud_impact_dropdown(
        cloud_impact_values=cloud_impact_values, selected_value=customer_impact
    )

    blocks = _build_new_case_form(
        subject=subject,
        description=description,
        customer_impact_block=customer_impact_block,
        service_block=service_block,
    )

    await slack_client.chat_postEphemeral(
        channel=channel,
        user=user,
        text="New Support Case",
        blocks=blocks,
    )


def parse_new_case_form_data(
    body: dict[str, Any],
) -> NewSalesforceCaseFormSubmission | None:
    """Parse the new Salesforce case workflow form submission.

    Args:
        body: Full action body from Slack

    Returns:
        The submitted form data if valid; None if required fields are missing.
    """

    state_values = (body.get("state") or {}).get("values") or {}

    subject = (
        state_values.get(SUBJECT_BLOCK_ID, {}).get(SUBJECT_ACTION_ID, {}).get("value")
    )
    description = (
        state_values.get(DESCRIPTION_BLOCK_ID, {})
        .get(DESCRIPTION_ACTION_ID, {})
        .get("value")
    )
    customer_impact_value = (
        state_values.get(CUSTOMER_IMPACT_BLOCK_ID, {})
        .get(CUSTOMER_IMPACT_ACTION_ID, {})
        .get("selected_option", {})
        or {}
    ).get("value")
    service_value = (
        state_values.get(SERVICE_BLOCK_ID, {})
        .get(SERVICE_ACTION_ID, {})
        .get("selected_option", {})
        or {}
    ).get("value")

    if not subject or not description:
        logfire.error(
            "New Salesforce case form submission missing required fields",
            title=subject,
            description=description,
        )
        return None

    logfire.info(
        "New Salesforce case workflow form submitted",
        subject=subject,
    )

    return NewSalesforceCaseFormSubmission(
        subject=subject,
        description=description,
        service=service_value,
        customer_impact=customer_impact_value,
    )


async def handle_new_salesforce_case_workflow_form_cancel(
    ack: AsyncAck,
    respond: AsyncRespond,
):
    """Handle cancellation of the new Salesforce case workflow form.

    Dismisses the ephemeral form message without taking any action.

    Args:
        ack: Slack ack function
        respond: Slack respond function for deleting the ephemeral message
    """
    await ack()
    await respond(text="", replace_original=True, delete_original=True)
