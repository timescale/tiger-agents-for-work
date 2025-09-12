#!/bin/bash

# Load environment variables from .env if it exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Build services first
echo "Building services..."
docker-compose build

# Always start core services
echo "Starting core services..."
docker-compose up -d app db tiger-slack-ingest tiger-slack-mcp

# Conditionally start MCP servers based on DISABLE_* environment variables
if [ -z "$DISABLE_DOCS_MCP_SERVER" ]; then
    echo "Starting docs MCP server..."
    docker-compose up -d tiger-docs-mcp
fi

if [ -z "$DISABLE_GITHUB_MCP_SERVER" ]; then
    echo "Starting GitHub MCP server..."
    docker-compose up -d tiger-github-mcp
fi

if [ -z "$DISABLE_LINEAR_MCP_SERVER" ]; then
    echo "Starting Linear MCP server..."
    docker-compose up -d tiger-linear-mcp
fi

if [ -z "$DISABLE_MEMORY_MCP_SERVER" ]; then
    echo "Starting memory MCP server..."
    docker-compose up -d tiger-memory-mcp
fi

if [ -z "$DISABLE_SALESFORCE_MCP_SERVER" ]; then
    echo "Starting Salesforce MCP server..."
    docker-compose up -d tiger-salesforce-mcp
fi

echo "Services started successfully!"