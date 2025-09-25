# Tiger Agent CLI

Tiger Agent provides a command-line interface for running custom AI-powered Slack bots.
The CLI allows you to quickly deploy a TigerAgent instance with custom prompts and MCP server integrations without writing Python code.

## Create a Custom Tiger Agent with the CLI

### 0. Prerequisites

Before running the Tiger Agent CLI, you need:

1. [uv](https://docs.astral.sh/uv/)
2. A PostgreSQL database with TimescaleDB. You can use [docker](https://www.docker.com/products/docker-desktop/) if you wish.
3. An ANTHROPIC_API_KEY
4. A Slack app with tokens (see [docs/slack_app.md](/docs/slack_app.md))


### 1. Database Creation

You will need a PostgreSQL database with the TimescaleDB extension.

You can use docker to run the database:

```bash
# pull the latest image
docker pull timescale/timescaledb-ha:pg17

# run the database container
docker run -d --name tiger-agent \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=tsdb \
  -e POSTGRES_USER=tsdbadmin \
  -p 127.0.0.1:5432:5432 \
  timescale/timescaledb-ha:pg17
```

### 2. Project Structure

Your project structure will look like this:

```
my-tiger-agent/
├── .env
├── mcp_config.json
└── prompts/
    ├── system_prompt.md
    └── user_prompt.md
```

Create the root directory.

```bash
mkdir my-tiger-agent

cd my-tiger-agent
```

### 3. Environment Variables

Create a `.env` file to put your environment variables in. Copy [.env.sample](/.env.sample) to get started.

```bash
curl -o .env https://raw.githubusercontent.com/timescale/tiger-agent/refs/heads/main/.env.sample
```

Then, edit the `.env` file to add your:

- SLACK_APP_TOKEN
- SLACK_BOT_TOKEN
- ANTHROPIC_API_KEY
- LOGFIRE_TOKEN (optional)


### 4. Prompts

Create a directory to put your prompt templates in.

```bash
mkdir prompts
```

Copy the [system_prompt.md](/prompts/system_prompt.md) and [user_prompt.md](/prompts/user_prompt.md) into the `prompts` directory you just created.

```bash
curl -o prompts/system_prompt.md https://raw.githubusercontent.com/timescale/tiger-agent/refs/heads/main/prompts/system_prompt.md
curl -o prompts/user_prompt.md https://raw.githubusercontent.com/timescale/tiger-agent/refs/heads/main/prompts/user_prompt.md
```

Use these Jinja2 templates as a starting point for customizing the instructions for your agent.
See [Prompt Templates](prompt_templates.md) for detailed instructions on customizing these templates.

### 5. MCP Server Config File (Optional)

You can give your agent capabilities by configuring MCP Servers for it to use.
Copy [examples/mcp_config.json](/examples/mcp_config.json) to your project as an example to get started.

```bash
curl -o mcp_config.json https://raw.githubusercontent.com/timescale/tiger-agent/refs/heads/main/examples/mcp_config.json
```

Read [MCP Server Configuration](mcp_config.md) for detailed instructions on how to edit this file.


### 6. Running the Tiger Agent CLI

Install Tiger Agent as a tool

```bash
# install the tool
uv tool install --from git+https://github.com/timescale/tiger-agent.git tiger-agent

# test the installation
tiger-agent --help
```

Run the CLI (without MCP Servers):

```bash
tiger-agent run
```

Run the CLI with MCP Servers:

```bash
tiger-agent run --mcp-config mcp_config.json
```

#### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | `anthropic:claude-sonnet-4-20250514` | AI model to use |
| `--prompts` | `./prompts` | Directory containing prompt templates |
| `--mcp-config` | None | Path to MCP configuration JSON file |
| `--env` | Auto-detected | Path to custom environment file |
| `--worker-sleep-seconds` | `60` | Base worker sleep duration |
| `--worker-min-jitter-seconds` | `-15` | Minimum jitter for worker sleep |
| `--worker-max-jitter-seconds` | `15` | Maximum jitter for worker sleep |
| `--max-attempts` | `3` | Maximum retry attempts per event |
| `--max-age-minutes` | `60` | Event expiration time |
| `--invisibility-minutes` | `10` | Task claim duration |
| `--num-workers` | `5` | Number of concurrent workers |

#### Examples

```bash
# Basic usage with default settings
tiger-agent run --prompts ./my-prompts

# Custom model and MCP integration
tiger-agent run \
  --model "openai:gpt-4" \
  --prompts ./prompts \
  --mcp-config ./config/mcp_servers.json

# Production configuration with custom worker settings
tiger-agent run \
  --prompts ./prompts \
  --mcp-config ./mcp_config.json \
  --num-workers 10 \
  --max-attempts 5 \
  --worker-sleep-seconds 30

# Using custom environment file
tiger-agent run \
  --prompts ./prompts \
  --env .env.production
```

## If you need further customization...

If you need to customize your Tiger Agent beyond what is possible with the CLI, check out the guide in [docs/tiger_agent.md](/docs/tiger_agent.md)!
