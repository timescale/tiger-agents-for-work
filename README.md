# Tiger Agent

An intelligent orchestrator agent for TigerData that provides comprehensive assistance for team collaboration, technical documentation, and customer support through specialized sub-agents.

## Architecture

```
                    ┌─────────────────────────┐
                    │                         │
                    │    🎯 EON AGENT         │
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
    │   📊 PROGRESS │ │   📚 DOCS       │ │ 💼 SALES   │
    │     AGENT     │ │    AGENT        │ │   AGENT    │
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

**🎯 EON (Orchestrator)**: Primary entry point from Slack that routes requests to specialized sub-agents

**📊 PROGRESS**: Team tracking - "What has @john been working on?" | Project reports | Cross-platform insights  

**📚 DOCS**: Technical expertise - PostgreSQL/TimescaleDB help | Configuration guidance | API references

**💼 SALES**: Customer support - Salesforce data search | Support ticket insights | Customer history

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