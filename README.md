# docctx

Local-first deterministic context retrieval engine for coding agents.

## What is docctx?

`docctx` solves a specific problem: when a coding agent needs documentation, it delivers the most precise chunks from the most trusted sources — with explainable ranking — or returns nothing.

**Key principle:** Wrong context is more dangerous than no context. An agent with no docs knows it doesn't know. An agent with wrong docs confidently generates wrong code.

## Installation

```bash
# Install with uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

## Quick Start

```bash
# Add documentation
docctx add https://react.dev/reference/react/useEffect

# List packs
docctx list

# Query documentation
docctx query "useEffect cleanup"

# Start MCP server
docctx serve
```

## CLI Commands

| Command | Description |
|---|---|
| `docctx add <url>` | Ingest a URL as a context pack |
| `docctx refresh <pack>` | Re-crawl an existing pack |
| `docctx remove <pack>` | Hard delete a pack |
| `docctx list` | List all packs |
| `docctx query "<query>"` | Search documentation |
| `docctx inspect <url\|pack>` | Inspect extraction or pack structure |
| `docctx explain "<query>"` | Show retrieval reasoning |
| `docctx doctor` | Health check |
| `docctx serve` | Start MCP server |

## MCP Configuration

Add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "docctx": {
      "command": "docctx",
      "args": ["serve"]
    }
  }
}
```

Or with `uv run`:
```json
{
  "mcpServers": {
    "docctx": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/docctx", "docctx", "serve"]
    }
  }
}
```

## MCP Tools

- **`search_docs`** — Search chunks. Supports `response_mode: compact|standard`, `token_budget`, `min_confidence: high|low|any`
- **`get_chunk`** — Get full chunk content by ID. Lazy expansion after `search_docs`.
- **`list_packs`** — Discover available documentation packs.

## Configuration

Config file: `~/.docctx/config.toml` (auto-created on first run)

```toml
[retrieval]
floor_score = 3.0          # BM25 minimum to include
confidence_cutoff = 6.0    # BM25 threshold for "high" confidence
default_limit = 5
max_limit = 10

[chunking]
target_tokens = 400
max_tokens = 800
min_tokens = 80

[ingestion]
rate_limit_rps = 1.0
max_pages = 50
max_depth = 2
respect_robots = true
```

## Scope Rules

| Scope | Crawls |
|---|---|
| `page-only` | Entry URL only |
| `siblings` | Entry URL + sibling pages (same parent path) — **default for deep URLs** |
| `subtree` | Entry URL + all descendants |
| `site` | Entire domain (requires `--scope site`) |

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Run specific test file
pytest tests/unit/test_chunker.py -v
```

## Architecture

```
INGESTION (CLI, network):
URL → Discover → Fetch → Extract → Chunk → Index → DB

SERVING (MCP, offline):
Query → FTS5 → Boost → Filter → Threshold → Chunks
```

Storage: `~/.docctx/store.db` (SQLite WAL)  
Cache: `~/.docctx/cache/` (SHA256-keyed HTML)
