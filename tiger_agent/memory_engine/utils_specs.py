from typing import Any

import httpx
import pytest

from tiger_agent.memory_engine import utils as me
from tiger_agent.memory_engine.utils import (
    MemoryEngineError,
    me_memory_get_by_path,
    me_memory_search,
    project_memory,
)

SPACE = "space123"


async def search(**kwargs: Any) -> Any:
    return await me_memory_search(**{"space": SPACE, **kwargs})


async def read(path: str, **kwargs: Any) -> Any:
    return await me_memory_get_by_path(**{"space": SPACE, "path": path, **kwargs})


REPO_DOC = {
    "id": "019f",
    "content": "# Diagnose Backup Failure\n\nBackups run through pgBackRest.",
    "meta": {
        "doc": {"type": "runbook", "title": "Diagnose Backup Failure"},
        "repo_path": "docs/runbooks/backup-failure.md",
        "source": "docs",
    },
    "tree": "/share/projects/support-dev-collab/docs/docs/runbooks",
    "name": "backup-failure.md",
    "versionHash": "e568",
    "score": 0.03,
}
SLAB_DOC = {
    "id": "019e",
    "content": "Severity 1 means production is down.",
    "meta": {
        "original_filename": "Working High Severity Cases.md",
        "slab_topic_path": "Technical Services/Policies",
        "title": "Summary",
    },
    "tree": "/share/slab/technical_services/policies",
    "name": "working-high-severity-cases.md",
    "score": 0.5,
}
REPO_URL = (
    "https://github.com/timescale/support-dev-collab/blob/HEAD/"
    "docs/runbooks/backup-failure.md"
)
SLAB_LINKS = {
    "url": "https://tigerdata.slab.com/search?q=Working%20High%20Severity%20Cases",
    "slabTopic": "Technical Services/Policies",
}
OUTSIDE_DOC = {
    "id": "0190",
    "content": "a private agent session",
    "meta": {},
    "tree": "/share/projects/support-dev-collab/agent_sessions/x",
    "name": "session.md",
    "score": 0.9,
}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(me, "ME_API_KEY", "me.test.secret")
    monkeypatch.setattr(me, "ME_MAX_CONTENT_CHARS", 40)


@pytest.fixture
def rpc(monkeypatch):
    """Replace the HTTP call; `rpc.calls` records (method, params)."""

    class Fake:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []
            self.spaces: list[str] = []
            self.result: Any = {"results": []}
            self.error: Exception | None = None

    fake = Fake()

    async def _rpc(space: str, method: str, params: dict[str, Any]) -> Any:
        fake.spaces.append(space)
        fake.calls.append((method, params))
        if fake.error:
            raise fake.error
        return fake.result

    monkeypatch.setattr(me, "_rpc", _rpc)
    return fake


class TestSearchGuards:
    async def test_not_configured(self, monkeypatch, rpc):
        monkeypatch.setattr(me, "ME_API_KEY", "")
        assert "not configured" in await search(tree=me.SLAB_TREE)
        assert rpc.calls == []

    async def test_tree_is_required(self, rpc):
        result = await search(semantic="backups")
        assert "`tree` is required" in result
        assert rpc.calls == []

    @pytest.mark.parametrize(
        "tree",
        [
            "/share/projects/*/docs.*",
            "/share/projects/support-dev-collab/docs.*",
            "/share/projects/*/docs/*",
            "/share/projects/*/docs",
            "/share/slab/*",
            "/share/slab",
            "/share/slab/engineering/*",
        ],
    )
    async def test_allowed_trees_reach_the_api(self, rpc, tree):
        await search(semantic="x", tree=tree)
        assert rpc.calls[0][1]["tree"] == tree

    @pytest.mark.parametrize(
        "tree",
        [
            "/share/projects/*",
            "/share/projects/*/agent_sessions/*",
            "/share/projects/*/git_history.*",
            "/share/projects/*/docs_private/*",
            "/share/projects/x/docs/../../agent_sessions",
            "/share/slabx/*",
            "/share/*",
            "/home/me/*",
            "*/docs/*",
            "docs & runbooks",
            "/share/slab/!draft/*",
            "/share/projects/*/docs.* | /share/projects/*/agent_sessions.*",
        ],
    )
    async def test_trees_outside_the_docs_are_refused(self, rpc, tree):
        result = await search(semantic="x", tree=tree)
        assert "outside the searchable docs" in result
        assert rpc.calls == []

    @pytest.mark.parametrize("space", ["", "bad space", "a/b", "x\r\nHost: evil", None])
    async def test_a_missing_or_malformed_space_is_refused(self, rpc, space):
        result = await me_memory_search(space=space, semantic="x", tree=me.SLAB_TREE)
        assert "`space` is required" in result
        assert rpc.calls == []

    async def test_space_is_required_by_the_signature(self, rpc):
        with pytest.raises(TypeError):
            await me_memory_search(semantic="x", tree=me.SLAB_TREE)  # type: ignore[call-arg]

    @pytest.mark.parametrize("space", ["ahkgk250pmzh", "bxrois55diwt", "my-space_1"])
    async def test_the_space_the_caller_names_is_the_one_used(self, rpc, space):
        await search(space=space, semantic="x", tree=me.SLAB_TREE)
        assert rpc.spaces == [space]

    @pytest.mark.parametrize(
        "select",
        [
            [],
            [""],
            ["content:abc"],
            ["content:5", "content"],
            ["content:1", "content:2"],
        ],
    )
    async def test_invalid_select_is_refused(self, rpc, select):
        result = await search(semantic="x", tree=me.SLAB_TREE, select=select)
        assert isinstance(result, str)
        assert rpc.calls == []


