__version__ = "0.1.0"

from tiger_agent.agent.tiger_agent import TigerAgent
from tiger_agent.agent.types import AgentResponseContext, ExtraContextDict
from tiger_agent.app import TigerApp
from tiger_agent.prompts.types import PromptPackage
from tiger_agent.slack.types import SlackAppMentionEvent
from tiger_agent.tasks.harness import TaskHarness
from tiger_agent.tasks.handlers import TaskHandler, TaskProcessor
from tiger_agent.tasks.types import Task
from tiger_agent.types import HarnessContext
from tiger_agent.utils import get_harness_ctx

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
    "TigerApp",
    "TaskHarness",
    "TaskHandler",
    "TaskProcessor",
    "HarnessContext",
    "Task",
    # Backwards-compatible aliases
    "EventHarness",
    "Event",
    "EventProcessor",
    "get_harness_ctx",
]
