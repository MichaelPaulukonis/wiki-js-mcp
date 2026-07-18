# Asset Upload & Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 MCP tools to wiki-js-mcp for listing asset folders, listing assets, creating folders, and uploading local files to Wiki.js.

**Architecture:** Flat addition to `src/wiki_mcp_server.py` - 4 new `@mcp.tool()` functions appended after existing tools, following exact existing pattern. GraphQL for folder/asset queries and folder creation; multipart REST POST to `/u` for binary upload. No new files.

**Tech Stack:** Python 3.12+, FastMCP, httpx (already in use), Wiki.js GraphQL API + REST upload endpoint `/u`

---

> **Note on testing:** No automated test suite exists. Verification uses direct curl against the live Wiki.js GraphQL API and REST endpoint. Requires a running Wiki.js instance with valid `.env` configured. Load your token: `source .env && TOKEN=$WIKIJS_TOKEN`.

---

## File Structure

- **Modify:** `src/wiki_mcp_server.py`
  - Add `WIKIJS_MAX_UPLOAD_BYTES` to `Settings` class (~line 44)
  - Append 4 new tools after line 2061 (end of file, before `def main()`)

No other files change.

---

## Task 1: Create Worktree

**Files:**
- No file changes - setup only

- [ ] **Step 1: Create worktree**

```bash
git worktree add .worktrees/asset-tools -b asset-tools
cd .worktrees/asset-tools
```

- [ ] **Step 2: Verify worktree is clean**

```bash
git status
```

Expected: `nothing to commit, working tree clean`

---

## Task 2: Add `WIKIJS_MAX_UPLOAD_BYTES` to Settings

**Files:**
- Modify: `src/wiki_mcp_server.py` (~line 44, inside `Settings` class)

- [ ] **Step 1: Add setting to Settings class**

Find the `Settings` class (line ~34). Add after `DEFAULT_SPACE_NAME`:

```python
WIKIJS_MAX_UPLOAD_BYTES: int = Field(default=52_428_800)  # 50MB
```

The full `Settings` class fields should look like:
```python
class Settings(BaseSettings):
    WIKIJS_API_URL: str = Field(default="http://localhost:3000")
    WIKIJS_TOKEN: Optional[str] = Field(default=None)
    WIKIJS_API_KEY: Optional[str] = Field(default=None)
    WIKIJS_USERNAME: Optional[str] = Field(default=None)
    WIKIJS_PASSWORD: Optional[str] = Field(default=None)
    WIKIJS_MCP_DB: str = Field(default="./wikijs_mappings.db")
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FILE: str = Field(default="wikijs_mcp.log")
    REPOSITORY_ROOT: str = Field(default="./")
    DEFAULT_SPACE_NAME: str = Field(default="Documentation")
    WIKIJS_MAX_UPLOAD_BYTES: int = Field(default=52_428_800)  # 50MB
```

- [ ] **Step 2: Verify server still starts**

```bash
cd .worktrees/asset-tools
source venv/bin/activate
python -c "from src.wiki_mcp_server import settings; print(settings.WIKIJS_MAX_UPLOAD_BYTES)"
```

Expected output: `52428800`

- [ ] **Step 3: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "feat: add WIKIJS_MAX_UPLOAD_BYTES setting (default 50MB)"
```

---

## Task 3: Implement `wikijs_list_asset_folders`

**Files:**
- Modify: `src/wiki_mcp_server.py` (append before `def main()`)

- [ ] **Step 1: Verify GraphQL query works against live instance**

```bash
source .env
curl -s -X POST "$WIKIJS_API_URL/graphql" \
  -H "Authorization: Bearer $WIKIJS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ assets { folders(parentFolderId: 0) { id name slug } } }"}' \
  | python3 -m json.tool
```

Expected: JSON with `data.assets.folders` array (may be empty `[]` if no folders exist yet).

- [ ] **Step 2: Implement tool**

Insert before `def main():` at the end of the file:

```python
@mcp.tool()
async def wikijs_list_asset_folders(parent_folder_id: int = 0) -> str:
    """
    List asset folders in Wiki.js.

    Args:
        parent_folder_id: Parent folder ID (0 = root)

    Returns:
        JSON string with folders list: {"folders": [{"id", "name", "slug"}]}
    """
    try:
        await wikijs.authenticate()

        query = """
        query($parentFolderId: Int!) {
            assets {
                folders(parentFolderId: $parentFolderId) {
                    id
                    name
                    slug
                }
            }
        }
        """

        response = await wikijs.graphql_request(query, {"parentFolderId": parent_folder_id})
        folders = response.get("data", {}).get("assets", {}).get("folders", [])

        return json.dumps({"folders": folders})

    except Exception as e:
        error_msg = f"Failed to list asset folders: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})
