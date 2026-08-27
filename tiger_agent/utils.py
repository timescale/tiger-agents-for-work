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


def create_wrapped_process_tool_call(
    existing_func: ProcessToolCallback | None,
) -> ProcessToolCallback:
    async def process_tool_call(
        ctx: RunContext[AgentResponseContext],
        call_tool: CallToolFunc,
        name: str,
        tool_args: dict[str, Any],
    ):
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
    for value in mcp_servers.values():
        existing_process_tool_call = value.mcp_server.process_tool_call

        value.mcp_server.process_tool_call = create_wrapped_process_tool_call(
            existing_process_tool_call
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
