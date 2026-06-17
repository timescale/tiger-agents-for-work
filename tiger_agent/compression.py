"""Compact oversized MCP tool results before they reach the model.

Large tool results (issue lists, search hits, query results) are dominated by
JSON arrays of similarly-shaped objects, and most of their bulk is object keys
repeated per item. Re-encoding such results as TOON (Token-Oriented Object
Notation) factors those keys out: a uniform array of flat objects becomes a
table whose columns are declared once, dropping the per-row key repetition
without losing any items. Results that don't benefit (plain text, binary
content, irregular or deeply nested JSON) fall through untouched.

TOON encoding is lossless: `toons.loads(toons.dumps(obj)) == obj`. The
threshold and ratio gates below mean a compacted result reaches the model only
when it is meaningfully smaller than the original JSON; otherwise the original
is returned unchanged.
"""

import json
import logging

import toons
from pydantic_ai.mcp import ToolResult

logger = logging.getLogger(__name__)

# Results smaller than this pass through untouched; compacting small payloads
# costs more than it saves.
MIN_COMPRESSABLE_CHARS = 4_000

# Keep the original unless the compacted form is at least 20% smaller. TOON only
# wins decisively on flat uniform arrays; nested or irregular shapes encode to
# roughly the same size as JSON and are left as JSON by this gate.
MAX_COMPACT_RATIO = 0.8


def compress_tool_result(result: ToolResult) -> ToolResult:
    """Compact a large JSON tool result via TOON; return the original if not applicable."""
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

        if not isinstance(obj, (dict, list)):
            return result

        compacted = toons.dumps(obj)
        if len(compacted) < original_len * MAX_COMPACT_RATIO:
            logger.info(
                "compacted tool result from %d to %d chars",
                original_len,
                len(compacted),
            )
            return compacted
    except Exception:
        logger.exception("tool result compaction failed, returning original")
    return result
