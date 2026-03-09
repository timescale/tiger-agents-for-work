import os

CASE_ID_FIELD = "Id"
CASE_OWNER_ID_FIELD = "OwnerId"
DEV_HELP_LINKS_FIELD = "Dev_Help_Links__c"

CASE_FIELDS = [
    CASE_ID_FIELD,
    "CaseNumber",
    CASE_OWNER_ID_FIELD,
    "Subject",
    "Status",
    "Priority",
    "CreatedDate",
    "CreatedById",
]


SALESFORCE_DOMAIN = os.environ["SALESFORCE_DOMAIN"]
SALESFORCE_CLIENT_ID = os.environ["SALESFORCE_CLIENT_ID"]
SALESFORCE_CLIENT_SECRET = os.environ["SALESFORCE_CLIENT_SECRET"]