```

- [ ] **Step 3: Verify server starts with new tool**

```bash
python -c "import src.wiki_mcp_server; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "feat: add wikijs_list_asset_folders tool"
```

---

## Task 4: Implement `wikijs_list_assets`

**Files:**
- Modify: `src/wiki_mcp_server.py` (append before `def main()`)

- [ ] **Step 1: Verify GraphQL query works against live instance**

```bash
source .env
curl -s -X POST "$WIKIJS_API_URL/graphql" \
  -H "Authorization: Bearer $WIKIJS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ assets { list(folderId: 0, kind: ALL) { id filename ext kind mime fileSize createdAt updatedAt } } }"}' \
  | python3 -m json.tool
```

Expected: JSON with `data.assets.list` array.

- [ ] **Step 2: Implement tool**

Append before `def main():`:

```python
@mcp.tool()
async def wikijs_list_assets(folder_id: int = 0, kind: str = "ALL") -> str:
    """
    List assets in a Wiki.js folder.

    Args:
        folder_id: Folder ID (0 = root)
        kind: Asset kind filter - ALL, IMAGE, BINARY, DOCUMENT (default ALL)

    Returns:
        JSON string: {"assets": [{"id", "filename", "ext", "kind", "mime", "fileSize", "createdAt", "updatedAt"}], "total": int}
    """
    try:
        await wikijs.authenticate()

        valid_kinds = {"ALL", "IMAGE", "BINARY", "DOCUMENT"}
        if kind.upper() not in valid_kinds:
            kind = "ALL"
        else:
            kind = kind.upper()

        query = """
        query($folderId: Int!, $kind: AssetKind!) {
            assets {
                list(folderId: $folderId, kind: $kind) {
                    id
                    filename
                    ext
                    kind
                    mime
                    fileSize
                    createdAt
                    updatedAt
                }
            }
        }
        """

        response = await wikijs.graphql_request(query, {"folderId": folder_id, "kind": kind})
        assets = response.get("data", {}).get("assets", {}).get("list", [])

        return json.dumps({"assets": assets, "total": len(assets)})

    except Exception as e:
        error_msg = f"Failed to list assets: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})
```

- [ ] **Step 3: Test kind coercion**

```bash
python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')
from src.wiki_mcp_server import wikijs_list_assets
import json

async def test():
    # invalid kind should coerce to ALL
    result = await wikijs_list_assets(folder_id=0, kind='INVALID')
    data = json.loads(result)
    assert 'error' not in data, f'Unexpected error: {data}'
    assert 'assets' in data
    assert 'total' in data
    print('kind coercion OK')

asyncio.run(test())
"
```

Expected: `kind coercion OK`

- [ ] **Step 4: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "feat: add wikijs_list_assets tool"
```

---

## Task 5: Implement `wikijs_create_asset_folder`

**Files:**
- Modify: `src/wiki_mcp_server.py` (append before `def main()`)

- [ ] **Step 1: Verify mutation schema against live instance**

```bash
source .env
curl -s -X POST "$WIKIJS_API_URL/graphql" \
  -H "Authorization: Bearer $WIKIJS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation { assets { createFolder(parentFolderId: 0, slug: \"test-verify\", name: \"Test Verify\") { responseResult { succeeded errorCode message } } } }"}' \
  | python3 -m json.tool
```

Expected: `responseResult.succeeded: true`. If it creates the folder, delete it manually in Wiki.js admin or note the folder ID for cleanup.

- [ ] **Step 2: Implement tool**

Append before `def main():`:

```python
@mcp.tool()
async def wikijs_create_asset_folder(name: str, slug: str, parent_folder_id: int = 0) -> str:
    """
    Create an asset folder in Wiki.js.

    Args:
        name: Human-readable folder name
        slug: URL-safe folder slug (caller's responsibility to make URL-safe)
        parent_folder_id: Parent folder ID (0 = root)

    Returns:
        JSON string: {"created": true, "name": str, "slug": str, "parent_folder_id": int}
    """
    try:
        await wikijs.authenticate()

        mutation = """
        mutation($parentFolderId: Int!, $slug: String!, $name: String!) {
            assets {
                createFolder(parentFolderId: $parentFolderId, slug: $slug, name: $name) {
                    responseResult {
                        succeeded
                        errorCode
                        message
                    }
                }
            }
        }
        """

        response = await wikijs.graphql_request(mutation, {
            "parentFolderId": parent_folder_id,
            "slug": slug,
            "name": name
        })

        result_data = response.get("data", {}).get("assets", {}).get("createFolder", {})
        response_result = result_data.get("responseResult", {})

        if response_result.get("succeeded"):
            logger.info(f"Created asset folder: {name} (slug: {slug}, parent: {parent_folder_id})")
            return json.dumps({
                "created": True,
                "name": name,
                "slug": slug,
                "parent_folder_id": parent_folder_id
            })
        else:
            error_msg = response_result.get("message", "Unknown error")
            return json.dumps({"error": f"Failed to create folder: {error_msg}"})

    except Exception as e:
        error_msg = f"Failed to create asset folder: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})
```

