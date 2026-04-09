import os

CONFIRM_PROACTIVE_PROMPT = "confirm_proactive_prompt"
REJECT_PROACTIVE_PROMPT = "reject_proactive_prompt"
AGENT_FEEDBACK_RATING = "agent_feedback_rating"

CREATE_SUPPORT_CASE_COMMAND = os.environ.get(
    "SLACK_CREATE_SALESFORCE_CASE_COMMAND", None
)

NEW_SALESFORCE_CASE_WORKFLOW_FORM_SUBMIT = "new_salesforce_case_workflow_form_submit"
NEW_SALESFORCE_CASE_WORKFLOW_FORM_CANCEL = "new_salesforce_case_workflow_form_cancel"
