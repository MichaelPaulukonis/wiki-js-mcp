# Asset Tools Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 10 code-level findings from the adversarial review of the `asset-tools` PR (false-positive upload success, stale-asset misidentification, guessed filename sanitization, no retry/error-detail on upload, upload bypassing the shared client, missing env var docs, inconsistent response shapes).

**Architecture:** One root cause explains 3 of the "upload" findings at once: `WikiJSClient.authenticate()` sets a client-level `Content-Type: application/json` default header, which leaks into *every* request built from `wikijs.client` — including multipart uploads, where it silently overrides httpx's automatic `multipart/form-data; boundary=...` header (verified empirically: a client-level header wins over the one httpx would auto-generate from `files=`). That's *why* the original code built a second throwaway `httpx.AsyncClient` for uploads. Fix: stop setting `Content-Type` at the client level at all — `graphql_request`'s `json=` parameter already auto-sets `application/json` per request, so nothing depends on the client default — then add a proper `WikiJSClient.upload_asset()` method (with the same `@retry` and `httpx.HTTPStatusError`/`RequestError` handling `graphql_request` already has) that reuses the shared, pooled client. Separately, folder-ID validation is restored (correctly, walking the full folder tree instead of the old root-only check that caused it to be deleted), and the post-upload "which asset did I just create" step is redesigned from a fragile filename-sanitization guess to a before/after asset-ID diff, which also unifies the two divergent response shapes into one.

**Tech Stack:** Python 3.12, fastmcp>=3.4.2 (verified: `@mcp.tool()` returns a plain callable on this version, so tools can call each other directly by name with no wrapper indirection), httpx, tenacity, pytest, pytest-asyncio, unittest.mock.

**Branch:** Continue on the existing `asset-tools` branch (already checked out at the repo root, tracking `origin/asset-tools`). **Prerequisite:** `docs/superpowers/plans/2026-07-04-fastmcp-tool-callable-fix.md` must be merged to `main` and merged into `asset-tools` first — it pins `fastmcp>=3.4.2,<4.0.0` and adds `conftest.py`/`tests/_helpers.py` this plan reuses. (Earlier drafts of this plan extracted `_<name>_impl` wrapper functions so `wikijs_upload_asset` could call `wikijs_list_assets` internally without hitting a `FunctionTool`-not-callable bug on fastmcp 2.x — that extraction turned out to be unnecessary once the version pin was corrected to target the working 3.x line, so this plan calls `wikijs_list_asset_folders`/`wikijs_list_assets` directly.)

---

## Task 0: Confirm the prerequisite fix is present

**Files:** none (verification only)

- [ ] **Step 1: Check the fastmcp fix landed on this branch**

```bash
cd /Users/michaelpaulukonis/projects/wiki-js-mcp
git log --oneline main | grep -i "fastmcp" | head -5
git log --oneline asset-tools | grep -i "fastmcp" | head -5
```
Expected: the "pin fastmcp to verified-working 3.x" commit appears in both. If it doesn't appear in `asset-tools`, run:
```bash
git checkout asset-tools
git fetch origin
git merge origin/main
```
before proceeding, then reinstall in this branch's venv (`pip install -r requirements.txt`) and confirm `python3 -c "import fastmcp; print(fastmcp.__version__)"` prints a 3.x version.

- [ ] **Step 2: Confirm test infra from that plan exists**

```bash
ls conftest.py tests/_helpers.py tests/test_tool_cross_calls.py
```
Expected: all three exist. If not, stop and merge the prerequisite plan first.

---

## Task 1: Stop leaking a stale Content-Type into every request; add a proper upload method to WikiJSClient

**Files:** Modify `src/wiki_mcp_server.py`

- [ ] **Step 1: Remove `Content-Type` from the token-auth branch of `authenticate()`**

