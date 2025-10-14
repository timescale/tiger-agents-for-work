{% if user %}
**User Info:**
id: {{ user.id }}
username: {{ user.name }}
real_name: {{ user.real_name }}
local time zone: {{ user.tz }}
{% if local_time %}user's local time: {{ local_time }}{% endif %}
{% endif %}

**Message Details:**
channel: {{ event.channel }}
ts: {{ event.ts }}
{% if event.thread_ts %}thread_ts: {{ event.thread_ts }}{% endif %}
event_ts: {{ event.event_ts }}

**Respond to this message:**
{{ event.text }}
