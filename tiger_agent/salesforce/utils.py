import asyncio
import base64
import os
from collections.abc import Callable, Coroutine
from typing import Any

import logfire
from aiosfstream_ng.client import Client
from simple_salesforce.api import Salesforce

from tiger_agent.salesforce.clients import (
    ClientCredentialsAuthenticator,
)
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
    SALESFORCE_DOMAIN,
)
from tiger_agent.salesforce.types import (
    CaseData,
    EmailAttachment,
    SalesforceFeedItem,
    ServiceRecord,
)

RECONNECT_DELAY_SECONDS = 30
IGNORED_CONTACT_EMAILS = set(
    os.environ.get("SALESFORCE_IGNORE_CONTACT_EMAILS", "")
    .lower()
    .replace(" ", "")
    .split(",")
)

logfire.info("Salesforce ignore list", extra={"list": IGNORED_CONTACT_EMAILS})


async def subscribe_to_topic(
    salesforce_client: Salesforce,
    topic_name: str,
    handler: Callable[[CaseData], Coroutine[Any, Any, None]],
):
    channel = f"/topic/{topic_name}"
    while True:
        try:
            async with Client(ClientCredentialsAuthenticator()) as streaming_client:
                await streaming_client.subscribe(channel)
                logfire.info(
                    "Subscribed to PushTopic ", extra={"topic_name": topic_name}
                )

                async for message in streaming_client:
                    data = message.get("data", {})
                    sobject = data.get("sobject", {})

                    case_id = sobject.get("Id")
                    if not case_id:
                        continue

                    try:
                        fields = ", ".join(CASE_FIELDS)
                        result = salesforce_client.query(
                            f"SELECT {fields} FROM Case WHERE Id = '{case_id}' LIMIT 1"
                        )
                        if not result["records"]:
                            logfire.warning(
                                "Case not found after CDC event", extra={"id": case_id}
                            )
                            continue
                        case = CaseData(**result["records"][0])

                        logfire.info(
                            "Handling Salesforce event",
                            extra={"topic": topic_name, "payload": data},
                        )
                        await handler(case)
                    except Exception:
                        logfire.exception("Error handling new case", case_id=case_id)

            logfire.warning(
                "Streaming client exited unexpectedly, reconnecting",
                extra={"topic_name": topic_name, "delay": RECONNECT_DELAY_SECONDS},
            )
        except Exception:
            logfire.exception(
                "Streaming connection error, reconnecting",
                extra={"topic_name": topic_name, "delay": RECONNECT_DELAY_SECONDS},
            )

        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


async def subscribe_to_case_comment_topic(
    topic_name: str,
    handler: Callable[[str, str], Coroutine[Any, Any, None]],
):
    """Subscribe to a PushTopic for new CaseComment events.

    CaseComment is the underlying SObject created when someone posts a
    Chatter comment on a case. FeedItem and CDC are not supported for
    Chatter posts, so this is the recommended streaming approach.

    Calls handler(case_id, comment_id) for each new comment.
    """
    channel = f"/topic/{topic_name}"
    while True:
        try:
            async with Client(ClientCredentialsAuthenticator()) as streaming_client:
                await streaming_client.subscribe(channel)
                logfire.info(
                    "Subscribed to CaseComment PushTopic",
                    extra={"topic_name": topic_name},
                )

                async for message in streaming_client:
                    data = message.get("data", {})
                    sobject = data.get("sobject", {})

                    comment_id = sobject.get("Id")
                    case_id = sobject.get("ParentId")
                    if not comment_id or not case_id:
                        continue

                    try:
                        logfire.info(
                            "Handling CaseComment event",
                            extra={"topic": topic_name, "payload": data},
                        )
                        await handler(case_id, comment_id)
                    except Exception:
                        logfire.exception(
                            "Error handling new case comment",
                            comment_id=comment_id,
                            case_id=case_id,
                        )

            logfire.warning(
                "CaseComment streaming client exited unexpectedly, reconnecting",
                extra={"topic_name": topic_name, "delay": RECONNECT_DELAY_SECONDS},
            )
        except Exception:
            logfire.exception(
                "CaseComment streaming connection error, reconnecting",
                extra={"topic_name": topic_name, "delay": RECONNECT_DELAY_SECONDS},
            )

        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


