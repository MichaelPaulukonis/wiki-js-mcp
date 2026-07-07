# tests/test_tool_cross_calls.py
import json
from unittest.mock import AsyncMock

import wiki_mcp_server as w
from tests._helpers import tool_fn


def _mock_wikijs(monkeypatch, graphql_response):
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    monkeypatch.setattr(
        w.wikijs, "graphql_request", AsyncMock(return_value=graphql_response)
    )


def _assert_not_broken_by_fastmcp(data: dict):
    """A regression to FunctionTool-wrapping (fastmcp 2.x-style) manifests as
    a caught exception whose message contains this text, since every tool
    wraps its body in try/except Exception rather than letting it raise."""
    if "error" in data:
        assert (
            "not callable" not in data["error"]
        ), f"fastmcp regression present: {data}"


async def test_create_space_calls_create_page(monkeypatch):
    _mock_wikijs(
        monkeypatch,
        {
            "data": {
                "pages": {
                    "create": {
                        "responseResult": {"succeeded": True, "message": None},
                        "page": {"id": 42, "path": "my-space", "title": "My Space"},
                    }
                }
            }
        },
    )
    result = json.loads(await tool_fn(w.wikijs_create_space)("My Space", "desc"))
    _assert_not_broken_by_fastmcp(result)
    assert result["spaceId"] == 42


async def test_sync_file_docs_calls_get_page_and_update_page(monkeypatch, tmp_path):
    get_response = {
        "data": {
            "pages": {
                "single": {
                    "id": 7,
                    "path": "p",
                    "title": "T",
                    "content": "old",
                    "description": "",
                    "isPrivate": False,
                    "isPublished": True,
                    "locale": "en",
                    "createdAt": None,
                    "updatedAt": None,
                    "tags": [],
                }
            }
        }
    }
    update_response = {
        "data": {
            "pages": {
                "update": {
                    "responseResult": {"succeeded": True, "message": None},
                    "page": {"id": 7, "path": "p", "title": "T", "updatedAt": "now"},
                }
            }
        }
    }
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    # wikijs_sync_file_docs calls wikijs_get_page (1 graphql call), which then
    # calls wikijs_update_page, which itself does its own "get current page"
    # query (2nd call, same shape as get_response) before the update mutation
    # (3rd call) -- so get_response is consumed twice.
    monkeypatch.setattr(
        w.wikijs,
        "graphql_request",
        AsyncMock(side_effect=[get_response, get_response, update_response]),
    )

    f = tmp_path / "foo.py"
    f.write_text("x = 1\n")
    db = w.get_db()
    db.add(
        w.FileMapping(
            file_path=str(f), page_id=7, relationship_type="documents", file_hash=""
        )
    )
    db.commit()

    result = json.loads(await tool_fn(w.wikijs_sync_file_docs)(str(f), "did a thing"))
    _assert_not_broken_by_fastmcp(result)
    assert result["updated"] is True


async def test_generate_file_overview_calls_create_page_and_link(monkeypatch, tmp_path):
    create_response = {
        "data": {
            "pages": {
                "create": {
                    "responseResult": {"succeeded": True, "message": None},
                    "page": {"id": 99, "path": "foo-py-documentation", "title": "T"},
                }
            }
        }
    }
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    monkeypatch.setattr(
        w.wikijs, "graphql_request", AsyncMock(return_value=create_response)
    )

    f = tmp_path / "foo.py"
    f.write_text("def bar():\n    pass\n")

    result = json.loads(await tool_fn(w.wikijs_generate_file_overview)(str(f)))
    _assert_not_broken_by_fastmcp(result)
    assert result["action"] == "created"
    assert result["pageId"] == 99


async def test_create_repo_structure_calls_create_page(monkeypatch):
    create_response = {
        "data": {
            "pages": {
                "create": {
                    "responseResult": {"succeeded": True, "message": None},
                    "page": {"id": 1, "path": "repo", "title": "repo"},
                }
            }
        }
    }
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    monkeypatch.setattr(
        w.wikijs, "graphql_request", AsyncMock(return_value=create_response)
    )
    result = json.loads(
        await tool_fn(w.wikijs_create_repo_structure)("repo", "desc", ["Overview"])
    )
    _assert_not_broken_by_fastmcp(result)
    assert result["status"] == "created"


async def test_create_nested_page_calls_create_page(monkeypatch):
    parent_query_response = {
        "data": {"pages": {"singleByPath": {"id": 5, "path": "repo/api", "title": "T"}}}
    }
    # wikijs_create_nested_page finds the parent, then calls wikijs_create_page
    # with parent_id="5"; since parent_id is truthy, wikijs_create_page issues
    # its own "single(id: 5)" lookup to build the nested path before the
    # create mutation -- 3 graphql calls total.
    parent_lookup_by_id_response = {
        "data": {"pages": {"single": {"path": "repo/api", "title": "T"}}}
    }
    create_response = {
        "data": {
            "pages": {
                "create": {
                    "responseResult": {"succeeded": True, "message": None},
                    "page": {"id": 6, "path": "repo/api/endpoints", "title": "T"},
                }
            }
        }
    }
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    monkeypatch.setattr(
        w.wikijs,
        "graphql_request",
        AsyncMock(
            side_effect=[
                parent_query_response,
                parent_lookup_by_id_response,
                create_response,
            ]
        ),
    )
    result = json.loads(
        await tool_fn(w.wikijs_create_nested_page)("Endpoints", "content", "repo/api")
    )
    _assert_not_broken_by_fastmcp(result)
    assert "error" not in result


async def test_batch_delete_pages_calls_delete_page(monkeypatch):
    # wikijs_batch_delete_pages(page_ids=[1]) first looks up each id via its
    # own "single" query, then calls wikijs_delete_page(page_id=1), which
    # does its own "single" lookup again before the delete mutation --
    # single_response is consumed twice, followed by delete_response.
    single_response = {
        "data": {"pages": {"single": {"id": 1, "path": "a", "title": "A"}}}
    }
    delete_response = {
        "data": {
            "pages": {
                "delete": {
                    "responseResult": {"succeeded": True, "message": None},
                }
            }
        }
    }
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    monkeypatch.setattr(
        w.wikijs,
        "graphql_request",
        AsyncMock(side_effect=[single_response, single_response, delete_response]),
    )
    result = json.loads(
        await tool_fn(w.wikijs_batch_delete_pages)(page_ids=[1], confirm_deletion=True)
    )
    _assert_not_broken_by_fastmcp(result)
    assert result["deleted_count"] == 1