- [ ] **Step 3: Verify tool end-to-end**

```bash
python3 -c "
import asyncio, json, sys
sys.path.insert(0, '.')
from src.wiki_mcp_server import wikijs_create_asset_folder, wikijs_list_asset_folders

async def test():
    result = await wikijs_create_asset_folder('Plan Test', 'plan-test', 0)
    data = json.loads(result)
    assert data.get('created') == True, f'Expected created=True, got: {data}'

    folders = json.loads(await wikijs_list_asset_folders(0))
    slugs = [f['slug'] for f in folders['folders']]
    assert 'plan-test' in slugs, f'Folder not found in list: {slugs}'
    print('create_asset_folder OK')

asyncio.run(test())
"
```

Expected: `create_asset_folder OK`

- [ ] **Step 4: Clean up test folder via Wiki.js admin UI**

Navigate to Wiki.js admin → Assets, delete the `plan-test` folder created in Step 3.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "feat: add wikijs_create_asset_folder tool"
```

---

## Task 6: Implement `wikijs_upload_asset`

**Files:**
- Modify: `src/wiki_mcp_server.py` (append before `def main()`)

- [ ] **Step 1: Verify REST upload endpoint works**

```bash
source .env
# Create a small test file
echo "test content" > /tmp/mcp-upload-test.txt

curl -s -X POST "$WIKIJS_API_URL/u" \
  -H "Authorization: Bearer $WIKIJS_TOKEN" \
  -F 'mediaUpload={"folderId":0}' \
  -F "file=@/tmp/mcp-upload-test.txt" \
  | python3 -m json.tool

rm /tmp/mcp-upload-test.txt
```

Expected: JSON response with asset metadata (id, filename, etc.) or an error message showing what fields the API expects.

- [ ] **Step 2: Implement tool**

Append before `def main():`:

```python
@mcp.tool()
async def wikijs_upload_asset(file_path: str, folder_id: int = 0) -> str:
    """
    Upload a local file to Wiki.js assets.

    Args:
        file_path: Absolute path to the local file
        folder_id: Target folder ID (0 = root). Use wikijs_list_asset_folders to find folder IDs.

    Returns:
        JSON string: {"id": int, "filename": str, "path": str, "mime": str, "fileSize": int}
        On folder-not-found: {"error": str, "available_folders": [...]}
    """
    try:
        if not os.path.isfile(file_path):
            return json.dumps({"error": f"File not found or is not a file: {file_path}"})

        file_size = os.path.getsize(file_path)
        if file_size > settings.WIKIJS_MAX_UPLOAD_BYTES:
            max_mb = settings.WIKIJS_MAX_UPLOAD_BYTES / 1_048_576
            actual_mb = file_size / 1_048_576
            return json.dumps({
                "error": f"File size {actual_mb:.1f}MB exceeds limit of {max_mb:.0f}MB",
                "file_size_bytes": file_size,
                "limit_bytes": settings.WIKIJS_MAX_UPLOAD_BYTES
            })

        await wikijs.authenticate()

        if folder_id != 0:
            folders_response = await wikijs.graphql_request(
                """
                query {
                    assets {
                        folders(parentFolderId: 0) {
                            id
                            name
                            slug
                        }
                    }
                }
                """,
                {}
            )
            folders = folders_response.get("data", {}).get("assets", {}).get("folders", [])
            folder_ids = {f["id"] for f in folders}
            if folder_id not in folder_ids:
                return json.dumps({
                    "error": f"Folder ID {folder_id} not found at root level",
                    "available_folders": folders
                })

        upload_url = f"{wikijs.base_url}/u"
        filename = os.path.basename(file_path)

        with open(file_path, "rb") as f:
            response = await wikijs.client.post(
                upload_url,
                files={"file": (filename, f)},
                data={"mediaUpload": json.dumps({"folderId": folder_id})}
            )

        response.raise_for_status()
        result = response.json()

        if isinstance(result, list) and result:
            asset = result[0]
        elif isinstance(result, dict):
            asset = result
        else:
            return json.dumps({"error": f"Unexpected response from upload endpoint: {result}"})

        logger.info(f"Uploaded asset: {filename} to folder {folder_id}")
        return json.dumps({
            "id": asset.get("id"),
            "filename": asset.get("filename") or filename,
            "path": asset.get("path") or asset.get("url"),
            "mime": asset.get("mime"),
            "fileSize": asset.get("fileSize") or file_size
        })

    except Exception as e:
        error_msg = f"Failed to upload asset: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})
