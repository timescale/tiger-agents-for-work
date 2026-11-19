"""Read-only access to the internal docs indexed in Memory Engine.

The two public coroutines, ``me_memory_search`` and ``me_memory_get_by_path``,
mirror the Memory Engine MCP tools of the same name, because the
``td-find-internal-docs`` skill is written against exactly those two tools. They
differ in four deliberate ways:

* the space is a required argument on every call, as the skill passes it, with
  no configured default,
* only two trees can be reached: ``/share/projects/<repo>/docs`` (repo docs) and
  ``/share/slab`` (the company wiki), and
* ``select`` is applied here, because the JSON-RPC API has no such parameter, and
* every doc comes back with the ``url`` to cite (see ``doc_links``), whatever
  ``select`` asks for, so the caller need not build one.

Every failure is returned as text rather than raised, so the model can react.
"""

import re
from typing import Any
from urllib.parse import quote

import httpx
import logfire

from tiger_agent.memory_engine.constants import (
    ME_API_KEY,
    ME_MAX_CONTENT_CHARS,
    ME_SEARCH_DEFAULT_LIMIT,
    ME_SEARCH_MAX_LIMIT,
    ME_SERVER,
    ME_TIMEOUT_SECONDS,
)

REPO_DOCS_TREE = "/share/projects/*/docs.*"
SLAB_TREE = "/share/slab/*"

# What a search returns when `select` is omitted: the triage fields for both
# corpora (see the skill), never the body.
DEFAULT_SEARCH_SELECT = [
    "tree",
    "name",
    "score",
    "meta.repo_path",
    "meta.doc",
    "meta.original_filename",
    "meta.slab_topic_path",
]

# A tree filter must stay inside the two corpora. The character set rules out the
# boolean and negation forms of the tree syntax, so a filter cannot be bent
# toward the much larger sibling trees (agent_sessions, git_history, ...).
_TREE_FILTER = re.compile(
    r"^(?:/share/projects/[A-Za-z0-9_\-*]+/docs(?:[./][A-Za-z0-9_\-./*{}]*)?"
    r"|/share/slab(?:[./][A-Za-z0-9_\-./*{}]*)?)$"
)
# Where a returned memory may live. Checked on every hit as a second line of
# defense behind the filter above.
_ALLOWED_MEMORY_TREE = re.compile(r"^/share/(?:projects/[^/]+/docs|slab)(?:/|$)")
_ALLOWED_MEMORY_PATH = re.compile(r"^/share/(?:projects/[^/]+/docs|slab)/.+$")
_REPO_DOCS_TREE = re.compile(r"^/share/projects/([^/]+)/docs(?:/|$)")
_SLAB_TREE = re.compile(r"^/share/slab(?:/|$)")
_CONTENT_SELECTOR = re.compile(r"^content(?::(\d+)(?::(\d*))?)?$")
# A space slug, e.g. "ahkgk250pmzh". It goes into a header, so keep it plain.
_SPACE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class MemoryEngineError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


async def _rpc(space: str, method: str, params: dict[str, Any]) -> Any:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {ME_API_KEY}",
        "X-Me-Space": space,
    }
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    with logfire.span("memory_engine_rpc", method=method, space=space):
        async with httpx.AsyncClient(timeout=ME_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{ME_SERVER}/api/v1/memory/rpc", json=payload, headers=headers
            )
    if response.status_code != 200:
        raise MemoryEngineError(
            f"HTTP {response.status_code}: {response.text[:300]}",
            code=f"HTTP_{response.status_code}",
        )
    body = response.json()
    error = body.get("error")
    if error:
        raise MemoryEngineError(
            error.get("message", "unknown error"),
            code=(error.get("data") or {}).get("code"),
        )
    return body["result"]


def _with_leading_slash(tree: str) -> str:
    return tree if tree.startswith("/") else f"/{tree}"


def _check_space(space: str | None) -> str | None:
    if not space or not _SPACE_SLUG.match(space):
        return (
            "`space` is required: pass the Memory Engine space slug the skill "
            f"gives you, not {space!r}."
        )
    return None


