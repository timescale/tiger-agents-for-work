import logging
import os
from logging.config import dictConfig

import logfire

from tiger_agent import __version__


def setup_logging(service_name: str = "tiger-agent") -> None:
    """Configure Python standard library logging to use logfire as handler."""
    # Only configure logfire if token is available
    logfire_token = os.environ.get("LOGFIRE_TOKEN", "").strip()
    if logfire_token:
        logfire.configure(
            service_name=os.getenv("SERVICE_NAME", service_name),
            service_version=__version__,
        )

        # Set up all the logfire instrumentation
        logfire.instrument_psycopg()
        logfire.instrument_pydantic_ai()
        logfire.instrument_mcp()
        logfire.instrument_httpx()
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
                    # Suppress noisy third-party loggers if needed
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
