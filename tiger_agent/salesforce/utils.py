import asyncio
import base64
import os
from collections.abc import Callable, Coroutine
from typing import Any

import logfire
from aiosfstream_ng.client import Client
from pydantic_ai import BinaryContent
from simple_salesforce.api import Salesforce
from slack_sdk.web.async_client import (
    AsyncWebClient,
)

from tiger_agent.salesforce.clients import (
    ClientCredentialsAuthenticator,
)
from tiger_agent.salesforce.constants import (
    CASE_FIELDS,
    SALESFORCE_DOMAIN,
)
from tiger_agent.salesforce.types import (
    CaseData,
    ContentVersion,
    EmailAttachment,
    FileAttachment,
    SalesforceFeedItem,
    ServiceRecord,
)
from tiger_agent.slack.types import SlackBaseEvent, SlackFile
from tiger_agent.slack.utils import download_private_file

RECONNECT_DELAY_SECONDS = 30
IGNORED_CONTACT_EMAILS = set(
    os.environ.get("SALESFORCE_IGNORE_CONTACT_EMAILS", "")
    .lower()
    .replace(" ", "")
    .split(",")
)

EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

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


_INLINE_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _build_inline_html_body(
    text_body: str,
    uploaded: list[tuple[str, str, str]],  # (version_id, content_type, filename)
) -> str:
    """Build an HtmlBody that embeds uploaded attachments inline.

    Images render as <img> via the Salesforce Shepherd download URL so that
    screenshots appear in-place in the case timeline. Other file types render
    as a download link.
    """
    import html

    parts = ["<div>"]
    for line in text_body.splitlines():
        parts.append(f"<p>{html.escape(line)}</p>")
    for version_id, content_type, filename in uploaded:
        url = f"/sfc/servlet.shepherd/version/download/{version_id}"
        name = html.escape(filename)
        if content_type in _INLINE_IMAGE_MIME_TYPES:
            parts.append(
                f'<p><img src="{url}" alt="{name}" style="max-width:100%;height:auto" /></p>'
            )
        else:
            parts.append(f'<p><a href="{url}">{name}</a></p>')
    parts.append("</div>")
    return "".join(parts)


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
    import os

    has_attachments = bool(attachments)
    payload = {
        "ParentId": case_id,
        "FromAddress": from_address,
        "FromName": from_name or from_address,
        "ToAddress": to_address,
        "Subject": subject,
        "TextBody": body,
        "HtmlBody": html_body,
        "Incoming": incoming,
        # Keep in Draft (5) while uploading attachments; finalize to "0" (New) after.
        "Status": "5" if has_attachments else "0",
    }
    # Delete any existing draft EmailMessages on the case before creating a new one,
    # as draft messages block creation with INVALID_OPERATION.
    if has_attachments:
        drafts = salesforce_client.query(
            f"SELECT Id FROM EmailMessage WHERE ParentId = '{case_id}' AND Status = '5'"
        )
        for draft in drafts.get("records", []):
            salesforce_client.EmailMessage.delete(draft["Id"])

    result = salesforce_client.EmailMessage.create(payload)
    if not result["success"] or not result["id"]:
        logfire.error(
            "Could not add email comment to Salesforce case", extra={"case_id": case_id}
        )
        return

    email_message_id = result["id"]

    # Track successfully uploaded versions for inline HTML body construction:
    # (version_id, content_type, filename)
    uploaded: list[tuple[str, str, str]] = []

    for attachment in attachments or []:
        encoded = base64.b64encode(attachment.body).decode("utf-8")

        # Strip extension from Title to avoid doubled extensions (e.g. "foo.png.png").
        # Salesforce stores the extension separately in FileExtension.
        name_without_ext = os.path.splitext(attachment.name)[0] or attachment.name

        # Upload file as ContentVersion
        cv_result = salesforce_client.ContentVersion.create(
            {
                "Title": name_without_ext,
                "PathOnClient": attachment.name,
                "VersionData": encoded,
                "IsMajorVersion": False,
            }
        )
        if not cv_result["success"] or not cv_result["id"]:
            logfire.error(
                "Could not upload attachment as ContentVersion",
                extra={"email_message_id": email_message_id, "name": attachment.name},
            )
            continue

        version_id = cv_result["id"]

        # Retrieve the ContentDocumentId for the newly created ContentVersion
        cv_record = salesforce_client.query(
            f"SELECT ContentDocumentId FROM ContentVersion WHERE Id = '{version_id}'"
        )
        records = cv_record.get("records", [])
        if not records:
            logfire.error(
                "Could not retrieve ContentDocumentId for ContentVersion",
                extra={"content_version_id": version_id},
            )
            continue

        content_document_id = records[0]["ContentDocumentId"]

        # Link the ContentDocument to the EmailMessage
        link_result = salesforce_client.ContentDocumentLink.create(
            {
                "ContentDocumentId": content_document_id,
                "LinkedEntityId": email_message_id,
                "ShareType": "V",
                "Visibility": "AllUsers",
            }
        )
        if not link_result["success"] or not link_result["id"]:
            logfire.error(
                "Could not link ContentDocument to EmailMessage",
                extra={
                    "email_message_id": email_message_id,
                    "content_document_id": content_document_id,
                },
            )
            continue

        uploaded.append((version_id, attachment.content_type or "", attachment.name))

    # Finalize: set Status to "0" (New) and write the inline HTML body so
    # images appear in-place in the Salesforce case timeline.
    if has_attachments:
        inline_html = _build_inline_html_body(body, uploaded)
        salesforce_client.EmailMessage.update(
            email_message_id, {"Status": "0", "HtmlBody": inline_html}
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


def get_feed_attachment_ids(
    salesforce_client: Salesforce,
    feed_item_id: str,
) -> list[str]:
    """Return the Ids of all FeedAttachments for a given FeedItem."""
    result = salesforce_client.query(
        f"SELECT Id FROM FeedAttachment WHERE FeedEntityId = '{feed_item_id}'"
    )
    return [r["Id"] for r in result.get("records", [])]


@logfire.instrument("download_feed_attachment {feed_attachment_id=}")
def download_feed_attachment(
    salesforce_client: Salesforce,
    feed_attachment_id: str,
) -> FileAttachment | None:
    """Download the body of a Salesforce FeedAttachment by its Id.

    Handles three attachment types:
    - Content/File: fetched via ContentVersion.VersionData, falling back to
      ContentDocument.Body if no ContentVersion exists.
    - Attachment: fetched via the legacy Attachment.Body REST endpoint.
    - Link: no body to download, returns None.
    """
    try:
        attachment = salesforce_client.FeedAttachment.get(feed_attachment_id)
        record_id = attachment.get("RecordId")
        attachment_type = attachment.get("Type", "")

        if attachment_type == "Link":
            return None

        if not record_id:
            raise ValueError(f"FeedAttachment {feed_attachment_id} has no RecordId")
        content_version: ContentVersion | None = None
        match attachment_type:
            case "Content":
                # Content: RecordId is a ContentVersion.Id directly
                content_version = ContentVersion.model_validate(
                    salesforce_client.ContentVersion.get(record_id)
                )

            case "InlineImage":
                # InlineImage: RecordId is a ContentDocument.Id
                version = salesforce_client.query(
                    f"SELECT Id, Title, FileExtension, VersionData, ContentDocumentId"
                    f" FROM ContentVersion"
                    f" WHERE ContentDocumentId = '{record_id}' AND IsLatest = true"
                    f" LIMIT 1"
                )
                records = version.get("records", [])
                if not records:
                    raise ValueError(
                        f"No ContentVersion found for ContentDocument {record_id}"
                    )
                content_version = ContentVersion.model_validate(records[0])

            case _:
                logfire.warn(
                    "Unexpected attachment type, ignoring",
                    attachment_type=attachment_type,
                )
                return None

        url = f"https://{salesforce_client.sf_instance}{content_version.VersionData}"
        name = content_version.Title or feed_attachment_id
        extension = content_version.FileExtension
        if extension:
            name = f"{name}.{extension}"
        content_type = EXT_TO_MIME.get(
            (extension or "").lower(), "application/octet-stream"
        )
        response = salesforce_client.session.get(
            url,
            headers={
                "Authorization": f"Bearer {salesforce_client.session_id}",
                "Accept": "*/*",
            },
        )
        response.raise_for_status()

        return FileAttachment(
            name=name,
            body=response.content,
            content_type=content_type,
        )
    except Exception:
        logfire.exception(
            "Failed to download feed attachment, skipping",
            feed_attachment_id=feed_attachment_id,
        )
        return None


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


async def build_email_attachments_from_slack_files(
    client: AsyncWebClient,
    event: SlackBaseEvent,
) -> list[EmailAttachment]:
    attachments: list[EmailAttachment] = []
    for file in event.files or []:
        if not file.id:
            logfire.warn("Skipping file with no id", file_name=file.name)
            continue
        info_result = await client.files_info(file=file.id)
        file = SlackFile.model_validate(info_result.data.get("file", {}))

        if not file.url_private_download:
            logfire.warn(
                "Skipping file with no url_private_download",
                file_id=file.id,
                name=file.name,
            )
            continue
        file_content = await download_private_file(
            url_private_download=file.url_private_download,
            mimetype=file.mimetype,
        )
        if not file_content:
            logfire.info(
                "Could not download file",
                file_name=file.name,
                url=file.url_private_download,
            )
            continue

        attachments.append(
            EmailAttachment(
                name=file.name,
                body=file_content.data
                if isinstance(file_content, BinaryContent)
                else file_content.encode("utf-8"),
                content_type=file.mimetype,
            )
        )
    return attachments
