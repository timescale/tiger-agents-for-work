"""Logging configuration for Tiger Agent using Logfire integration.

This module provides centralized logging setup that integrates with Pydantic Logfire
for comprehensive observability. It configures:

- **Logfire Integration**: When LOGFIRE_TOKEN is available, routes all logs through Logfire
- **Instrumentation**: Automatically instruments key libraries (psycopg, pydantic-ai, MCP, httpx)
- **System Metrics**: Collects process-level metrics for performance monitoring
- **Fallback Logging**: Uses standard console logging when Logfire is unavailable

The logging setup is designed to work both in development (without Logfire) and
production (with full Logfire observability) environments.
"""

import json
import logging
import os
from logging.config import dictConfig
from typing import Any

import logfire
from psycopg.types.json import Jsonb
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.mcp import (
    CallToolFunc,
    ProcessToolCallback,
)

from tiger_agent import __version__
from tiger_agent.agent.types import AgentResponseContext
from tiger_agent.mcp.types import MCPDict

MAX_TOOL_RESULT_CHARS = 200_000

# How many times one exact (tool, arguments) pair may run in a single agent
# run before further attempts are refused. Two is a legitimate retry; a
# third is a loop.
MAX_IDENTICAL_TOOL_CALLS = 2


def setup_logging(service_name: str = "tiger-agent") -> None:
    """Configure comprehensive logging with Logfire integration.

    Sets up logging configuration that adapts based on environment:

    **With LOGFIRE_TOKEN**:
    - Configures Logfire with service identity and version
    - Instruments key libraries for automatic tracing:
      - psycopg: Database query tracing
      - pydantic-ai: AI model interaction tracing
      - MCP: Model Context Protocol server communication
      - httpx: HTTP client request tracing
    - Collects system metrics (CPU, memory, threads)
    - Routes all standard library logs through Logfire
    - Suppresses noisy third-party loggers

    **Without LOGFIRE_TOKEN**:
    - Falls back to console logging with timestamp formatting
    - Maintains INFO level logging for development

    Environment Variables:
    - LOGFIRE_TOKEN: Required for Logfire integration
    - SERVICE_NAME: Override default service name

    Args:
        service_name: Default service name if SERVICE_NAME env var not set
    """
    # Only configure logfire if token is available
    logfire_token = os.environ.get("LOGFIRE_TOKEN", "").strip()
    if logfire_token:
        logfire.configure(
            service_name=os.getenv("SERVICE_NAME", service_name),
            service_version=__version__,
        )

        # Set up all the logfire instrumentation
        logfire.instrument_psycopg()  # Database query tracing
        logfire.instrument_pydantic_ai()  # AI model interaction tracing
        logfire.instrument_mcp()  # MCP server communication tracing
        logfire.instrument_system_metrics(
            {
                "process.cpu.time": ["user", "system"],
                "process.cpu.utilization": None,
                "process.cpu.core_utilization": None,
                "process.memory.usage": None,
                "process.memory.virtual": None,
                "process.thread.count": None,
            }
        )

        # Configure standard library logging with logfire handler
        dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "handlers": {
                    "logfire": {
                        "class": "logfire.LogfireLoggingHandler",
                    },
                },
                "root": {
                    "handlers": ["logfire"],
                    "level": "INFO",
                },
                "loggers": {
                    # Suppress noisy third-party loggers
                    "urllib3": {"level": "WARNING"},
                    "websockets": {"level": "WARNING"},
                },
            }
        )
    else:
        # Fallback to basic console logging when logfire token is not available
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )


