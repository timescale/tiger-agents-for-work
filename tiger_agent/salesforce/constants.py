import os

CASE_ID_FIELD = "Id"
CASE_OWNER_ID_FIELD = "OwnerId"
DEV_HELP_LINKS_FIELD = "Dev_Help_Links__c"

CASE_FIELDS = [
    CASE_ID_FIELD,
    "CaseNumber",
    CASE_OWNER_ID_FIELD,
    "Owner.Id",
    "Owner.Username",
    "Owner.FirstName",
    "Owner.LastName",
    "Owner.Email",
    "Cloud_Impact__c",
    "ContactEmail",
    "Severity__c",
    "Subject",
    "Status",
    "Priority",
    "CreatedDate",
    "CreatedById",
]


SALESFORCE_DOMAIN = os.environ.get("SALESFORCE_DOMAIN", None)
SALESFORCE_CLIENT_ID = os.environ.get("SALESFORCE_CLIENT_ID", None)
SALESFORCE_CLIENT_SECRET = os.environ.get("SALESFORCE_CLIENT_SECRET", None)
SALESFORCE_CASE_CHANNEL = os.environ.get("SALESFORCE_CASE_CHANNEL", None)
SALESFORCE_ENABLE_SPAM_FILTERING = os.environ.get(
    "SALESFORCE_ENABLE_SPAM_FILTERING", None
)
SALESFORCE_SLACK_THREAD_FIELD = os.environ.get("SALESFORCE_SLACK_THREAD_FIELD", None)
SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD = os.environ.get(
    "SALESFORCE_SLACK_CUSTOMER_THREAD_FIELD", None
)
SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT = os.environ.get(
    "SALESFORCE_CASE_EMAIL_COMMENT_SUBJECT", "Re: Case"
)
SALESFORCE_CASE_SUPPORT_EMAIL = os.environ.get(
    "SALESFORCE_CASE_SUPPORT_EMAIL", "support@tigerdata.com"
)
SALESFORCE_INTERNAL_FROM_NAME_SUFFIX = os.environ.get(
    "SALESFORCE_INTERNAL_FROM_NAME_SUFFIX", "TigerData"
)
