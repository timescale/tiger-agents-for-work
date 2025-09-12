from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic_ai import Agent
from pydantic_ai.toolsets.abstract import AbstractToolset

from app.utils.util import prune

AgentDepsT = TypeVar("AgentDepsT")

class FilteringAgent(Agent[AgentDepsT]):
    """Agent that filters out None items from toolsets."""
    
    def __init__(
        self,
        *args: Any,
        toolsets: Sequence[AbstractToolset[AgentDepsT]] | None = None,
        **kwargs: Any
    ) -> None:
        # Filter toolsets if provided
        if toolsets is not None:
            toolsets = prune(list(toolsets))
        
        super().__init__(*args, **kwargs, toolsets=toolsets)