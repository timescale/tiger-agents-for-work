# Prompt Templates

Tiger Agent uses Jinja2 templates for dynamic, context-aware prompt generation. This system allows for sophisticated prompts that adapt to conversation context, user preferences, and event metadata.

## Overview

The prompt system uses two required templates:

- **`system_prompt.md`**: Defines the AI's role, capabilities, and behavior patterns
- **`user_prompt.md`**: Formats the user's request with relevant context

Templates have access to a rich context object containing event details, user information, bot capabilities, and temporal data.

## How templates are discovered

Templates are discovered by regex, not by exact filename. Any `.md` file whose name matches the corresponding regex is loaded, rendered, and passed to the agent as a **sequence of prompts**. This means you can split a prompt across multiple files, and downstream packages can *supplement* (not just override) the base prompts.

| Regex | Matches | Consumer |
| --- | --- | --- |
| `^(system_prompt\|shared_system_prompt).*\.md$` | `system_prompt.md`, `system_prompt_identity.md`, `shared_system_prompt.tigerlabs.md`, … | Main agent's system prompt |
| `^user_prompt.*\.md$` | `user_prompt.md`, `user_prompt_extra.md`, … | Main agent's user prompt |
| `^(investigator_system_prompt\|shared_system_prompt).*\.md$` | `investigator_system_prompt.md`, `shared_system_prompt.tigerlabs.md`, … | Investigator sub-agent's system prompt |

Matched templates are rendered in order of **shortest filename first**, then alphabetically. So `system_prompt.md` renders before `system_prompt_identity.md`, which renders before `shared_system_prompt.tigerlabs.md`. Design your filenames with this ordering in mind if the sequence matters.

### `shared_system_prompt.<tag>.md` — cross-agent prompts

Any file named `shared_system_prompt.<tag>.md` (e.g. `shared_system_prompt.tigerlabs.md`) is loaded by **both** the main agent and the investigator sub-agent. Use this pattern for guidance that applies to every agent in the system — for example, "how to discover tools available through a shared MCP proxy" — so you only maintain one file. Prompts that should apply to only one agent stay in their agent-specific file (`system_prompt*.md` or `investigator_system_prompt*.md`).

### `investigator_system_prompt*.md` — the investigator sub-agent

Tiger Agent exposes an `investigator` sub-agent for delegating focused investigations that would otherwise clutter the main agent's context (e.g. iterative log searches, PromQL queries, multi-step lookups). Files matching `^investigator_system_prompt.*\.md$` are loaded into that sub-agent's system prompt and rendered with the same context object as the main agent's prompts.

## Template Files

### `system_prompt.md`

The system prompt defines the AI's role, capabilities, and behavior patterns. This template sets the foundation for how your Tiger Agent will respond and interact.

```markdown
# Tiger Agent System Prompt

You are Tiger Agent, an AI assistant integrated into Slack via {{bot.name}}.

## Your Capabilities

- Access to real-time information through connected tools
- Ability to help with {{user.real_name}}'s requests in {{bot.team}}
- Context-aware responses based on user timezone ({{user.tz_label}})

## Available Tools

{% if mention.text contains "help" %}
You can help with documentation, code analysis, project management, and more.
{% endif %}

## Guidelines

- Be helpful and concise
- Use threaded replies when appropriate
- Reference user by name: {{user.real_name or user.name}}
- Consider local time: {{local_time.strftime('%I:%M %p %Z')}}
```

### `user_prompt.md`

The user prompt formats the user's request with relevant context, providing the AI with all necessary information to generate an appropriate response.

```markdown
# Request from {{user.real_name or user.name}}

**Message:** {{mention.text}}

**Context:**

- Channel: {{mention.channel}}
- Time: {{local_time.strftime('%Y-%m-%d %I:%M %p %Z')}}
  {% if mention.thread_ts %}
- Thread: This is part of an ongoing conversation
  {% endif %}

**User Profile:**

- Timezone: {{user.tz_label}}
- Team: {{bot.team}}

Please respond appropriately to this request.
```

## Template Context Variables

Templates have access to a comprehensive context object with the following variables:

| Variable     | Description                                    |
| ------------ | ---------------------------------------------- |
| `task`       | Complete Task object with processing metadata  |
| `mention`    | AppMentionEvent with Slack message details     |
| `bot`        | Bot information (name, team, capabilities)     |
| `user`       | User profile (real_name, timezone, etc.)       |
| `local_time` | Event timestamp in user's timezone             |

