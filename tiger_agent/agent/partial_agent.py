from __future__ import annotations

from typing import Any

from pydantic_ai.agent import WrapperAgent

from tiger_agent.agent.limits import FINALIZE_PROMPT, run_and_return_partial


class PartialAnswerAgent(WrapperAgent[Any, Any]):
    """Make a budget-exhausted run end in a finalized answer instead of an exception.

    The sub-agent harness discards a child's whole context when the child raises
    ``UsageLimitExceeded`` and hands the parent a one-line steering message.
    Routing the child's ``run`` through ``run_and_return_partial`` gives it the
    same finalize pass the parent has, so what it gathered reaches the parent.
    """

    def __init__(self, wrapped, *, finalize_prompt: str = FINALIZE_PROMPT):
        super().__init__(wrapped)
        self.finalize_prompt = finalize_prompt

    async def run(self, user_prompt=None, *, deps=None, usage_limits=None, **kwargs):
        return await run_and_return_partial(
            self.wrapped,
            user_prompt=user_prompt,
            deps=deps,
            usage_limits=usage_limits,
            finalize_prompt=self.finalize_prompt,
            **kwargs,
        )
