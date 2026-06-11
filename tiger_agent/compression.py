"""Compact oversized MCP tool results before they reach the model.

Large tool results (issue lists, search hits, query results) are dominated by
JSON arrays of similarly-shaped objects, and most of their bulk is object keys
repeated per item plus field values shared by every item. Rendering such
arrays as a table with the constant fields factored out removes that
redundancy without dropping any items. Results that aren't shaped this way
(plain text, binary content, irregular JSON) pass through untouched.
"""

import json
import logging
from typing import Any

from pydantic_ai.mcp import ToolResult

logger = logging.getLogger(__name__)

# Results smaller than this pass through untouched; compacting small payloads
# costs more than it saves.
MIN_COMPRESSABLE_CHARS = 4_000

# Arrays with fewer items than this stay as JSON; tables only pay off when
# keys repeat enough times.
MIN_TABLE_ROWS = 5

# Keep the original unless the compacted form is at least 20% smaller.
MAX_COMPACT_RATIO = 0.8


def compress_tool_result(result: ToolResult) -> ToolResult:
    """Compact a large JSON tool result; return the original if not applicable."""
    try:
        if isinstance(result, str):
            try:
                obj = json.loads(result)
            except ValueError:
                return result
            original_len = len(result)
        elif isinstance(result, (dict, list)):
            obj = result
            original_len = len(json.dumps(obj, default=str))
        else:
            return result

        if original_len < MIN_COMPRESSABLE_CHARS:
            return result

        compacted = _compact(obj)
        if compacted is not None and len(compacted) < original_len * MAX_COMPACT_RATIO:
            logger.info(
                "compacted tool result from %d to %d chars",
                original_len,
                len(compacted),
            )
            return compacted
    except Exception:
        logger.exception("tool result compaction failed, returning original")
    return result


def _compact(obj: Any) -> str | None:
    if isinstance(obj, list):
        return _tabulate(obj, title="items")

    if isinstance(obj, dict):
        tables = []
        remainder = dict(obj)
        for key, value in obj.items():
            table = _tabulate(value, title=key) if isinstance(value, list) else None
            if table is not None:
                remainder[key] = f"<{len(value)} items, see table '{key}' below>"
                tables.append(table)
        if not tables:
            return None
        return "\n\n".join([_dumps(remainder), *tables])

    return None


def _tabulate(items: list[Any], title: str) -> str | None:
    """Render a list of similarly-shaped dicts as a constant-factored table."""
    if len(items) < MIN_TABLE_ROWS or not all(isinstance(i, dict) for i in items):
        return None

    keys: dict[str, None] = {}  # ordered union of keys across all items
    for item in items:
        keys.update(dict.fromkeys(item))

    cells = [{k: _dumps(item[k]) for k in item} for item in items]
    constant = [
        k
        for k in keys
        if all(k in c for c in cells) and len({c[k] for c in cells}) == 1
    ]
    varying = [k for k in keys if k not in constant]

    lines = [
        f"'{title}': {len(items)} items as a table"
        " (each row is one item; combine with the constant fields):"
    ]
    if constant:
        lines.append(
            "constant fields (identical for every item): "
            + _dumps({k: items[0][k] for k in constant})
        )
    if varying:
        lines.append(" | ".join(varying))
        lines.extend(
            " | ".join(cell.get(k, "<absent>") for k in varying) for cell in cells
        )
    return "\n".join(lines)


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