def _check_tree_filter(tree: str | None) -> str | None:
    if not tree:
        return (
            "`tree` is required. Use "
            f"`{REPO_DOCS_TREE}` for repo docs or `{SLAB_TREE}` for Slab."
        )
    if ".." in tree or "//" in tree or not _TREE_FILTER.match(tree):
        return (
            f"`tree` {tree!r} is outside the searchable docs. Use "
            f"`{REPO_DOCS_TREE}` (all repos), "
            "`/share/projects/<repo>/docs.*` (one repo) or "
            f"`{SLAB_TREE}` (Slab)."
        )
    return None


def _is_content_selector(field: str) -> bool:
    return field == "content" or field.startswith("content:")


def _check_select(select: list[str] | None) -> str | None:
    if select is None:
        return None
    if not select or any(not isinstance(field, str) or not field for field in select):
        return "`select` must be a non-empty list of field names."
    content_selectors = {field for field in select if _is_content_selector(field)}
    if any(not _CONTENT_SELECTOR.match(field) for field in content_selectors):
        return (
            "Invalid content selector. Use `content`, `content:N` (first N "
            "characters), `content:M:N` (characters M to N) or `content:M:`."
        )
    if len(content_selectors) > 1:
        return "`select` may contain only one content selector."
    return None


def _slice_content(content: str, selector: str) -> str:
    match = _CONTENT_SELECTOR.match(selector)
    assert match is not None
    first, second = match.group(1), match.group(2)
    if first is None:
        return content
    if second is None:
        return content[: int(first)]
    return content[int(first) : int(second)] if second else content[int(first) :]


def project_memory(memory: dict[str, Any], select: list[str] | None) -> dict[str, Any]:
    """The fields of one memory that `select` asks for.

    Without `select`, everything but the body. A body is always capped at
    ME_MAX_CONTENT_CHARS, with its full length reported so the caller can page
    through it with a `content:M:N` selector.
    """
    projected: dict[str, Any] = {}
    content_selector: str | None = None

    if select is None:
        projected = {k: v for k, v in memory.items() if k != "content"}
        content_selector = "content"
    else:
        meta = memory.get("meta") or {}
        for field in select:
            if _is_content_selector(field):
                content_selector = field
            elif field.startswith("meta."):
                key = field[len("meta.") :]
                if key in meta:
                    projected.setdefault("meta", {})[key] = meta[key]
            elif field in memory and field != "content":
                projected[field] = memory[field]

    content = memory.get("content")
    if content_selector is not None and isinstance(content, str):
        text = _slice_content(content, content_selector)
        projected["contentLength"] = len(content)
        if len(text) > ME_MAX_CONTENT_CHARS:
            text = text[:ME_MAX_CONTENT_CHARS]
            projected["contentTruncated"] = True
        projected["content"] = text
    return projected


def doc_links(memory: dict[str, Any]) -> dict[str, str]:
    """The link to cite for one doc, built from its full memory.

    Repo docs get a GitHub link. Its repo is the segment of `tree` after
    `/share/projects/` with the underscores turned back into hyphens, and
    `blob/HEAD` lets GitHub pick the default branch, so neither the branch nor the
    repo's casing has to be looked up (GitHub ignores the casing).

    Slab keeps no post id, so a Slab doc gets a search link on its file name, plus
    its topic trail when it has one. A `/posts/...` link cannot be built.

    Empty when the memory lacks the metadata, so the caller never cites a guess.
    """
    meta = memory.get("meta") or {}
    tree = _with_leading_slash(str(memory.get("tree") or ""))

    repo_match = _REPO_DOCS_TREE.match(tree)
    if repo_match:
        repo_path = meta.get("repo_path")
        if not isinstance(repo_path, str) or not repo_path:
            return {}
        repo = repo_match.group(1).replace("_", "-")
        return {
            "url": f"https://github.com/timescale/{repo}/blob/HEAD/"
            f"{quote(repo_path.lstrip('/'), safe='/')}"
        }

    if _SLAB_TREE.match(tree):
        filename = meta.get("original_filename")
        if not isinstance(filename, str) or not filename:
            return {}
        name = filename.removesuffix(".md")
        links = {"url": f"https://tigerdata.slab.com/search?q={quote(name)}"}
        topic = meta.get("slab_topic_path")
        if isinstance(topic, str) and topic:
            links["slabTopic"] = topic
        return links

    return {}


