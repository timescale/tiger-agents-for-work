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
{% if mention.thread_ts %}thread_ts: {{ mention.thread_ts }}{% endif %}
event_ts: {{ event.event_ts }}

## Respond to this message

{{ mention.text }}
{% endif %}
