__version__ = "0.0.11"

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.types import AgentResponseContext, ExtraContextDict
from tiger_agent.prompts.types import PromptPackage
from tiger_agent.slack.types import SlackAppMentionEvent
from tiger_agent.tasks.harness import TaskHarness
from tiger_agent.tasks.types import Task, TaskProcessor
from tiger_agent.types import HarnessContext

# Backwards-compatible aliases
EventHarness = TaskHarness
Event = Task
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
    "HarnessContext",
    "Task",
    "TaskProcessor",
    # Backwards-compatible aliases
    "EventHarness",
    "Event",
    "EventProcessor",
]
