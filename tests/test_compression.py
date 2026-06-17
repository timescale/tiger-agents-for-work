"""Tests for tiger_agent.compression."""

import json

from tiger_agent.compression import (
    MAX_COMPACT_RATIO,
    MIN_COMPRESSABLE_CHARS,
    compress_tool_result,
)


def make_items(n: int = 20, pad: int = 400) -> list[dict]:
    """Build n similarly-shaped dicts with constant and varying fields."""
    return [
        {
            "id": i,
            "title": f"Issue {i}",
            "status": "open",
            "team": {"id": "T1", "name": "Platform"},
            "description": "x" * pad,
        }
        for i in range(n)
    ]


def test_small_result_passes_through():
    payload = json.dumps(make_items(n=5, pad=0))
    assert len(payload) < MIN_COMPRESSABLE_CHARS
    assert compress_tool_result(payload) is payload


def test_non_json_string_passes_through():
    text = "plain text result " * 500
    assert compress_tool_result(text) is text


def test_non_json_non_collection_passes_through():
    assert compress_tool_result(42) == 42
    assert compress_tool_result(None) is None


def test_large_json_array_is_compacted():
    payload = json.dumps(make_items())
    compacted = compress_tool_result(payload)
    assert isinstance(compacted, str)
    assert compacted is not payload
    assert len(compacted) < len(payload) * MAX_COMPACT_RATIO
    # Constant fields are factored out once.
    assert compacted.count('"open"') == 1
    assert compacted.count("Platform") == 1


def test_all_items_preserved():
    items = make_items()
    compacted = compress_tool_result(json.dumps(items))
    for item in items:
        assert json.dumps(item["title"]) in compacted


def test_python_list_input_is_compacted():
    items = make_items()
    compacted = compress_tool_result(items)
    assert isinstance(compacted, str)
    assert len(compacted) < len(json.dumps(items))


def test_dict_envelope_keeps_scalar_fields():
    payload = json.dumps({"issues": make_items(), "next_cursor": "abc123"})
    compacted = compress_tool_result(payload)
    assert compacted is not payload
    assert "next_cursor" in compacted
    assert "abc123" in compacted
    assert "'issues'" in compacted


def test_missing_keys_marked_absent():
    items = make_items()
    del items[3]["title"]
    compacted = compress_tool_result(json.dumps(items))
    assert "<absent>" in compacted


def test_pipe_in_value_stays_quoted():
    # The column separator is " | ", so a value containing pipes could be
    # mistaken for column boundaries. Cells are JSON-encoded, so the value
    # stays wrapped in quotes and a row remains recoverable by tracking quotes
    # rather than naively splitting on the separator.
    items = make_items()
    items[5]["title"] = "this | has | pipes"
    compacted = compress_tool_result(json.dumps(items))
    assert '"this | has | pipes"' in compacted


def test_irregular_array_passes_through():
    payload = json.dumps(["just a string", *make_items()])
    assert compress_tool_result(payload) is payload


def test_few_items_pass_through():
    payload = json.dumps(make_items(n=3, pad=3000))
    assert len(payload) > MIN_COMPRESSABLE_CHARS
    assert compress_tool_result(payload) is payload


def test_insufficient_savings_keeps_original():
    # One short key with long unique values: key dedup saves almost nothing.
    items = [{"a": f"{i}" * 2000} for i in range(5)]
    payload = json.dumps(items)
    assert compress_tool_result(payload) is payload


def test_unserializable_input_falls_back_to_original():
    bad = {("tuple", "key"): make_items()}
    assert compress_tool_result(bad) is bad
