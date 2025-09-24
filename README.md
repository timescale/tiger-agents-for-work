# Tiger Agent


Want to see a Tiger Agent in action as quickly as possible? Jump to the [Quick Start](#quick-start).

## What is TigerData building with Tiger Agent?

At TigerData, we have used Tiger Agent to build Eon.
Eon is an agentic bot living in our internal Slack.
Eon answers questions of all sorts.

We are building Eon in the open. Check out our work at [https://github.com/timescale/tiger-eon](https://github.com/timescale/tiger-eon).
Use what we have learned as a reference for building your own Tiger Agent.

We gave Eon superpowers via multiple MCP servers, including:

1. **Slack** - Eon can read the conversation for context more like a human would. They can also search Slack for answers.
   [https://github.com/timescale/tiger-slack](https://github.com/timescale/tiger-slack)
2. **Docs** - Eon can search TigerData docs, Postgres docs, and use our curated prompts.
   [https://github.com/timescale/tiger-docs-mcp-server](https://github.com/timescale/tiger-docs-mcp-server)
3. **Memory** - Eon can "remember" important facts about interactions. [https://github.com/timescale/tiger-memory-mcp-server](https://github.com/timescale/tiger-memory-mcp-server)
4. **Linear** - Eon can read our Linear issues. [https://github.com/timescale/tiger-linear-mcp-server](https://github.com/timescale/tiger-linear-mcp-server)
5. **GitHub** - Eon can read our commits and pull requests. [https://github.com/timescale/tiger-gh-mcp-server](https://github.com/timescale/tiger-gh-mcp-server)
6. **Salesforce** - Eon can read our support cases. [https://github.com/timescale/tiger-salesforce-mcp-server](https://github.com/timescale/tiger-salesforce-mcp-server)

None of these MCP servers are **required** to use Tiger Agent, but feel free to if they suit your needs.

We have found Eon to be an extremely valuable addition to our company.

## Quick Start

### Prerequisites

1. [uv](https://docs.astral.sh/uv/)
2. [docker](https://www.docker.com/products/docker-desktop/)

### Setup

#### 1. Clone the repo and install the dependencies.

```bash
# clone the repo
git clone https://github.com/timescale/tiger-agent
cd tiger-agent

# install the dependencies
uv sync

# verify the installation
uv run python -m tiger_agent --help
```

#### 2. Run a TimescaleDB database in a docker container.

```bash
# pull the latest image
docker pull timescale/timescaledb-ha:pg17

# run the database container
docker run -d --name tiger-agent -e POSTGRES_PASSWORD=password -p 127.0.0.1:5432:5432 timescale/timescaledb-ha:pg17
```

#### 3. Create a Slack App

#### 4. Set your environment variables

Copy the sample .env file.

```bash
cp .env.sample .env
```
Edit the .env file.

1. Add your `SLACK_APP_TOKEN`. It starts with `xapp-`.
2. Add your `SLACK_BOT_TOKEN`. It starts with `xoxb-`.
3. Add your `ANTHROPIC_API_KEY`. It starts with `sk-ant-`.
4. [OPTIONAL] Add your `LOGFIRE_TOKEN`. It starts with `pylf_v1_`.

### Run a Tiger Agent

Run a Tiger Agent.

```bash
uv run python -m tiger_agent run 
```

## Customization

