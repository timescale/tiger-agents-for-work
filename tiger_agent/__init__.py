__version__ = "0.0.11"

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.types import AgentResponseContext, ExtraContextDict
from tiger_agent.prompts.types import PromptPackage
from tiger_agent.slack.types import SlackAppMentionEvent
from tiger_agent.tasks.harness import TaskHarness
from tiger_agent.tasks.types import Task, TaskProcessor
from tiger_agent.types import Context

# Backwards-compatible aliases
EventHarness = TaskHarness
Event = Task
HarnessContext = Context
TaskContext = Context
EventProcessor = TaskProcessor


__all__ = [
    "AgentResponseContext",
    "ExtraContextDict",
    "TigerAgent",
    "SlackAppMentionEvent",
    "PromptPackage",
    "__version__",
    # New names
    "TaskHarness",
    "Context",
    "Task",
    "TaskProcessor",
    # Backwards-compatible aliases
    "EventHarness",
    "HarnessContext",
    "TaskContext",
    "Event",
    "EventProcessor",
]