Find (around line 111-121):
```python
    async def authenticate(self) -> bool:
        """Set up authentication headers for GraphQL requests."""
        if settings.token:
            self.client.headers.update(
                {
                    "Authorization": f"Bearer {settings.token}",
                    "Content-Type": "application/json",
                }
            )
            self.authenticated = True
            return True
```
Replace with:
```python
    async def authenticate(self) -> bool:
        """Set up authentication headers for requests.

        Deliberately does NOT set a client-level Content-Type: httpx already sets
        the correct Content-Type per request from the json= or files= parameter
        (application/json for graphql_request, multipart/form-data;boundary=... for
        upload_asset). A client-level Content-Type default would override both.
        """
        if settings.token:
            self.client.headers.update({"Authorization": f"Bearer {settings.token}"})
            self.authenticated = True
            return True
```

- [ ] **Step 2: Remove `Content-Type` from the username/password branch**

Find (around line 151-157):
```python
                    jwt_token = response["data"]["authentication"]["login"]["jwt"]
                    self.client.headers.update(
                        {
                            "Authorization": f"Bearer {jwt_token}",
                            "Content-Type": "application/json",
                        }
                    )
                    self.authenticated = True
                    return True
```
Replace with:
```python
                    jwt_token = response["data"]["authentication"]["login"]["jwt"]
                    self.client.headers.update({"Authorization": f"Bearer {jwt_token}"})
                    self.authenticated = True
                    return True
```

- [ ] **Step 3: Add `upload_asset()` method to `WikiJSClient`, after `graphql_request`**

Find the end of `graphql_request` (around line 200-202):
```python
        except httpx.RequestError as e:
            logger.error(f"Wiki.js connection error: {str(e)}")
            raise Exception(f"Wiki.js connection error: {str(e)}")


# Initialize client
```
Replace with:
```python
        except httpx.RequestError as e:
            logger.error(f"Wiki.js connection error: {str(e)}")
            raise Exception(f"Wiki.js connection error: {str(e)}")

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def upload_asset(self, folder_id: int, filename: str, fileobj) -> str:
        """Upload a file via Wiki.js's multipart /u endpoint. Returns the raw response text.

        Wiki.js's upload route uses multer().array('mediaUpload'): a text part named
        'mediaUpload' carries JSON metadata {"folderId": N}, and a second part with the
        same field name carries the actual file; multer tells them apart by whether the
        part has a filename (Content-Disposition).
        """
        url = f"{self.base_url}/u"
        try:
            response = await self.client.post(
                url,
                files=[
                    (
                        "mediaUpload",
                        (None, json.dumps({"folderId": folder_id}), "text/plain"),
                    ),
                    ("mediaUpload", (filename, fileobj)),
                ],
            )
            response.raise_for_status()
            return response.text.strip()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Wiki.js upload HTTP error {e.response.status_code}: {e.response.text}"
            )
            raise Exception(
                f"Wiki.js upload HTTP error {e.response.status_code}: {e.response.text}"
            )
        except httpx.RequestError as e:
            logger.error(f"Wiki.js upload connection error: {str(e)}")
            raise Exception(f"Wiki.js upload connection error: {str(e)}")


# Initialize client
```

- [ ] **Step 4: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "fix: stop leaking Content-Type default into multipart uploads, add WikiJSClient.upload_asset()"
```

---

## Task 2: Simplify `wikijs_list_assets`'s kind validation

Small cleanup finding from the review, unrelated to the impl-extraction question — same behavior, one `.upper()` call instead of two.

**Files:** Modify `src/wiki_mcp_server.py`

- [ ] **Step 1: Simplify the double-`.upper()` branch**

Find:
```python
        valid_kinds = {"ALL", "IMAGE", "BINARY", "DOCUMENT"}
        if kind.upper() not in valid_kinds:
            kind = "ALL"
        else:
            kind = kind.upper()
```
Replace with:
```python
        valid_kinds = {"ALL", "IMAGE", "BINARY", "DOCUMENT"}
        kind_upper = kind.upper()
        kind = kind_upper if kind_upper in valid_kinds else "ALL"
```

- [ ] **Step 2: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "style: simplify kind validation in wikijs_list_assets to one .upper() call"
```

---

## Task 3: Add recursive folder-ID validation helper

The PR's original folder check (removed in commit `d570f62`) only queried `folders(parentFolderId: 0)` — root-level children — so it falsely rejected valid nested folder IDs, which is *why* it was deleted with nothing put in its place. This walks the whole tree instead.

**Files:** Modify `src/wiki_mcp_server.py`

- [ ] **Step 1: Add the helper function, directly above `wikijs_list_asset_folders`**

Find:
```python
@mcp.tool()
async def wikijs_list_asset_folders(parent_folder_id: int = 0) -> str:
```
Replace with:
```python
async def _get_all_asset_folder_ids() -> Dict[int, Dict[str, Any]]:
    """Recursively enumerate every asset folder in the tree.

    Wiki.js's assets.folders query only returns direct children of a given
    parentFolderId, not the whole tree - so validating a folder_id against just
    folders(parentFolderId=0) (the root level) falsely rejects valid nested
    folders. This walks the full tree via BFS instead.
    """
    all_folders: Dict[int, Dict[str, Any]] = {}
    to_visit = [0]
    visited_parents = set()
    while to_visit:
        parent_id = to_visit.pop()
        if parent_id in visited_parents:
            continue
        visited_parents.add(parent_id)
        response = await wikijs_list_asset_folders(parent_folder_id=parent_id)
        folders = json.loads(response).get("folders", [])
        for folder in folders:
            all_folders[folder["id"]] = folder
            to_visit.append(folder["id"])
    return all_folders


@mcp.tool()
async def wikijs_list_asset_folders(parent_folder_id: int = 0) -> str:
```

- [ ] **Step 2: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "feat: add recursive asset-folder-tree lookup for upload validation"
```

---

## Task 4: Rewrite `wikijs_upload_asset`

Replaces the filename-sanitization guess with a before/after asset-ID diff (fixes stale-asset misattribution and the incomplete-sanitization false-negative in one change), restores folder-ID validation using the Task 3 helper (fixes the false-positive-success bug and makes the docstring's `available_folders` promise true again), and unifies the two response shapes into one.

**Files:** Modify `src/wiki_mcp_server.py`

- [ ] **Step 1: Replace the whole function**

Find (the entire current `wikijs_upload_asset`, from `@mcp.tool()` through its final `except` block — see `src/wiki_mcp_server.py:2415-2533` on this branch before this edit):
```python
@mcp.tool()
async def wikijs_upload_asset(file_path: str, folder_id: int = 0) -> str:
    """
    Upload a local file to Wiki.js assets.

    Args:
        file_path: Absolute path to the local file
        folder_id: Target folder ID (0 = root). Use wikijs_list_asset_folders to find folder IDs.

    Returns:
        JSON string: {"id": int, "filename": str, "mime": str, "fileSize": int, "kind": str}
        On folder-not-found: {"error": str, "available_folders": [...]}
    """
    try:
        if not os.path.isfile(file_path):
            return json.dumps(
                {"error": f"File not found or is not a file: {file_path}"}
            )

        file_size = os.path.getsize(file_path)
        if file_size > settings.WIKIJS_MAX_UPLOAD_BYTES:
            max_mb = settings.WIKIJS_MAX_UPLOAD_BYTES / 1_048_576
            actual_mb = file_size / 1_048_576
            return json.dumps(
                {
                    "error": f"File size {actual_mb:.1f}MB exceeds limit of {max_mb:.0f}MB",
                    "file_size_bytes": file_size,
                    "limit_bytes": settings.WIKIJS_MAX_UPLOAD_BYTES,
                }
            )

        await wikijs.authenticate()

        upload_url = f"{wikijs.base_url}/u"
        filename = os.path.basename(file_path)

        # Wiki.js upload route uses multer().array('mediaUpload'):
        # - file part named 'mediaUpload' is the actual file
        # - text part named 'mediaUpload' is JSON metadata {"folderId": N}
        # Both parts share the same field name; multer distinguishes by Content-Disposition filename.
        # Use a dedicated client: wikijs.client has Content-Type: application/json set by default,
        # which conflicts with the multipart/form-data Content-Type httpx generates for file uploads.
        auth_header = wikijs.client.headers.get("Authorization", "")
        with open(file_path, "rb") as f:
            async with httpx.AsyncClient(timeout=30.0) as upload_client:
                response = await upload_client.post(
                    upload_url,
                    headers={"Authorization": auth_header},
                    files=[
                        (
                            "mediaUpload",
                            (None, json.dumps({"folderId": folder_id}), "text/plain"),
                        ),
                        ("mediaUpload", (filename, f)),
                    ],
                )

        response.raise_for_status()
        result = response.text.strip()

        if result != "ok":
            return json.dumps(
                {"error": f"Unexpected response from upload endpoint: {result}"}
            )

        # Wiki.js returns "ok" with no asset metadata; query the list to get it.
        # Wiki.js sanitizes filenames: lowercase and replace [\s,;#]+ with _
        sanitized_filename = re.sub(r"[\s,;#]+", "_", filename.lower())

        list_query = """
        query($folderId: Int!, $kind: AssetKind!) {
            assets {
                list(folderId: $folderId, kind: $kind) {
                    id
                    filename
                    ext
                    kind
                    mime
                    fileSize
                }
            }
        }
        """
        list_response = await wikijs.graphql_request(
            list_query, {"folderId": folder_id, "kind": "ALL"}
        )
        assets = list_response.get("data", {}).get("assets", {}).get("list", [])
        asset = next(
            (a for a in assets if a.get("filename") == sanitized_filename), None
        )

        logger.info(f"Uploaded asset: {filename} to folder {folder_id}")

        if asset:
            return json.dumps(
                {
                    "id": asset.get("id"),
                    "filename": asset.get("filename") or sanitized_filename,
                    "mime": asset.get("mime"),
                    "fileSize": asset.get("fileSize") or file_size,
                    "kind": asset.get("kind"),
                }
            )
        else:
            # Upload succeeded but asset not found in listing (may appear after indexing)
            return json.dumps(
                {
                    "uploaded": True,
                    "filename": sanitized_filename,
                    "folder_id": folder_id,
                    "file_size": file_size,
                    "note": "Upload succeeded but asset not found in listing yet",
                }
            )

    except Exception as e:
        error_msg = f"Failed to upload asset: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})
```

Replace with:
```python
@mcp.tool()
async def wikijs_upload_asset(file_path: str, folder_id: int = 0) -> str:
    """
    Upload a local file to Wiki.js assets.

    Args:
        file_path: Absolute path to the local file
        folder_id: Target folder ID (0 = root). Use wikijs_list_asset_folders to find folder IDs.

    Returns:
        JSON string: {"id": int|None, "filename": str, "mime": str|None, "fileSize": int|None,
                       "kind": str|None, "verified": bool}. "verified" is False if the upload
                       succeeded but the newly created asset couldn't be uniquely identified
                       in a post-upload listing (folder not yet indexed, or a concurrent upload
                       landed in the same folder at the same time).
        On folder-not-found: {"error": str, "available_folders": [...]}
    """
    try:
        if not os.path.isfile(file_path):
            return json.dumps(
                {"error": f"File not found or is not a file: {file_path}"}
            )

        file_size = os.path.getsize(file_path)
        if file_size > settings.WIKIJS_MAX_UPLOAD_BYTES:
            max_mb = settings.WIKIJS_MAX_UPLOAD_BYTES / 1_048_576
            actual_mb = file_size / 1_048_576
            return json.dumps(
                {
                    "error": f"File size {actual_mb:.1f}MB exceeds limit of {max_mb:.0f}MB",
                    "file_size_bytes": file_size,
                    "limit_bytes": settings.WIKIJS_MAX_UPLOAD_BYTES,
                }
            )

        await wikijs.authenticate()

        if folder_id != 0:
            all_folders = await _get_all_asset_folder_ids()
            if folder_id not in all_folders:
                return json.dumps(
                    {
                        "error": f"Folder ID {folder_id} not found",
                        "available_folders": list(all_folders.values()),
                    }
                )

        filename = os.path.basename(file_path)

        before_response = await wikijs_list_assets(folder_id=folder_id, kind="ALL")
        before_ids = {a["id"] for a in json.loads(before_response)["assets"]}

        with open(file_path, "rb") as f:
            result = await wikijs.upload_asset(folder_id, filename, f)

        if result != "ok":
            return json.dumps(
                {"error": f"Unexpected response from upload endpoint: {result}"}
            )

        after_response = await wikijs_list_assets(folder_id=folder_id, kind="ALL")
        after_assets = json.loads(after_response)["assets"]
        new_assets = [a for a in after_assets if a["id"] not in before_ids]

        logger.info(f"Uploaded asset: {filename} to folder {folder_id}")

        if len(new_assets) == 1:
            asset = new_assets[0]
            return json.dumps(
                {
                    "id": asset.get("id"),
                    "filename": asset.get("filename"),
                    "mime": asset.get("mime"),
                    "fileSize": asset.get("fileSize"),
                    "kind": asset.get("kind"),
                    "verified": True,
                }
            )
        else:
            # 0 new assets: folder listing not indexed yet. >1: a concurrent upload
            # landed in this folder between the before/after snapshots and can't be
            # told apart from ours - report unverified either way rather than guess.
            return json.dumps(
                {
                    "id": None,
                    "filename": filename,
                    "mime": None,
                    "fileSize": file_size,
                    "kind": None,
                    "verified": False,
                    "note": "Upload succeeded but the new asset could not be uniquely verified in the folder listing",
                }
            )

    except Exception as e:
        error_msg = f"Failed to upload asset: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})
```

- [ ] **Step 2: Confirm the now-unused `re` import is still used elsewhere**

```bash
command grep -n "^import re\|re\.\w" src/wiki_mcp_server.py
```
`re` was only used for the deleted sanitization guess and nowhere else in this file as of the asset-tools PR — if this grep shows `import re` with no other `re.something` usage below it, remove the `import re` line. If other functions use `re.`, leave the import.

- [ ] **Step 3: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "fix: restore recursive folder validation, replace filename-guess with before/after asset diff, unify response shape"
```

---

## Task 5: Add the missing env var to config/example.env

**Files:** Modify `config/example.env`

- [ ] **Step 1: Add the setting, matching README's documented value**

Find:
```
# Optional: Repository Context
REPOSITORY_ROOT=./
DEFAULT_SPACE_NAME=Documentation
```
Replace with:
```
# Optional: Repository Context
REPOSITORY_ROOT=./
DEFAULT_SPACE_NAME=Documentation

# Optional: Asset Upload
WIKIJS_MAX_UPLOAD_BYTES=52428800  # 50MB default
```

- [ ] **Step 2: Commit**

```bash
git add config/example.env
git commit -m "docs: add WIKIJS_MAX_UPLOAD_BYTES to example.env, was documented in README but missing here"
```

---

## Task 6: Tests for `WikiJSClient.upload_asset`

**Files:**
- Create: `tests/test_upload_asset_client.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_upload_asset_client.py
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
            "error", request=request, response=httpx.Response(status_code, request=request, text=text)
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

    post_mock = AsyncMock(
        side_effect=[httpx.ConnectError("boom"), _fake_response()]
    )
    monkeypatch.setattr(w.wikijs.client, "post", post_mock)

    result = await w.wikijs.upload_asset(0, "test.txt", b"hello")

    assert result == "ok"
    assert post_mock.call_count == 2


@pytest.mark.asyncio
async def test_upload_asset_http_error_preserves_response_body(monkeypatch):
    monkeypatch.setattr(w.wikijs.upload_asset.retry, "stop", tenacity.stop_after_attempt(1))

    post_mock = AsyncMock(return_value=_fake_response(text="folder does not exist", status_code=422))
    monkeypatch.setattr(w.wikijs.client, "post", post_mock)

    with pytest.raises(Exception) as exc_info:
        await w.wikijs.upload_asset(0, "test.txt", b"hello")

    assert "folder does not exist" in str(exc_info.value)
    assert "422" in str(exc_info.value)
```

- [ ] **Step 2: Run and confirm PASS**

