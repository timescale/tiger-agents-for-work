"""Tests for tiger_agent.compression."""

import json

import toons

from tiger_agent.compression import (
    MAX_COMPACT_RATIO,
    MIN_COMPRESSABLE_CHARS,
    compress_tool_result,
)


def make_flat(n: int = 80) -> list[dict]:
    """Flat, uniform rows — the shape TOON compacts into a column table."""
    return [
        {"id": i, "title": f"Issue {i}", "status": "open", "score": i * 7 % 100}
        for i in range(n)
    ]


def make_nested(n: int = 20, pad: int = 400) -> list[dict]:
    """Rows with a nested object — TOON can't tabulate these, so they stay JSON."""
    return [
        {
            "id": i,
            "title": f"Issue {i}",
            "team": {"id": "T1", "name": "Platform"},
            "description": "x" * pad,
        }
        for i in range(n)
    ]


def test_small_result_passes_through():
    payload = json.dumps(make_flat(n=5))
    assert len(payload) < MIN_COMPRESSABLE_CHARS
    assert compress_tool_result(payload) is payload


def test_non_json_string_passes_through():
    text = "plain text result " * 500
    assert compress_tool_result(text) is text


def test_non_json_non_collection_passes_through():
    assert compress_tool_result(42) == 42
    assert compress_tool_result(None) is None


def test_large_flat_array_is_compacted():
    payload = json.dumps(make_flat())
    compacted = compress_tool_result(payload)
    assert isinstance(compacted, str)
    assert compacted is not payload
    assert len(compacted) < len(payload) * MAX_COMPACT_RATIO


def test_compaction_is_lossless():
    items = make_flat()
    compacted = compress_tool_result(json.dumps(items))
    # TOON encoding round-trips back to the original items — no data dropped.
    assert toons.loads(compacted) == items


def test_python_list_input_is_compacted():
    items = make_flat()
    compacted = compress_tool_result(items)
    assert isinstance(compacted, str)
    assert toons.loads(compacted) == items


def test_dict_envelope_is_compacted_and_lossless():
    payload_obj = {"issues": make_flat(), "next_cursor": "abc123", "has_more": True}
    compacted = compress_tool_result(json.dumps(payload_obj))
    assert isinstance(compacted, str)
    assert "next_cursor" in compacted
    assert "abc123" in compacted
    assert toons.loads(compacted) == payload_obj


def test_nested_array_not_smaller_passes_through():
    # Nested objects block TOON's column table, so the encoding is ~JSON-sized
    # and the ratio gate keeps the original JSON.
    payload = json.dumps(make_nested())
    assert len(payload) > MIN_COMPRESSABLE_CHARS
    assert compress_tool_result(payload) is payload


def test_unserializable_input_falls_back_to_original():
    bad = {("tuple", "key"): make_flat()}
    assert compress_tool_result(bad) is bad