def _truncate_tool_result(name: str, result: Any) -> Any:
    """Cap MCP tool results at MAX_TOOL_RESULT_CHARS so a single oversized
    payload (e.g. an unbounded thanos_range) can't blow the model's context.

    ContextManagerCapability.after_tool_execute only truncates plain strings
    and ToolReturns — MCP structured_content comes back as dict/list and slips
    through, so we truncate here at the source.

    The truncated payload is wrapped in explicit sentinel markers and prefaced
    with guidance so the model knows the response is incomplete and can
    narrow its next query rather than hallucinating that the JSON is complete.
    """
    try:
        serialized = (
            result if isinstance(result, str) else json.dumps(result, default=str)
        )
    except (TypeError, ValueError):
        return result

    if len(serialized) <= MAX_TOOL_RESULT_CHARS:
        return result

    keep = MAX_TOOL_RESULT_CHARS // 2
    omitted = len(serialized) - 2 * keep
    truncated = (
        f"[TOOL_RESULT_TRUNCATED tool={name} original_chars={len(serialized)} "
        f"kept_chars={2 * keep} omitted_chars={omitted}]\n"
        "The tool returned more data than fits in context. The response below "
        "has been split: the first half of the raw payload, a truncation "
        "marker, then the last half. Any JSON/structure spanning the middle is "
        "incomplete. Do NOT treat the visible content as the full result — "
        "re-run the tool with tighter filters (narrower time range, label "
        "selectors, smaller limit) to get a complete answer.\n"
        "--- BEGIN TRUNCATED PAYLOAD ---\n"
        f"{serialized[:keep]}\n"
        f"[... {omitted} characters omitted ...]\n"
        f"{serialized[-keep:]}\n"
        "--- END TRUNCATED PAYLOAD ---"
    )
    logfire.warning(
        "Truncated oversized MCP tool result",
        name=name,
        original_chars=len(serialized),
        kept_chars=len(truncated),
    )
    return truncated


class RepeatedToolCallTracker:
    """Counts identical tool calls within a single agent run.

    Agent runs were observed re-issuing the same call dozens of times: 85
    identical ``get_user_details`` invocations that had already *succeeded*,
    the same case summary fetched five times byte-for-byte, and an ID
    brute-forced through 118 variants after the first rejection. Prose caps do
    not hold -- one trace made 60 calls against a skill whose own text says
    "at most 2 retries per tool" -- so the bound lives in code.

    One tracker is shared by every MCP server in a run, since a run's tool
    calls fan out across several servers but the budget is per run.
    """

    def __init__(self, limit: int = MAX_IDENTICAL_TOOL_CALLS) -> None:
        self._limit = limit
        self._counts: dict[str, int] = {}

    @staticmethod
    def _key(name: str, tool_args: dict[str, Any]) -> str:
        """Exact match on canonicalised arguments.

        Deliberately exact rather than fuzzy: a near-match rule risks
        suppressing a legitimately different query, and the observed loops are
        dominated by byte-identical repeats that exact matching already stops.
        """
        try:
            args = json.dumps(tool_args, sort_keys=True, default=str)
        except (TypeError, ValueError):
            args = repr(tool_args)
        return f"{name}::{args}"

    @staticmethod
    def describe(name: str, tool_args: dict[str, Any]) -> str:
        """The tool the model actually asked for.

        Through the tigerlabs proxy nearly every call arrives as
        ``call_tool``, with the real target in ``tool_args``.
        """
        target = tool_args.get("toolName")
        return f"{name}({target})" if isinstance(target, str) else name

    def record(self, name: str, tool_args: dict[str, Any]) -> int:
        """Count this call and return how many times it has now been seen."""
        key = self._key(name, tool_args)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def exceeds_limit(self, attempt: int) -> bool:
        return attempt > self._limit


def _repeated_call_message(name: str, tool_args: dict[str, Any], attempt: int) -> str:
    return (
        f"[REPEATED_TOOL_CALL tool={RepeatedToolCallTracker.describe(name, tool_args)} "
        f"attempt={attempt}]\n"
        "This call was not executed. You have already made this exact call with "
        "these exact arguments earlier in this run, and its result is already in "
        "your context -- scroll back and use it rather than fetching it again.\n"
        "If that earlier result did not answer your question, repeating it will "
        "not either. Either use a different tool, change the arguments in a way "
        "that asks a genuinely different question, or record the fact as "
        "unresolved and move on."
    )