class TestSearchRequest:
    async def test_only_given_criteria_are_sent(self, rpc):
        await search(
            semantic="backups",
            fulltext="pgbackrest",
            tree=me.REPO_DOCS_TREE,
            meta={"doc": {"type": "runbook"}},
            grep="stanza",
            limit=5,
        )
        method, params = rpc.calls[0]
        assert method == "memory.search"
        assert params == {
            "semantic": "backups",
            "fulltext": "pgbackrest",
            "tree": me.REPO_DOCS_TREE,
            "meta": {"doc": {"type": "runbook"}},
            "grep": "stanza",
            "limit": 5,
        }

    async def test_empty_criteria_are_left_out(self, rpc):
        await search(semantic="", fulltext=None, tree=me.SLAB_TREE, meta={}, grep="")
        assert rpc.calls[0][1] == {"tree": me.SLAB_TREE, "limit": 10}

    @pytest.mark.parametrize(
        "requested,sent", [(None, 10), (0, 10), (-5, 1), (1000, 25), (7, 7)]
    )
    async def test_limit_is_clamped(self, rpc, requested, sent):
        await search(semantic="x", tree=me.SLAB_TREE, limit=requested)
        assert rpc.calls[0][1]["limit"] == sent


class TestSearchResults:
    async def test_default_projection_is_triage_fields_without_the_body(self, rpc):
        rpc.result = {"results": [REPO_DOC, SLAB_DOC]}
        result = await search(semantic="x", tree=me.REPO_DOCS_TREE)
        assert result["total"] == 2
        assert result["results"][0] == {
            "tree": REPO_DOC["tree"],
            "name": "backup-failure.md",
            "score": 0.03,
            "meta": {
                "repo_path": "docs/runbooks/backup-failure.md",
                "doc": REPO_DOC["meta"]["doc"],
            },
            "url": REPO_URL,
        }
        assert result["results"][1]["meta"] == {
            "original_filename": "Working High Severity Cases.md",
            "slab_topic_path": "Technical Services/Policies",
        }
        for hit in result["results"]:
            assert "content" not in hit

    async def test_select_picks_fields(self, rpc):
        rpc.result = {"results": [SLAB_DOC]}
        result = await search(
            semantic="x",
            tree=me.SLAB_TREE,
            select=["name", "meta.title", "meta.missing"],
        )
        assert result["results"] == [
            {
                "name": "working-high-severity-cases.md",
                "meta": {"title": "Summary"},
                **SLAB_LINKS,
            }
        ]

    async def test_content_slice_reports_the_full_length(self, rpc):
        rpc.result = {"results": [REPO_DOC]}
        result = await search(
            semantic="x", tree=me.REPO_DOCS_TREE, select=["name", "content:11"]
        )
        hit = result["results"][0]
        assert hit["content"] == "# Diagnose "
        assert hit["contentLength"] == len(REPO_DOC["content"])

    async def test_hits_outside_the_docs_trees_are_dropped(self, rpc):
        rpc.result = {"results": [OUTSIDE_DOC, REPO_DOC]}
        result = await search(semantic="x", tree=me.REPO_DOCS_TREE)
        assert [h["name"] for h in result["results"]] == ["backup-failure.md"]
        assert result["total"] == 1

    async def test_api_error_is_returned_as_text(self, rpc):
        rpc.error = MemoryEngineError("Invalid params", code="VALIDATION_ERROR")
        result = await search(semantic="x", tree=me.SLAB_TREE)
        assert result == "Memory Engine search failed: Invalid params"

    async def test_network_error_is_returned_as_text(self, rpc):
        rpc.error = httpx.ConnectError("boom")
        result = await search(semantic="x", tree=me.SLAB_TREE)
        assert result.startswith("Memory Engine search failed")


