{% if mention.type == "salesforce_event" %}

A new Salesforce support case has been received. Use the `salesforce-new-case-notification` skill to gather context and return the structured notification.

## Case

case_id: {{ mention.case.Id }}
case_number: {{ mention.case.CaseNumber }}
subject: {{ mention.case.Subject }}
status: {{ mention.case.Status }}
priority: {{ mention.case.Priority }}
created_date: {{ mention.case.CreatedDate }}
{% if mention.case.Description %}description: {{ mention.case.Description }}{% endif %}
{% else %}

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
