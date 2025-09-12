#!/bin/bash

# Load environment variables from .env if it exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Build services first
echo "Building services..."
docker-compose build

# Build profile arguments based on DISABLE_* variables
PROFILES=""

if [ -z "$DISABLE_DOCS_MCP_SERVER" ]; then
    PROFILES="$PROFILES --profile docs-mcp"
fi

if [ -z "$DISABLE_GITHUB_MCP_SERVER" ]; then
    PROFILES="$PROFILES --profile github-mcp"
fi

if [ -z "$DISABLE_LINEAR_MCP_SERVER" ]; then
    PROFILES="$PROFILES --profile linear-mcp"
fi

if [ -z "$DISABLE_MEMORY_MCP_SERVER" ]; then
    PROFILES="$PROFILES --profile memory-mcp"
fi

if [ -z "$DISABLE_SALESFORCE_MCP_SERVER" ]; then
    PROFILES="$PROFILES --profile salesforce-mcp"
fi

# Start services with the constructed profiles
echo "Starting services with profiles: $PROFILES"
docker-compose $PROFILES up -d

echo "Services started successfully!"