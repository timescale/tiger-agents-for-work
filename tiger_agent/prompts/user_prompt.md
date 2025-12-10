## Message Details

channel: {{ mention.channel }}
ts: {{ mention.ts }}
{% if mention.thread_ts %}thread_ts: {{ mention.thread_ts }}{% endif %}
event_ts: {{ event.event_ts }}

## Respond to this message

{{ mention.text }}