def should_ignore_new_case(case: CaseData) -> bool:
    if not case.ContactEmail:
        return False
    return case.ContactEmail in IGNORED_CONTACT_EMAILS


def create_case_url(case: CaseData) -> str:
    return f"https://{SALESFORCE_DOMAIN}/lightning/r/Case/{case.Id}/view"


def create_case(
    salesforce_client: Salesforce,
    subject: str,
    description: str,
    severity: str,
    account_id: str,
    project_id: str | None = None,
    service_id: str | None = None,
    origin: str | None = None,
) -> CaseData:
    payload = {
        "Subject": subject,
        "Description": description,
        "Severity__c": severity,
        "AccountId": account_id,
    }
    if project_id:
        payload["Cloud_Project_ID__c"] = project_id
    if service_id:
        payload["Cloud_Service_ID__c"] = service_id
    if origin:
        payload["Origin"] = origin
    result = salesforce_client.Case.create(payload)
    if not result["success"] or not result["id"]:
        logfire.error("Could not create a new salesforce case")
        return
    case = salesforce_client.Case.get(result["id"])
    return CaseData(**case)


def get_services_for_account(
    salesforce_client: Salesforce, account_id: str
) -> list[ServiceRecord] | None:
    result = salesforce_client.query(
        f"SELECT Name, Project_Id__c FROM Service__c WHERE Account__c = '{account_id}'"
    )
    records = result.get("records", [])
    if not records:
        return None
    return [
        ServiceRecord(service_id=r["Name"], project_id=r.get("Project_Id__c"))
        for r in records
        if r.get("Name")
    ]


def add_case_email_comment(
    salesforce_client: Salesforce,
    case_id: str,
    body: str,
    from_address: str,
    to_address: str | None,
    subject: str,
    from_name: str | None = None,
    incoming: bool = True,
    html_body: str | None = None,
    attachments: list[EmailAttachment] | None = None,
) -> None:
    payload = {
        "ParentId": case_id,
        "FromAddress": from_address,
        "FromName": from_name or from_address,
        "ToAddress": to_address,
        "Subject": subject,
        "TextBody": body,
        "HtmlBody": html_body,
        "Incoming": incoming,
        "Status": "0",  # 0 = "New"
    }
    result = salesforce_client.EmailMessage.create(payload)
    if not result["success"] or not result["id"]:
        logfire.error(
            "Could not add email comment to Salesforce case", extra={"case_id": case_id}
        )
        return

    email_message_id = result["id"]
    for attachment in attachments or []:
        encoded = base64.b64encode(attachment.body).decode("utf-8")
        att_result = salesforce_client.Attachment.create(
            {
                "ParentId": email_message_id,
                "Name": attachment.name,
                "ContentType": attachment.content_type,
                "Body": encoded,
            }
        )
        if not att_result["success"] or not att_result["id"]:
            logfire.error(
                "Could not attach file to Salesforce email message",
                extra={"email_message_id": email_message_id, "name": attachment.name},
            )


def get_recent_case_feed_items(
    salesforce_client: Salesforce,
    created_after: str | None = None,
    types: list[str] | None = None,
    public_only: bool = False,
) -> list[SalesforceFeedItem]:
    try:
        conditions = ["Parent.Type = 'Case'"]
        if created_after is not None:
            conditions.append(f"CreatedDate > {created_after}")
        if types:
            type_list = ", ".join(f"'{t}'" for t in types)
            conditions.append(f"Type IN ({type_list})")
        if public_only:
            conditions.append("Visibility = 'AllUsers'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        result = salesforce_client.query(
            f"SELECT Id, ParentId, Body, Type, CreatedDate, CreatedById,"
            f" CreatedBy.Name, CreatedBy.Email, Visibility"
            f" FROM FeedItem{where}"
            f" ORDER BY CreatedDate DESC"
        )
        return [SalesforceFeedItem(**r) for r in result.get("records", [])]
    except Exception:
        logfire.exception("Failed to fetch recent feed items")
        return []


def get_project_ids_for_account(
    salesforce_client: Salesforce, account_id: str
) -> list[str] | None:
    result = salesforce_client.query(
        f"SELECT Project_Id__c FROM Project__c WHERE Account__c = '{account_id}'"
    )
    records = result.get("records", [])
    if not records:
        return None
    return [r["Project_Id__c"] for r in records if r.get("Project_Id__c")]
