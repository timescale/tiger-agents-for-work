# MCP Server Configuration

Tiger Agent can be extended with powerful capabilities through MCP (Model Context Protocol) servers. This document explains how to configure and use MCP servers with your Tiger Agent instance.

## Overview

MCP servers provide specialized capabilities through a standardized protocol, allowing your Tiger Agent to access external APIs, databases, documentation systems, and specialized tools. There are two types of MCP servers supported:

- **HTTP-based MCP Servers**: Remote services accessible over Streamable HTTP
- **Command-line MCP Servers**: Local processes that run as stdio servers

## Configuration File

MCP servers are configured via a JSON configuration file (typically `mcp_config.json`). This file defines which servers to load, how to connect to them, and how to organize their tools.

### Basic Structure

```json
{
  "server_name": {
    "tool_prefix": "prefix",
    "url": "http://example.com/mcp",
    "allow_sampling": true,
    "disabled": false,
    "internal_only": false // if this is true, the tool can only be used in non-shared slack channels
  }
}
```

## HTTP-based MCP Servers

HTTP-based servers are ideal for cloud-based services and remote APIs.
The config values used to construct an [MCPServerStreamableHTTP](https://ai.pydantic.dev/api/mcp/#pydantic_ai.mcp.MCPServerStreamableHTTP) object.
All parameters to the construtor are supported.


### Configuration Format

```json
{
  "docs_server": {
    "tool_prefix": "docs",
    "url": "https://docs-mcp-server.example.com/mcp",
    "allow_sampling": false,
    "disabled": false
  },
  "github_server": {
    "tool_prefix": "github",
    "url": "http://localhost:3001/mcp",
    "allow_sampling": true,
    "disabled": false
  },
  "slack_server": {
    "tool_prefix": "slack",
    "url": "http://slack-mcp-server/mcp",
    "allow_sampling": false,
    "disabled": false
  }
}
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `tool_prefix` | Yes | Prefix added to tool names to prevent conflicts |
| `url` | Yes | HTTP endpoint for the MCP server |
| `allow_sampling` | No | Whether to allow model sampling for this server |
| `disabled` | No | Set to `true` to disable the server without removing config |

## Command-line MCP Servers

Command-line servers run as local processes and communicate via stdio.
The config values used to construct an [MCPServerStdio](https://ai.pydantic.dev/api/mcp/#pydantic_ai.mcp.MCPServerStdio) object.
All parameters to the construtor are supported.

### Configuration Format

```json
{
  "logfire_tools": {
    "command": "uvx",
    "args": ["logfire-mcp"],
    "env": {
      "LOGFIRE_READ_TOKEN": "your_token"
    },
    "disabled": false
  },
  "custom_tools": {
    "command": "python",
    "args": ["/path/to/mcp_server.py", "--config", "prod"],
    "env": {
      "DATABASE_URL": "postgresql://...",
      "API_KEY": "secret"
    },
    "disabled": false
  }
}
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `command` | Yes | Executable command to run the server |
| `args` | No | Array of command-line arguments |
| `env` | No | Environment variables to pass to the server |
| `disabled` | No | Set to `true` to disable the server without removing config |

## Configuration Features

### Tool Prefixing

Tool prefixes prevent naming conflicts when multiple servers provide similar functionality:

```json
{
  "github_server": {
    "tool_prefix": "github",
    "url": "http://localhost:3001/mcp"
  },
  "gitlab_server": {
    "tool_prefix": "gitlab",
    "url": "http://localhost:3002/mcp"
  }
}
```

Tools from these servers will be available as `github_create_issue`, `gitlab_create_issue`, etc.

### Selective Enabling/Disabling

Disable servers without removing their configuration:

```json
{
  "development_server": {
    "command": "python",
    "args": ["dev_server.py"],
    "disabled": true
  }
}
```

### Environment Variables

Pass configuration and secrets to command-line servers:

```json
{
  "database_tools": {
    "command": "python",
    "args": ["db_mcp_server.py"],
    "env": {
      "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
      "LOG_LEVEL": "INFO",
      "API_KEY": "secret_key_here"
    }
  }
}
```

### Sampling Control

Enable or disable [MCP Sampling](https://modelcontextprotocol.io/specification/2025-06-18/client/sampling).

```json
{
  "creative_server": {
    "tool_prefix": "creative",
    "url": "http://localhost:3003/mcp",
    "allow_sampling": true
  },
  "deterministic_server": {
    "tool_prefix": "calc",
    "url": "http://localhost:3004/mcp",
    "allow_sampling": false
  }
}
```

## Example Configurations

### Complete Example

```json
{
  "logfire_server": {
    "command": "uvx",
    "args": ["logfire-mcp"],
    "env": {
      "LOGFIRE_READ_TOKEN": "your_logfire_token"
    },
    "disabled": false
  },
  "docs_server": {
    "tool_prefix": "docs",
    "url": "https://docs-api.example.com/mcp",
    "allow_sampling": true,
    "disabled": false
  },
  "local_database": {
    "command": "python",
    "args": ["/opt/mcp/db_server.py", "--port", "5433"],
    "env": {
      "DATABASE_URL": "postgresql://tiger:password@localhost:5433/tiger_db",
      "POOL_SIZE": "10"
    },
    "disabled": false
  },
  "github_integration": {
    "tool_prefix": "github",
    "url": "http://localhost:3001/mcp",
    "allow_sampling": false,
    "disabled": false
  }
}
```

### Development vs Production

You can maintain different configurations for different environments:

**development.json**:
```json
{
  "local_docs": {
    "command": "python",
    "args": ["local_docs_server.py"],
    "env": {
      "DOCS_PATH": "./docs"
    }
  }
}
```

**production.json**:
```json
{
  "docs_server": {
    "tool_prefix": "docs",
    "url": "https://prod-docs-api.example.com/mcp",
    "allow_sampling": false
  }
}
```

## Using MCP Configuration

### With CLI

```bash
# Run with MCP servers
tiger-agent run --mcp-config mcp_config.json

# Run without MCP servers
tiger-agent run
```

### With Python API

```python
from tiger_agent import TigerAgent
from pathlib import Path

# Load MCP configuration
agent = TigerAgent(
    model="claude-3-5-sonnet-latest",
    jinja_env=Path("./templates"),
    mcp_config_path=Path("./mcp_config.json")
)
```

## MCP Servers to Try

1. [tiger-slack](https://github.com/timescale/tiger-slack) - read Slack history
2. [tiger-docs-mcp-server](https://github.com/timescale/tiger-docs-mcp-server) - Postgres and TigerData documentation
3. [tiger-gh-mcp-server](https://github.com/timescale/tiger-gh-mcp-server) - read GitHub commits/PRs
4. [tiger-linear-mcp-server](https://github.com/timescale/tiger-linear-mcp-server) - read Linear issues
