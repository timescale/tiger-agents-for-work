

{% if user %}
**User Info**:
id: {{ user.id }}
username: {{ user.name }}
real_name: {{ user.real_name }}
local time zone: {{ user.tz }}
{% if local_time %}user's local time: {{ local_time }}{% endif %}
{% endif %}

**Message**:
channel: {{ mention.channel }}
ts: {{ mention.ts }}
{% if not local_time %}message time: {{ event.event_ts }}{% endif %}
{% if mention.thread_ts %}thread_ts: {{ mention.thread_ts }}{% endif %}

Respond to this message: {{ mention.text }}