class TestLinks:
    async def test_search_hits_carry_the_link_whatever_select_asks_for(self, rpc):
        rpc.result = {"results": [REPO_DOC, SLAB_DOC]}
        result = await search(semantic="x", tree=me.REPO_DOCS_TREE, select=["name"])
        assert result["results"] == [
            {"name": "backup-failure.md", "url": REPO_URL},
            {"name": "working-high-severity-cases.md", **SLAB_LINKS},
        ]

    async def test_a_doc_read_carries_the_link(self, rpc):
        rpc.result = REPO_DOC
        result = await read(TestGetByPath.PATH, select=["name"])
        assert result == {"name": "backup-failure.md", "url": REPO_URL}

    def test_repo_link_turns_the_normalized_repo_back_into_hyphens(self):
        doc = {
            "tree": "/share/projects/timescaledb_enterprise_onprem/docs/docs",
            "meta": {"repo_path": "docs/ops/restore guide.md"},
        }
        assert me.doc_links(doc) == {
            "url": "https://github.com/timescale/timescaledb-enterprise-onprem"
            "/blob/HEAD/docs/ops/restore%20guide.md"
        }

    def test_a_tree_without_a_leading_slash_still_links(self):
        doc = {"tree": "share/projects/a_b/docs", "meta": {"repo_path": "docs/x.md"}}
        assert me.doc_links(doc)["url"].endswith("/timescale/a-b/blob/HEAD/docs/x.md")

    def test_slab_without_a_topic_has_no_trail(self):
        doc = {
            "tree": "/share/slab/uncategorized",
            "meta": {"original_filename": "Notes.md", "slab_topic_path": ""},
        }
        assert me.doc_links(doc) == {"url": "https://tigerdata.slab.com/search?q=Notes"}

    @pytest.mark.parametrize(
        "doc",
        [
            {"tree": "/share/projects/r/docs/docs", "meta": {}},
            {"tree": "/share/projects/r/docs/docs"},
            {"tree": "/share/slab/x", "meta": {}},
            {"tree": "/share/projects/r/agent_sessions/x", "meta": {"repo_path": "a"}},
            {},
        ],
    )
    def test_no_metadata_means_no_link_rather_than_a_guess(self, doc):
        assert me.doc_links(doc) == {}

    def test_a_slab_link_is_never_a_posts_link(self):
        links = me.doc_links(SLAB_DOC)
        assert "/posts/" not in links["url"]


