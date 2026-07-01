# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

MCP server exposing Wiki.js operations to AI assistants. Single-file Python implementation using FastMCP. This server is in active production use against a personal Wiki.js instance - changes to `src/wiki_mcp_server.py` affect live tooling.

Ignore `docker.yml` and Wiki.js setup sections of the README - those are for end-users standing up a Wiki.js instance, not for developing the server itself.

## Development Workflow

All development work happens in git worktrees under `.worktrees/`. Create a worktree before starting any feature or fix:

```bash
git worktree add .worktrees/<branch-name> -b <branch-name>
```

No test suite exists. Manual validation requires a live Wiki.js instance via `./test-server.sh`.

## Setup

```bash
./setup.sh          # Creates venv, installs deps, copies .env template
cp config/example.env .env  # Then edit with WIKIJS_API_URL and WIKIJS_TOKEN
```

Requires Python 3.12+. Uses Poetry if installed, otherwise `requirements.txt` via pip.

## Running

```bash
./start-server.sh           # Validates .env, launches src/wiki_mcp_server.py via venv
./test-server.sh            # Same with verbose output for interactive debugging
LOG_LEVEL=DEBUG ./start-server.sh
```

## Linting / Formatting

```bash
source venv/bin/activate
black src/
isort src/
mypy src/
```

## Architecture

Everything lives in `src/wiki_mcp_server.py`. Three logical layers:

**Config & auth** (`Settings`, `WikiJSClient`): Pydantic-settings reads `.env`. `WikiJSClient` handles GraphQL requests with tenacity retry (3 attempts, exponential backoff). Auth supports JWT token (`WIKIJS_TOKEN` / `WIKIJS_API_KEY`) or username/password via GraphQL login mutation. Each tool calls `await wikijs.authenticate()` at entry.

**Persistence** (`FileMapping`, `RepositoryContext`): SQLAlchemy models backed by SQLite (`WIKIJS_MCP_DB`). Tracks file-to-page mappings and repository context. `find_repository_root()` walks up from cwd looking for `.git` or `.wikijs_mcp` marker file.

**MCP tools**: 21 `@mcp.tool()` decorated async functions. All follow the pattern: authenticate, build GraphQL query/mutation, call `wikijs.graphql_request()`, return JSON string. `extract_code_structure()` uses Python AST to pull classes/functions/imports for auto-documentation tools.

## Configuring as MCP Server

In Claude Code `~/.claude.json` or Cursor `~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "wikijs": {
      "command": "/absolute/path/to/wiki-js-mcp/start-server.sh"
    }
  }
}
```

## Environment Variables

| Variable | Required | Notes |
|----------|----------|-------|
| `WIKIJS_API_URL` | Yes | Default `http://localhost:3000` |
| `WIKIJS_TOKEN` or `WIKIJS_API_KEY` | Yes* | JWT from Wiki.js admin panel |
| `WIKIJS_USERNAME` + `WIKIJS_PASSWORD` | Yes* | Alternative to token |
| `WIKIJS_MCP_DB` | No | SQLite path, default `./wikijs_mappings.db` |
| `LOG_LEVEL` | No | Default `INFO` |

*One auth method required.