def create_wrapped_process_tool_call(
    existing_func: ProcessToolCallback | None,
    tracker: RepeatedToolCallTracker | None = None,
) -> ProcessToolCallback:
    async def process_tool_call(
        ctx: RunContext[AgentResponseContext],
        call_tool: CallToolFunc,
        name: str,
        tool_args: dict[str, Any],
    ):
        if tracker is not None:
            attempt = tracker.record(name, tool_args)
            if tracker.exceeds_limit(attempt):
                logfire.warning(
                    "Blocked repeated tool call",
                    name=name,
                    tool=RepeatedToolCallTracker.describe(name, tool_args),
                    attempt=attempt,
                )
                return _repeated_call_message(name, tool_args, attempt)

        try:
            if existing_func is not None:
                result = await existing_func(ctx, call_tool, name, tool_args)
            else:
                result = await call_tool(name, tool_args)
            return _truncate_tool_result(name, result)
        except Exception as ex:
            logfire.exception(
                "Exception occurred during tool call", name=name, tool_args=tool_args
            )
            return f"Tool call failed, could not retrieve information. Error: {ex}"

    return process_tool_call


def wrap_mcp_servers_with_exception_handling(mcp_servers: MCPDict) -> MCPDict:
    """Wrap MCP servers with exception handling for tool calls.

    Creates wrapper functions around existing process_tool_call methods
    to add consistent error handling and logging.

    Args:
        mcp_servers: Dictionary of MCP server configurations

    Returns:
        Modified dictionary with wrapped process_tool_call functions
    """
    # One tracker for the whole run. Each server is wrapped separately, but the
    # repeat budget is per run, not per server.
    tracker = RepeatedToolCallTracker()

    for value in mcp_servers.values():
        existing_process_tool_call = value.mcp_server.process_tool_call

        value.mcp_server.process_tool_call = create_wrapped_process_tool_call(
            existing_process_tool_call, tracker=tracker
        )

    return mcp_servers


def serialize_to_jsonb(model: BaseModel) -> Jsonb:
    """Convert a Pydantic BaseModel to a PostgreSQL Jsonb object."""
    return Jsonb(model.model_dump())


def file_type_supported(mimetype: str) -> bool:
    return mimetype and (
        mimetype == "application/pdf" or mimetype.startswith(("text/", "image/"))
    )


def _to_yaml(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if v is None:
                continue
            rendered = _to_yaml(v, indent + 1)
            if "\n" in rendered:
                lines.append(f"{pad}{k}:\n{rendered}")
            else:
                lines.append(f"{pad}{k}: {rendered}")
        return "\n".join(lines)
    if isinstance(value, list):
        items = [
            f"{pad}- {_to_yaml(v, indent + 1).lstrip()}" for v in value if v is not None
        ]
        return "\n".join(items)
    return str(value)


def pretty_print_models(models: list[BaseModel]) -> str:
    parts = []
    for model in models:
        parts.append(f"---\n{_to_yaml(model.model_dump())}")
    return "\n\n".join(parts)


def split_markdown_text_into_blocks(text: str, max_string_length: int) -> list[str]:
    """
    This is a simple string chunker. That takes a string and returns an array of chunks
    """

    if not text:
        return []

    chunks: list[str] = []
    current_chunk_length = 0
    current_chunk: str = ""

    # let's split sections by splitting on empty lines
    splits = text.split("\n\n")

    # then iterate over each of the sections
    for section in splits:
        padding = "\n\n" if current_chunk else ""
        section_length = len(section) + len(padding)

        # if we can add this section to the current chunk, do it
        if current_chunk_length + section_length <= max_string_length:
            current_chunk += padding + section
            current_chunk_length += section_length

        # otherwise, make a new chunk
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = section
            current_chunk_length = len(section)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks
