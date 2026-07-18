import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import tenacity

import wiki_mcp_server as w


def _fake_response(text="ok", status_code=200):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    if status_code >= 400:
        request = httpx.Request("POST", "http://test.invalid/u")
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error",
            request=request,
            response=httpx.Response(status_code, request=request, text=text),
        )
    else:
        resp.raise_for_status.side_effect = None
    return resp


@pytest.mark.asyncio
async def test_upload_asset_reuses_shared_client_no_content_type_override(monkeypatch):
    """The whole point of Task 1: the shared client must not carry a stale
    Content-Type default that would break multipart encoding."""
    assert "content-type" not in {k.lower() for k in w.wikijs.client.headers.keys()}

    post_mock = AsyncMock(return_value=_fake_response())
    monkeypatch.setattr(w.wikijs.client, "post", post_mock)

    result = await w.wikijs.upload_asset(0, "test.txt", b"hello")

    assert result == "ok"
    _, kwargs = post_mock.call_args
    assert "Content-Type" not in kwargs.get("headers", {})
    field_names = [part[0] for part in kwargs["files"]]
    assert field_names == ["mediaUpload", "mediaUpload"]


@pytest.mark.asyncio
async def test_upload_asset_retries_on_connection_error(monkeypatch):
    # monkeypatch.setattr (not raw assignment) so the retry config auto-reverts
    # after this test instead of leaking into later tests sharing this singleton.
    monkeypatch.setattr(w.wikijs.upload_asset.retry, "wait", tenacity.wait_none())

    post_mock = AsyncMock(side_effect=[httpx.ConnectError("boom"), _fake_response()])
    monkeypatch.setattr(w.wikijs.client, "post", post_mock)

    result = await w.wikijs.upload_asset(0, "test.txt", b"hello")

    assert result == "ok"
    assert post_mock.call_count == 2


@pytest.mark.asyncio
async def test_upload_asset_http_error_preserves_response_body(monkeypatch):
    monkeypatch.setattr(
        w.wikijs.upload_asset.retry, "stop", tenacity.stop_after_attempt(1)
    )

    post_mock = AsyncMock(
        return_value=_fake_response(text="folder does not exist", status_code=422)
    )
    monkeypatch.setattr(w.wikijs.client, "post", post_mock)

    with pytest.raises(Exception) as exc_info:
        await w.wikijs.upload_asset(0, "test.txt", b"hello")

    assert "folder does not exist" in str(exc_info.value)
    assert "422" in str(exc_info.value)