class TestGetByPath:
    PATH = "/share/projects/support-dev-collab/docs/docs/runbooks/backup-failure.md"

    async def test_reads_the_whole_doc(self, rpc):
        rpc.result = {**REPO_DOC, "content": "short"}
        result = await read(self.PATH)
        assert rpc.calls == [("memory.getByPath", {"path": self.PATH})]
        assert result["content"] == "short"
        assert result["contentLength"] == 5
        assert result["meta"]["repo_path"] == "docs/runbooks/backup-failure.md"
        assert "contentTruncated" not in result

    async def test_a_leading_slash_is_added(self, rpc):
        rpc.result = REPO_DOC
        await read(self.PATH.lstrip("/"))
        assert rpc.calls[0][1] == {"path": self.PATH}

    async def test_long_body_is_cut_and_can_be_paged(self, rpc):
        rpc.result = {**REPO_DOC, "content": "x" * 100}
        cut = await read(self.PATH)
        assert len(cut["content"]) == 40
        assert cut["contentTruncated"] is True
        assert cut["contentLength"] == 100

        rest = await read(self.PATH, select=["content:40:"])
        assert len(rest["content"]) == 40
        assert rest["contentTruncated"] is True

        window = await read(self.PATH, select=["content:90:100"])
        assert window["content"] == "x" * 10
        assert "contentTruncated" not in window

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "/share/projects/support-dev-collab/agent_sessions/x/session.md",
            "/share/projects/support-dev-collab/docs",
            "/share/slab",
            "/share/slab/",
            "/home/me/notes/todo",
            "/share/slab/a/../../projects/x/agent_sessions/y",
            "/share/slab//a",
        ],
    )
    async def test_paths_outside_the_docs_are_refused(self, rpc, path):
        result = await read(path)
        assert "outside the readable docs" in result
        assert rpc.calls == []

    async def test_slab_path_is_allowed(self, rpc):
        rpc.result = SLAB_DOC
        result = await read(
            "/share/slab/technical_services/policies/working-high-severity-cases.md"
        )
        assert result["name"] == "working-high-severity-cases.md"

    async def test_not_found_explains_how_to_build_the_path(self, rpc):
        rpc.error = MemoryEngineError("Memory not found", code="NOT_FOUND")
        result = await read(self.PATH)
        assert "No doc at" in result
        assert "`tree` + '/' + `name`" in result

    async def test_other_errors_are_returned_as_text(self, rpc):
        rpc.error = MemoryEngineError("HTTP 502: bad gateway", code="HTTP_502")
        result = await read(self.PATH)
        assert result == "Memory Engine read failed: HTTP 502: bad gateway"

    async def test_a_memory_outside_the_docs_trees_is_not_returned(self, rpc):
        rpc.result = OUTSIDE_DOC
        result = await read(self.PATH)
        assert result.startswith("No doc at")

    async def test_a_missing_or_malformed_space_is_refused(self, rpc):
        for space in ("", "bad space", None):
            result = await me_memory_get_by_path(space=space, path=self.PATH)
            assert "`space` is required" in result
        assert rpc.calls == []

    async def test_the_space_the_caller_names_is_the_one_used(self, rpc):
        rpc.result = REPO_DOC
        await read(self.PATH, space="ahkgk250pmzh")
        assert rpc.spaces == ["ahkgk250pmzh"]


class TestProjectMemory:
    def test_without_select_everything_but_the_body_plus_the_capped_body(self):
        projected = project_memory(REPO_DOC, None)
        assert projected["id"] == "019f"
        assert projected["versionHash"] == "e568"
        assert projected["content"] == REPO_DOC["content"][:40]
        assert projected["contentTruncated"] is True

    def test_content_selector_forms(self):
        memory = {"content": "0123456789"}
        assert project_memory(memory, ["content:4"])["content"] == "0123"
        assert project_memory(memory, ["content:2:5"])["content"] == "234"
        assert project_memory(memory, ["content:7:"])["content"] == "789"
        assert project_memory(memory, ["content"])["content"] == "0123456789"

    def test_a_missing_body_is_not_invented(self):
        assert project_memory({"name": "x"}, ["name", "content:5"]) == {"name": "x"}


class TestRpc:
    @pytest.fixture
    def post(self, monkeypatch):
        sent: dict[str, Any] = {}
        response = httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json, headers):
                sent.update(url=url, json=json, headers=headers)
                return sent["response"]

        sent["response"] = response
        monkeypatch.setattr(me.httpx, "AsyncClient", FakeClient)
        return sent

    async def test_request_shape(self, post):
        assert (
            await me._rpc("space123", "memory.search", {"tree": "/share/slab/*"}) == {}
        )
        assert post["url"].endswith("/api/v1/memory/rpc")
        assert post["json"]["method"] == "memory.search"
        assert post["json"]["params"] == {"tree": "/share/slab/*"}
        assert post["headers"]["Authorization"] == "Bearer me.test.secret"
        assert post["headers"]["X-Me-Space"] == "space123"

    async def test_jsonrpc_error_carries_its_code(self, post):
        post["response"] = httpx.Response(
            200,
            json={
                "error": {"message": "Memory not found", "data": {"code": "NOT_FOUND"}}
            },
        )
        with pytest.raises(MemoryEngineError) as exc:
            await me._rpc("space123", "memory.getByPath", {"path": "/share/slab/x"})
        assert exc.value.code == "NOT_FOUND"
        assert "Memory not found" in str(exc.value)

    async def test_http_error_never_echoes_the_key(self, post):
        post["response"] = httpx.Response(401, text="unauthorized")
        with pytest.raises(MemoryEngineError) as exc:
            await me._rpc("space123", "memory.search", {})
        assert exc.value.code == "HTTP_401"
        assert "me.test.secret" not in str(exc.value)
