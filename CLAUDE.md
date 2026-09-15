# NotebookLM Context Integration

## Purpose
This project provides an MCP server that imports content from Google Drive / NotebookLM sources into a local SQLite index. Claude Code can then retrieve **only the relevant passages** for any query, instead of loading full documents into context.

## Token-reduction strategy
1. Import large documents once via `import_drive_file` or `import_local_file`.
2. Before answering any question about those documents, call `search_context(query)` to pull in the relevant chunks (~400 tokens each) instead of the full file.
3. Never read or paste large source files directly into context; always use the MCP tools.

## Available MCP tools (always prefer these)
| Tool | When to use |
|------|-------------|
| `search_context(query, top_k)` | Find relevant passages before answering |
| `import_drive_file(url_or_id)` | Import a Google Doc/Sheet/PDF from Drive |
| `import_local_file(path)` | Import a local .txt/.md/.pdf/.json file |
| `import_drive_folder(url_or_id)` | Import all files in a Drive folder |
| `list_sources()` | Show what has been imported |
| `get_source_full(id, max_tokens)` | Get raw text excerpt when needed |
| `remove_source(id)` | Remove a source from the store |
| `clear_all_context()` | Wipe the entire store |

## Setup (one-time)
```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure Google OAuth (skip if only using local files)
python setup_credentials.py

# 3. Add MCP server to Claude Code (already in .claude/settings.json)
```

## MCP server location
`notebooklm_mcp/server.py` – run with `python notebooklm_mcp/server.py`
