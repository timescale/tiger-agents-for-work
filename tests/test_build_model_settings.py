"""Tests for build_model_settings in tiger_agent.agent.utils."""

from pydantic_ai.models.anthropic import AnthropicModel

from tiger_agent.agent.utils import build_model_settings

CACHE_KEYS = {
    "anthropic_cache_tool_definitions",
    "anthropic_cache_instructions",
    "anthropic_cache_messages",
}
BETA_HEADER = {"anthropic-beta": "context-1m-2025-08-07"}


def test_default_ttl_on_modern_anthropic_model():
    settings = build_model_settings("anthropic:claude-sonnet-4-6", "5m")
    assert settings == dict.fromkeys(CACHE_KEYS, "5m")


def test_one_hour_ttl():
    settings = build_model_settings("anthropic:claude-sonnet-4-6", "1h")
    assert settings == dict.fromkeys(CACHE_KEYS, "1h")


def test_cache_disabled_on_modern_model_yields_none():
    assert build_model_settings("anthropic:claude-sonnet-4-6", None) is None


def test_cache_disabled_on_sonnet_45_keeps_beta_header():
    settings = build_model_settings("anthropic:claude-sonnet-4-5", None)
    assert settings == {"extra_headers": BETA_HEADER}


def test_sonnet_4_gets_header_and_cache():
    settings = build_model_settings("anthropic:claude-sonnet-4-20250514", "5m")
    assert settings["extra_headers"] == BETA_HEADER
    assert all(settings[k] == "5m" for k in CACHE_KEYS)


def test_non_anthropic_model_yields_none():
    assert build_model_settings("openai:gpt-4o", "5m") is None


def test_no_model_yields_none():
    assert build_model_settings(None, "5m") is None


def test_anthropic_model_instance(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = AnthropicModel("claude-sonnet-4-5")
    settings = build_model_settings(model, "5m")
    assert settings["extra_headers"] == BETA_HEADER
    assert all(settings[k] == "5m" for k in CACHE_KEYS)
