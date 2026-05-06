import os

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL = os.environ.get(
    "AGENT_FEEDBACK_RECEIVED_SLACK_CHANNEL", None
)

CONFIRM_PROACTIVE_PROMPT = "confirm_proactive_prompt"
REJECT_PROACTIVE_PROMPT = "reject_proactive_prompt"

NEW_SALESFORCE_CASE_WORKFLOW_FORM_SUBMIT = "new_salesforce_case_workflow_form_submit"
NEW_SALESFORCE_CASE_WORKFLOW_FORM_CANCEL = "new_salesforce_case_workflow_form_cancel"
NEW_SALESFORCE_CASE_WORKFLOW_FORM_TRIGGER = "new_salesforce_case_workflow_form_trigger"

FEEDBACK_FORM_TRIGGER = "feedback_form_trigger"
FEEDBACK_FORM_SUBMIT = "feedback_form_submit"
