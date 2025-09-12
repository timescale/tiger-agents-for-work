# Tiger Agent

An intelligent orchestrator agent for TigerData that provides comprehensive assistance for team collaboration, technical documentation, and customer support through specialized sub-agents.

## Architecture

```
                    ┌─────────────────────────┐
                    │                         │
                    │     EON AGENT         │
                    │   (Orchestrator)        │
                    │                         │
                    │ • Routes requests       │
                    │ • Maintains context     │
                    │ • Fallback responses    │
                    │                         │
                    └─────────┬───────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
    ┌───────▼──────┐ ┌────────▼────────┐ ┌─────▼──────┐
    │              │ │                 │ │            │
    │   PROGRESS   │ │      DOCS       │ │    SALES   │
    │     AGENT    │ │    AGENT        │ │   AGENT    │
    │              │ │                 │ │            │
    └───────┬──────┘ └────────┬────────┘ └─────┬──────┘
            │                 │                │
    ┌───────▼──────┐ ┌────────▼────────┐ ┌─────▼──────┐
    │ Team activity│ │PostgreSQL docs  │ │Salesforce  │
    │ GitHub repos │ │TimescaleDB docs │ │support data│
    │ Linear issues│ │TigerCloud docs  │ │Customer    │
    │ Slack convos │ │API references   │ │insights    │
    └──────────────┘ └─────────────────┘ └────────────┘
```

### Agent Capabilities

**eon**: Primary entry point from Slack that routes requests to specialized sub-agents
**progress**: Team tracking - "What has @john been working on?" | Project reports | Cross-platform insights  
**docs**: Technical expertise - PostgreSQL/TimescaleDB help | Configuration guidance | API references
**sales**: Customer support - Salesforce data search | Support ticket insights | Customer history

## Getting Started

### Installation

```bash
# Install dependencies
uv sync

# Set up environment variables
cp .env.sample .env
# Edit .env with your configuration

# Run database migrations  
uv run python -m migrations.runner

# Start the agent
uv run python -m app.main
```

### Environment Variables

First, initialize your environment configuration:

```bash
cp .env.sample .env
```

#### Required Variables

**Slack Integration** (Always Required):
```bash
SLACK_BOT_TOKEN=xoxb-your_bot_token_here
SLACK_APP_TOKEN=xapp-your_app_token_here
SLACK_DOMAIN=your_workspace_domain
```

**Core Services** (Always Required):
```bash
ANTHROPIC_API_KEY=sk-ant-your_anthropic_api_key_here
LOGFIRE_TOKEN=pylf_your_logfire_token_here
```

#### MCP Server Tokens

Depending on which MCP servers you have running, you'll need the corresponding API tokens:

**GitHub MCP Server** (if not disabled):
```bash
GITHUB_TOKEN=ghp_your_github_token_here
```

**Linear MCP Server** (if not disabled):
```bash
LINEAR_API_KEY=lin_api_your_linear_api_key_here
```

#### Optional: Disable MCP Servers

Set any of these variables to any value to disable the corresponding MCP server:

```bash
DISABLE_DOCS_MCP_SERVER=1          # Disable documentation server
DISABLE_GITHUB_MCP_SERVER=1        # Disable GitHub integration
DISABLE_LINEAR_MCP_SERVER=1        # Disable Linear integration  
DISABLE_MEMORY_MCP_SERVER=1        # Disable user memory
DISABLE_SALESFORCE_MCP_SERVER=1    # Disable Salesforce integration
DISABLE_SLACK_MCP_SERVER=1         # Disable Slack MCP server
```

**Note**: Database variables (`PGHOST`, `PGDATABASE`, etc.) are pre-configured for the Docker setup and typically don't need modification.

## Docker Deployment

### Quick Start

Use the provided startup script for intelligent container management:

```bash
./start.sh
```

This script will:
1. Build all Docker images
2. Start core services (app, database, Slack ingest)
3. Conditionally start MCP servers based on your `DISABLE_*` environment variables

### Manual Docker Commands

```bash
# Build and start all services
docker-compose build
docker-compose up -d

# Start only core services
docker-compose up -d app db tiger-slack-ingest

# Start specific MCP servers
docker-compose up -d tiger-slack-mcp tiger-memory-mcp

# View logs
docker-compose logs -f app
```

### MCP Server Management

The `DISABLE_*` environment variables control both:
- **Application behavior**: The agent won't attempt to connect to disabled servers
- **Docker container creation**: The `./start.sh` script won't start disabled server containers

For example, setting `DISABLE_GITHUB_MCP_SERVER=1` will:
- Skip starting the `tiger-github-mcp` container
- Make the agent return `None` when trying to access GitHub functionality

This allows you to run only the MCP servers you need, reducing resource usage and avoiding API token requirements for unused services.