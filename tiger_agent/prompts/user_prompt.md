{% if mention.type == "salesforce_event" %}

## Event Type

A new Salesforce support case has been received.

## Case Information

- Case ID: {{ mention.case.Id }}
- Case Number: {{ mention.case.CaseNumber }}
- Subject: {{ mention.case.Subject }}
- Status: {{ mention.case.Status }}
- Priority: {{ mention.case.Priority }}
- Severity: {{ mention.case.Severity__c }}
- Cloud Impact: {{ mention.case.Cloud_Impact__c }}
- Contact Email: {{ mention.case.ContactEmail }}
- Created Date: {{ mention.case.CreatedDate }}
- Created By ID: {{ mention.case.CreatedById }}
- Customer Thread: {{ mention.case.Customer_Slack_Thread__c }}

{% if mention.case.Owner %}

- Owner ID: {{ mention.case.Owner.Id }}
- Owner Username: {{ mention.case.Owner.Username }}
- Owner First Name: {{ mention.case.Owner.FirstName }}
- Owner Last Name: {{ mention.case.Owner.LastName }}
- Owner Email: {{ mention.case.Owner.Email }}

{% endif %}

{% if mention.case.Description %}

- Description: {{ mention.case.Description }}

{% endif %}

## Case Emails

Call `get_case_details` with `case_id_or_number` set to `{{ mention.case.Id }}` and `query_salesforce_directly` set to `true` to retrieve the full email thread. If an email has an attached or inline image, download that image for fuller context.

{% elif mention.type in ["app_mention", "message"] %}

## Event Type

A Slack user has sent you a prompt.

## User Info

{% if user %}
id: {{ user.id }}
username: {{ user.name }}
real_name: {{ user.real_name }}
local time zone: {{ user.tz }}
{% if local_time %}user's local time: {{ local_time }}{% endif %}
{% else %}
User info unavailable.
{% endif %}

## Message Details

channel: {{ mention.channel }}
ts: {{ mention.ts }}
event_ts: {{ task.event_ts }}

{% if thread_history %}

## Thread History

The following is the prior message history from the Slack thread that contains the message you are responding to, in chronological order.

{{ thread_history }}

{% endif %}

## Respond to this message

{{ mention.text }}

{% elif mention.type == "user_defined_rule_execution" %}

## Event Type

{% if mention.trigger == "schedule" %}
A scheduled rule is running. There is no user to reply to and no thread to read; do not ask questions. The system delivers your output.
{% else %}
A user-defined rule matched an incoming event. Carry out its action now; do not ask questions.
{% endif %}

## Rule

- Name: {{ mention.rule_name }}
- Owner: {{ mention.owner_slack_id }}
{% if mention.channel %}- Destination channel: {{ mention.channel }}{% endif %}

## Action

{{ rule.action_prompt }}

{% if mention.trigger == "schedule" %}

Window: the last {{ mention.window_hours }} hours, ending now ({{ task.event_ts }}).

## Output contract

Return two fields:

- `summary` — Slack mrkdwn, under about 1500 characters. State what was assessed (counts, distribution), then one line per finding. This is posted as the message.
- `report_markdown` — the full report. It is rendered as a Slack canvas, which supports headings, bold and italic, bullet and numbered lists, links, and code blocks. Do not use tables.

{% else %}

## Matched Event

```
{{ mention.matched_event | tojson(indent=2) }}
```

Match reason: {{ mention.match_reason }}
{% endif %}
{% endif %}