```bash
source venv/bin/activate
python3 -m pytest tests/test_upload_asset_client.py -v
```
Expected: all 3 PASS. If `test_upload_asset_reuses_shared_client_no_content_type_override` fails on the header-absence assertion, re-check Task 1 Steps 1-2 were applied — this test is what catches a regression back to the leaky-default bug.

- [ ] **Step 3: Commit**

```bash
git add tests/test_upload_asset_client.py
git commit -m "test: cover WikiJSClient.upload_asset retry, error detail, and header hygiene"
```

---

## Task 7: Tests for `wikijs_upload_asset`

**Files:**
- Create: `tests/test_wikijs_upload_asset.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_wikijs_upload_asset.py
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

    result = json.loads(
        await tool_fn(w.wikijs_upload_asset)(str(f), folder_id=999)
    )

    assert "error" in result
    assert "999" in result["error"]
    assert result["available_folders"] == [{"id": 1, "name": "Real Folder", "slug": "real"}]


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
async def test_upload_preexisting_same_name_asset_not_mistaken_for_new(tmp_path, monkeypatch):
    """Regression test for the stale-asset-collision bug: a pre-existing asset
    with the same filename must NOT be returned as the upload result."""
    monkeypatch.setattr(w.wikijs, "authenticate", AsyncMock(return_value=True))
    stale_listing = {
        "assets": [{"id": 1, "filename": "photo.jpg", "mime": "image/png", "fileSize": 999}],
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
```

Note: `monkeypatch.setattr(w, "wikijs_list_assets", ...)` replaces the module-level name entirely, which is safe here because `wikijs_upload_asset` looks up `wikijs_list_assets` from the module namespace at call time (a bare name reference inside the function body resolves via the module's global scope at call time, not at function-definition time) — so patching the module attribute is enough to intercept the call, whether or not fastmcp wraps it.

- [ ] **Step 2: Run and confirm PASS**

```bash
python3 -m pytest tests/test_wikijs_upload_asset.py -v
```
Expected: all 5 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_wikijs_upload_asset.py
git commit -m "test: cover folder validation, oversized/missing file, and asset-diff identification in wikijs_upload_asset"
```

---

## Task 8: Full verification

**Files:** none

- [ ] **Step 1: Run the whole suite**

```bash
source venv/bin/activate
python3 -m pytest tests/ -v
```
Expected: all tests across both plans PASS (6 from the fastmcp fix + 3 + 5 from this plan = 14).

- [ ] **Step 2: Format and lint**

```bash
black src/ tests/ conftest.py
isort src/ tests/ conftest.py
mypy src/ || true
```

- [ ] **Step 3: Re-read the diff for scope creep before committing formatting**

```bash
git diff --stat
```
Confirm `black`/`isort` only touched lines this plan already modified, not the whole file (per the review finding about the earlier `80591ad` commit reformatting unrelated code) — if it reformats untouched functions, revert those hunks manually (`git diff` then selectively `git checkout -p` the unrelated hunks) rather than committing a broad reformat under a narrow commit message.

- [ ] **Step 4: Manual smoke test against live Wiki.js**

```bash
./test-server.sh
```
From an MCP client: call `wikijs_upload_asset` with a real small file at `folder_id=0`, confirm `verified: true` with a real `id`; then call it again with an obviously-wrong `folder_id` (e.g. 999999) and confirm it returns `available_folders` instead of a false "uploaded" success. This is the one path a human should verify end-to-end — the review's most severe finding (false-positive success) is exactly what this checks.

- [ ] **Step 5: Commit any formatting changes**

```bash
git add -A
git commit -m "style: black/isort on files touched by this plan"
```

---

## Task 9: Push and update the PR (requires explicit user confirmation before each action)

**Files:** none

- [ ] **Step 1: Push**

```bash
git push origin asset-tools
```

- [ ] **Step 2: Update the existing PR description** to note these fixes address the code-review findings, then request re-review.

**Note on commit hygiene for future work on this branch:** the review flagged commit `80591ad` ("style: apply black/isort formatting to asset tools") for reformatting the *entire* file under a message that claimed a narrow scope. Don't rewrite that already-pushed commit's history — just keep new commits on this branch scoped to what their message says, per Task 8 Step 3 above.