```

- [ ] **Step 3: Test file-not-found guard**

```bash
python3 -c "
import asyncio, json, sys
sys.path.insert(0, '.')
from src.wiki_mcp_server import wikijs_upload_asset

async def test():
    result = json.loads(await wikijs_upload_asset('/tmp/nonexistent-file-xyz.png'))
    assert 'error' in result, f'Expected error, got: {result}'
    assert 'not found' in result['error'].lower()
    print('file-not-found guard OK')

asyncio.run(test())
"
```

Expected: `file-not-found guard OK`

- [ ] **Step 4: Test file size guard**

```bash
python3 -c "
import asyncio, json, sys, tempfile, os
sys.path.insert(0, '.')

# Temporarily override the limit to test
from src import wiki_mcp_server
orig = wiki_mcp_server.settings.WIKIJS_MAX_UPLOAD_BYTES
wiki_mcp_server.settings.WIKIJS_MAX_UPLOAD_BYTES = 5  # 5 bytes

from src.wiki_mcp_server import wikijs_upload_asset

async def test():
    with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as f:
        f.write(b'hello world')
        tmp_path = f.name
    try:
        result = json.loads(await wikijs_upload_asset(tmp_path))
        assert 'error' in result, f'Expected size error, got: {result}'
        assert 'exceeds limit' in result['error'], f'Wrong error: {result}'
        print('size guard OK')
    finally:
        os.unlink(tmp_path)

asyncio.run(test())
wiki_mcp_server.settings.WIKIJS_MAX_UPLOAD_BYTES = orig
"
```

Expected: `size guard OK`

- [ ] **Step 5: Test live upload to root**

```bash
python3 -c "
import asyncio, json, sys, tempfile, os
sys.path.insert(0, '.')
from src.wiki_mcp_server import wikijs_upload_asset

async def test():
    with tempfile.NamedTemporaryFile(delete=False, suffix='.txt', prefix='mcp-test-') as f:
        f.write(b'MCP upload test file')
        tmp_path = f.name
    try:
        result = json.loads(await wikijs_upload_asset(tmp_path, folder_id=0))
        assert 'error' not in result, f'Upload failed: {result}'
        assert result.get('filename'), f'No filename in result: {result}'
        print(f'Upload OK: {result}')
    finally:
        os.unlink(tmp_path)

asyncio.run(test())
"
```

Expected: `Upload OK: {"id": ..., "filename": "mcp-test-....txt", ...}`

Note: If the upload response format differs from expected (API returns different fields), adjust the field mapping in the tool accordingly and update this step.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_mcp_server.py
git commit -m "feat: add wikijs_upload_asset tool with size and path validation"
```

---

## Task 7: Final Verification & PR

**Files:**
- No code changes

- [ ] **Step 1: Verify all 4 tools appear in server**

```bash
python3 -c "
import src.wiki_mcp_server as m
tools = [name for name in dir(m) if name.startswith('wikijs_') and callable(getattr(m, name))]
required = ['wikijs_list_asset_folders', 'wikijs_list_assets', 'wikijs_create_asset_folder', 'wikijs_upload_asset']
for t in required:
    assert t in tools, f'Missing tool: {t}'
print('All 4 asset tools present:', required)
"
```

Expected: `All 4 asset tools present: [...]`

- [ ] **Step 2: Run linting**

```bash
source venv/bin/activate
black src/wiki_mcp_server.py
isort src/wiki_mcp_server.py
mypy src/wiki_mcp_server.py
```

Fix any mypy errors before proceeding.

- [ ] **Step 3: Commit lint fixes (if any)**

```bash
git add src/wiki_mcp_server.py
git commit -m "style: apply black/isort formatting to asset tools"
```

- [ ] **Step 4: Create PR**

```bash
gh pr create \
  --title "feat: add asset upload and query MCP tools" \
  --body "Adds 4 MCP tools for Wiki.js asset management:
- \`wikijs_list_asset_folders\` - list folders by parent ID
- \`wikijs_list_assets\` - list assets in a folder, filterable by kind (ALL/IMAGE/BINARY/DOCUMENT)
- \`wikijs_create_asset_folder\` - create a folder under a parent
- \`wikijs_upload_asset\` - upload local file to Wiki.js; validates file exists, checks size limit (default 50MB), verifies folder ID if specified

Spec: docs/superpowers/specs/2026-06-30-asset-upload-query-design.md"
```
