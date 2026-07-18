import json
from unittest.mock import AsyncMock

import pytest

import wiki_mcp_server as w
from tests._helpers import tool_fn


@pytest.mark.asyncio
async def test_upload_rejects_missing_file():
    result = json.loads(await tool_fn(w.wikijs_upload_asset)("/no/such/file.txt"))
    assert "error" in result
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(w.settings, "WIKIJS_MAX_UPLOAD_BYTES", 10)
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100)
    result = json.loads(await tool_fn(w.wikijs_upload_asset)(str(f)))
    assert "error" in result
    assert result["file_size_bytes"] == 100
    assert result["limit_bytes"] == 10


@pytest.mark.asyncio
async def test_upload_rejects_unknown_folder_id(tmp_path, monkeypatch):
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    monkeypatch.setattr(
        w,
        "_get_all_asset_folder_ids",
        AsyncMock(return_value={1: {"id": 1, "name": "Real Folder", "slug": "real"}}),
    )
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"fake image bytes")

    result = json.loads(await tool_fn(w.wikijs_upload_asset)(str(f), folder_id=999))

    assert "error" in result
    assert "999" in result["error"]
    assert result["available_folders"] == [
        {"id": 1, "name": "Real Folder", "slug": "real"}
    ]


@pytest.mark.asyncio
async def test_upload_succeeds_and_identifies_new_asset_by_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    before = {"assets": [{"id": 1, "filename": "existing.jpg"}], "total": 1}
    after = {
        "assets": [
            {"id": 1, "filename": "existing.jpg"},
            {
                "id": 2,
                "filename": "photo.jpg",
                "mime": "image/jpeg",
                "fileSize": 17,
                "kind": "IMAGE",
            },
        ],
        "total": 2,
    }
    monkeypatch.setattr(
        w,
        "wikijs_list_assets",
        AsyncMock(side_effect=[json.dumps(before), json.dumps(after)]),
    )
    monkeypatch.setattr(w.wikijs, "upload_asset", AsyncMock(return_value="ok"))

    f = tmp_path / "photo.jpg"
    f.write_bytes(b"fake image bytes")

    result = json.loads(await tool_fn(w.wikijs_upload_asset)(str(f)))

    assert result["verified"] is True
    assert result["id"] == 2
    assert result["filename"] == "photo.jpg"
    assert result["kind"] == "IMAGE"


@pytest.mark.asyncio
async def test_upload_preexisting_same_name_asset_not_mistaken_for_new(
    tmp_path, monkeypatch
):
    """Regression test for the stale-asset-collision bug: a pre-existing asset
    with the same filename must NOT be returned as the upload result."""
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    stale_listing = {
        "assets": [
            {"id": 1, "filename": "photo.jpg", "mime": "image/png", "fileSize": 999}
        ],
        "total": 1,
    }
    monkeypatch.setattr(
        w,
        "wikijs_list_assets",
        AsyncMock(side_effect=[json.dumps(stale_listing), json.dumps(stale_listing)]),
    )
    monkeypatch.setattr(w.wikijs, "upload_asset", AsyncMock(return_value="ok"))

    f = tmp_path / "photo.jpg"
    f.write_bytes(b"new content")

    result = json.loads(await tool_fn(w.wikijs_upload_asset)(str(f)))

    assert result["verified"] is False
    assert result["id"] is None  # must NOT be 1 (the stale asset's id)
