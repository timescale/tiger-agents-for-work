# Tiger Agent

An intelligent orchestrator agent for TigerData that provides comprehensive assistance for team collaboration, technical documentation, and customer support through specialized sub-agents.

## Architecture

Tiger Agent implements an **orchestrator + sub-agent architecture** where the main `eon` agent serves as an intelligent router that delegates tasks to specialized sub-agents based on the nature of incoming requests.

### Orchestrator Agent: Eon

The **Eon** agent is the primary entry point that:

- Receives questions and requests from Slack
- Analyzes the intent and context of each request
- Routes queries to the most appropriate sub-agent
- Provides fallback responses using general knowledge
- Maintains conversation context and user session data

Sub-agents are accessed via **tool definitions** on the orchestrator agent, allowing Eon to seamlessly invoke specialized capabilities as needed.

### Sub-Agents

#### 1. Progress Agent (`progress_agent_tool`)

**Purpose**: Team activity tracking and project management insights

**Capabilities**:

- Individual contributor progress summaries
- Project status reports and timeline analysis
- Cross-platform collaboration insights (Slack → GitHub → Linear)
- "Snooper of the Week" reports with team highlights
- Memory storage for user preferences and context

**Data Sources**: Slack conversations, GitHub repositories, Linear issues, user memory system

**Use Cases**: 

- "What has @john been working on this week?"
- "Generate a progress report for the authentication project"
- "Create a snooper of the week report"

#### 2. Documentation Agent (`docs_agent_tool`)

**Purpose**: Technical documentation and platform expertise

**Capabilities**:

- PostgreSQL, TimescaleDB, and TigerCloud documentation search
- Feature explanations with direct documentation quotes
- Configuration guidance and best practices
- SQL syntax help and performance optimization advice
- Confidence levels when documentation is incomplete

**Data Sources**: Official documentation repositories, technical guides, API references

**Use Cases**:

- "How do I configure continuous aggregates in TimescaleDB?"
- "What are the best practices for PostgreSQL indexing?"
- "Show me the API for creating hypertables"

#### 3. Sales Agent (`sales_agent_tool`)

**Purpose**: Customer support and sales insights from historical data

**Capabilities**:

- Semantic search through Salesforce support case summaries
- Historical problem resolution lookup
- Customer issue pattern identification
- Sales team insights from support trends
- Case-specific details with direct Salesforce links

**Data Sources**: Salesforce support cases, customer interaction history, case summaries with embeddings

**Use Cases**:

- "How have we handled database connection issues in the past?"
- "Find similar customer problems to this current issue"
- "What are the most common support requests this quarter?"

## Project Structure

```
tiger-agent/
├── app/
│   ├── __init__.py                     # Package constants (AGENT_NAME)
│   ├── main.py                         # Application entry point and worker setup
│   ├── mcp_servers.py                  # MCP server configurations and factories
│   ├── events/                         # Slack event handlers
│   ├── agents/                         # Agent implementations
│   │   ├── types.py                    # Shared type definitions (AgentContext, Mention, BotInfo)
│   │   ├── eon.py                      # Main orchestrator agent with sub-agent tools
│   │   ├── progress.py                 # Progress tracking and team analytics agent
│   │   ├── docs.py                     # Documentation and technical support agent
│   │   └── sales.py                    # Sales and customer support agent
│   └── utils/                          # Utility modules
│       ├── db.py                       # Database operations (mentions, cleanup)
│       └── slack.py                    # Slack API utilities (reactions, messaging)
├── migrations/                         # Database schema migrations
│   └── runner.py                       # Migration execution logic
├── pyproject.toml                      # Project dependencies and configuration
├── uv.lock                             # Lock file for dependencies
└── README.md                           # This file
```

### Cross-Platform Integration

- **Slack**: Real-time messaging and conversation context
- **GitHub**: Code repositories, pull requests, and commit history
- **Linear**: Project management and issue tracking  
- **Salesforce**: Customer support cases and sales data
- **Memory System**: User preferences and conversation context

### Conversation Context Management

- Thread-aware responses in Slack
- User timezone handling
- Session persistence across interactions
- Memory storage for user preferences

## Getting Started

### Prerequisites

- Python 3.13+
- PostgreSQL database
- Required MCP servers running (GitHub, Slack, Linear, Salesforce, Memory)
- Environment variables configured

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

### Configuration

The agent requires several MCP servers to be running:

- `GITHUB_MCP_SERVER_URL`: GitHub integration
- `SLACK_MCP_SERVER_URL`: Slack API access  
- `LINEAR_MCP_SERVER_URL`: Linear project management
- `SALESFORCE_MCP_SERVER_URL`: Customer support data
- `MEMORY_MCP_SERVER_URL`: User memory and preferences

## Usage Examples

### Progress Tracking

```
@eon What has the team been working on this week?
```
→ Routes to `progress_agent_tool` for comprehensive team activity summary

### Technical Support  

```
@eon How do I optimize query performance in TimescaleDB?
```
→ Routes to `docs_agent_tool` for documentation-backed technical guidance

### Customer Support

```
@eon Has anyone reported similar database connection errors before?
```
→ Routes to `sales_agent_tool` for historical support case analysis

## Development

### Adding New Sub-Agents

1. Create a new agent file in `app/agents/`
2. Implement the agent with appropriate MCP server toolsets
3. Create a query function that accepts `message` and `AgentContext`
4. Add a tool definition to the `eon_agent` in `eon.py`
5. Update the system prompt with tool selection guidelines

### Testing

The system includes comprehensive logging via Logfire for monitoring agent interactions and performance analysis.