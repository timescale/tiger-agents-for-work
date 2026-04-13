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
event_ts: {{ event.event_ts }}

{% if thread_history %}

## Thread History

The following is the prior message history from the Slack thread that contains the message you are responding to, in chronological order.

{{ thread_history }}

{% endif %}

## Respond to this message

{{ mention.text }}
{% endif %}
