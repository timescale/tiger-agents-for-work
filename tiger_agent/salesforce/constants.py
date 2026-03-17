import os

CASE_ID_FIELD = "Id"
CASE_OWNER_ID_FIELD = "OwnerId"
DEV_HELP_LINKS_FIELD = "Dev_Help_Links__c"

CASE_FIELDS = [
    CASE_ID_FIELD,
    "CaseNumber",
    CASE_OWNER_ID_FIELD,
    "ContactEmail",
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
