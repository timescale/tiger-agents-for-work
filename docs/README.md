# Tiger Agent Documentation

## [Database Architecture](database.md)
Detailed explanation of the PostgreSQL + TimescaleDB database design, including the event work queue system, database functions for atomic operations, migration system, and data models for durable event processing.

## [Event Processing Harness](event_harness.md)
Technical documentation of the EventHarness system that provides scalable, resilient event processing with bounded concurrency, immediate responsiveness, and atomic event claiming through PostgreSQL work queues.

## [TigerAgent](tiger_agent.md)
Deep dive into the TigerAgent class - the AI-powered event processor that integrates Pydantic-AI with MCP servers and Jinja2 templating. Includes customization patterns, configuration options, and subclassing examples for specialized use cases.

## [CLI Usage](cli.md)
Complete guide to using Tiger Agent as a command-line tool. Covers installation, configuration, prompt templates, MCP server setup, and deployment examples for creating custom AI bots without writing code.

## [Observability](observability.md)
Guide to Tiger Agent's comprehensive observability features using Logfire, including automatic instrumentation, system metrics collection, tracing patterns, and monitoring best practices for production deployments.
