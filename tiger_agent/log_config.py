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

import logging
import os
from logging.config import dictConfig

import logfire

from tiger_agent import __version__


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
        logfire.instrument_httpx(capture_headers=True)  # HTTP client request tracing
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