def _is_allowed(memory: dict[str, Any]) -> bool:
    tree = memory.get("tree")
    return isinstance(tree, str) and bool(
        _ALLOWED_MEMORY_TREE.match(_with_leading_slash(tree))
    )


async def me_memory_search(
    space: str,
    semantic: str | None = None,
    fulltext: str | None = None,
    tree: str | None = None,
    meta: dict[str, Any] | None = None,
    grep: str | None = None,
    limit: int | None = None,
    select: list[str] | None = None,
) -> dict[str, Any] | str:
    """Search the internal docs in Memory Engine.

    Args:
        space: Required. The Memory Engine space slug to search.
        semantic: Natural-language query for meaning-based search.
        fulltext: Keywords for exact matches: identifiers, errors, flags, alert names.
        tree: Required. The docs subtree to search.
        meta: Metadata filter, e.g. {"doc": {"type": "runbook"}}.
        grep: Case-insensitive regex on the content, alongside semantic or fulltext.
        limit: Maximum number of results.
        select: Fields to return for each hit, e.g. ["tree", "name", "meta.repo_path"].
    """
    if not ME_API_KEY:
        return "Memory Engine is not configured."
    for problem in (
        _check_space(space),
        _check_tree_filter(tree),
        _check_select(select),
    ):
        if problem:
            return problem

    params: dict[str, Any] = {
        "tree": tree,
        "limit": max(1, min(limit or ME_SEARCH_DEFAULT_LIMIT, ME_SEARCH_MAX_LIMIT)),
    }
    for key, value in (
        ("semantic", semantic),
        ("fulltext", fulltext),
        ("grep", grep),
        ("meta", meta),
    ):
        if value:
            params[key] = value

    try:
        result = await _rpc(space, "memory.search", params)
    except (MemoryEngineError, httpx.HTTPError) as e:
        return f"Memory Engine search failed: {e}"

    hits = [m for m in result.get("results", []) if _is_allowed(m)]
    dropped = len(result.get("results", [])) - len(hits)
    if dropped:
        logfire.warn("Dropped Memory Engine hits outside the docs trees", count=dropped)

    return {
        "results": [
            {
                **project_memory(
                    hit, select if select is not None else DEFAULT_SEARCH_SELECT
                ),
                **doc_links(hit),
            }
            for hit in hits
        ],
        "total": len(hits),
        "limit": params["limit"],
    }


async def me_memory_get_by_path(
    space: str,
    path: str,
    select: list[str] | None = None,
) -> dict[str, Any] | str:
    """Read one doc from Memory Engine by its `tree/name` path.

    Args:
        space: Required. The Memory Engine space slug to read from.
        path: The hit's `tree` + "/" + `name`, exactly as returned by a search.
        select: Fields to return. Omit for the whole doc. A body longer than the
            cap is cut, read the rest with a `content:M:N` selector.
    """
    if not ME_API_KEY:
        return "Memory Engine is not configured."
    path = _with_leading_slash((path or "").strip())
    if not _ALLOWED_MEMORY_PATH.match(path) or any(
        segment in ("", ".", "..") for segment in path[1:].split("/")
    ):
        return (
            f"`path` {path!r} is outside the readable docs. It must be under "
            "`/share/projects/<repo>/docs/` or `/share/slab/`: take it from a "
            "search hit as `tree` + '/' + `name`."
        )
    for problem in (_check_space(space), _check_select(select)):
        if problem:
            return problem

    try:
        memory = await _rpc(space, "memory.getByPath", {"path": path})
    except MemoryEngineError as e:
        if e.code == "NOT_FOUND":
            return (
                f"No doc at {path!r}. Build the path from a search hit as `tree` + "
                "'/' + `name`, exactly as returned: tree labels are normalized "
                "(lower-cased, underscores) while file names keep their hyphens."
            )
        return f"Memory Engine read failed: {e}"
    except httpx.HTTPError as e:
        return f"Memory Engine read failed: {e}"

    if not _is_allowed(memory):
        return f"No doc at {path!r}."
    return {**project_memory(memory, select), **doc_links(memory)}