### Detailed Variable Reference

#### `task`

Task (see [tiger_agent/tasks/types.py](/tiger_agent/tasks/types.py)):

- Processing metadata
- Event timing information
- Retry attempt counts
- Event status

#### `mention`

AppMentionEvent (see [tiger_agent/slack/types.py](/tiger_agent/slack/types.py)):

- `text`: The message content
- `channel`: Channel ID where the message was sent
- `user`: User ID who sent the message
- `ts`: Message timestamp
- `thread_ts`: Thread timestamp (if in a thread)

#### `bot`

BotInfo (see [tiger_agent/slack.py](/tiger_agent/slack.py)):

- `name`: Bot display name
- `team`: Team/workspace name
- Bot capabilities and configuration

#### `user`

UseInfo (see [tiger_agent/slack.py](/tiger_agent/slack.py)):

- `real_name`: User's display name
- `name`: User's username
- `tz_label`: User's timezone label
- Additional profile information

#### `local_time`

Event timestamp converted to the user's local timezone as a Python [datetime](https://docs.python.org/3/library/datetime.html#datetime-objects) object, supporting:

- `strftime()` formatting
- Timezone-aware operations
- Temporal calculations

## Configuration

### Template Loading

Templates are loaded from the filesystem using Jinja2:

```python
from pathlib import Path
from tiger_agent.prompts.types import PromptPackage

# Using a directory path
agent = TigerAgent(
    jinja_env=Path("/path/to/templates"),  # Template directory
    model="claude-3-5-sonnet-latest"
)

# Using a custom Jinja2 environment
jinja_env = Environment(
    enable_async=True,
    loader=FileSystemLoader("templates"),
    trim_blocks=True,
    lstrip_blocks=True
)

agent = TigerAgent(
    jinja_env=jinja_env,
    model="claude-3-5-sonnet-latest"
)

# Using multiple paths and packages
agent = TigerAgent(
    prompt_config=["~/prompts", "../test_prompts", PromptPackage(package_name="app", package_path="prompts")]
)
```

### CLI Configuration

When using the Tiger Agent CLI, templates are loaded from the `--prompts` directory:

```bash
# Use templates from a specific directory
tiger-agent run --prompts ./my-templates

# Use templates from the default ./prompts directory
tiger-agent run
```

## Template Customization Examples

### Conditional Content

Use Jinja2 conditionals to adapt templates based on context:

```markdown
# System Prompt with Conditional Tools

You are Tiger Agent, specialized for {{bot.team}}.

## Available Capabilities

{% if mention.channel == "general" %}

- General help and information
- Basic task assistance
  {% elif mention.channel == "engineering" %}
- Code review and debugging
- Technical documentation
- Architecture discussions
  {% else %}
- Context-aware assistance
  {% endif %}

{% if mention.thread_ts %}
Continue the conversation from the thread above.
{% else %}
This is a new conversation.
{% endif %}
```

### User Personalization

Personalize responses based on user information:

```markdown
# Personalized User Prompt

Hello {{user.real_name or user.name}}!

You mentioned: "{{mention.text}}"

Context for your request:

- Your timezone: {{user.tz_label}}
- Current time for you: {{local_time.strftime('%I:%M %p on %A, %B %d')}}
- Channel: {{mention.channel}}
  {% if bot.team %}
- Team: {{bot.team}}
  {% endif %}
```

### Time-Aware Templates

Use temporal context for time-sensitive responses:

```markdown
# Time-Aware System Prompt

You are Tiger Agent responding at {{local_time.strftime('%I:%M %p %Z')}}.

## Time-Based Behavior

{% set hour = local_time.hour %}
{% if hour < 9 %}

- It's early morning - be extra helpful with getting the day started
  {% elif hour > 17 %}
- It's evening - consider work-life balance in suggestions
  {% else %}
- Normal business hours - full assistance available
  {% endif %}

## Timezone Awareness

User is in {{user.tz_label}}, so tailor scheduling suggestions accordingly.
```

### Dynamic Tool References

Reference available tools dynamically:

```markdown
# System Prompt with Tool Integration

You have access to the following tools:
{% if mcp_tools %}
{% for tool_name in mcp_tools %}

- {{tool_name}}: Available for specialized tasks
  {% endfor %}
  {% else %}
- Basic conversation capabilities
  {% endif %}

Use tools when appropriate to provide comprehensive assistance.
```
