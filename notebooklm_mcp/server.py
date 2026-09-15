"""NotebookLM context MCP server for Claude Code."""

import json
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import MCPServer

from notebooklm_mcp import context_manager as cm
from notebooklm_mcp import drive_client as dc

mcp = MCPServer("notebooklm-context")


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def search_context(query: str, top_k: int = 5) -> str:
    """
    Search imported NotebookLM content and return the most relevant chunks.

    Use this BEFORE answering any question to retrieve relevant context instead
    of asking the user to paste large documents. This reduces token usage
    dramatically by injecting only the relevant passages.

    Args:
        query:  Natural language search query describing what you need.
        top_k:  Number of chunks to return (default 5, max 20).
    """
    top_k = min(top_k, 20)
    results = cm.search_context(query, top_k=top_k)
    if not results:
        return "No relevant context found. Import sources first with import_drive_file or import_local_file."

    lines = [f"Found {len(results)} relevant chunks (total ≈ {sum(r['tokens'] for r in results)} tokens):\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"--- Chunk {i} | Source: {r['source']} | Score: {r['score']} | ~{r['tokens']} tokens ---")
        lines.append(r["text"])
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def import_drive_file(file_id_or_url: str, chunk_size: int = 400) -> str:
    """
    Import a Google Drive file (Doc, Sheet, PDF, text) into the context store.

    Supports Google Docs, Sheets, PDFs and plain text. The file is chunked
    and indexed for fast semantic search, so only relevant passages are
    retrieved per query (no need to load the full document into context).

    Args:
        file_id_or_url: Google Drive file ID or full URL.
        chunk_size:     Target tokens per chunk (default 400).
    """
    if not dc.CREDS_PATH.exists():
        return (
            "Google Drive credentials not configured. "
            "Run: python setup_credentials.py\n"
            "Then re-authenticate and try again."
        )
    try:
        name, text = dc.get_file_text(file_id_or_url)
        if not text.strip():
            return f"File '{name}' appears to be empty or has no extractable text."
        info = cm.add_source(name=name, origin=f"drive:{file_id_or_url}", text=text, chunk_size=chunk_size)
        saved_tokens = info["total_tokens"]
        return (
            f"Imported '{name}' (source_id={info['source_id']}).\n"
            f"Chunks: {info['chunks']}  |  Total tokens: {saved_tokens:,}\n"
            f"Use search_context(query) to retrieve relevant passages instead "
            f"of loading all {saved_tokens:,} tokens into context."
        )
    except FileNotFoundError as e:
        return str(e)
    except Exception as e:
        return f"Error importing Drive file: {e}"


@mcp.tool()
def import_local_file(file_path: str, name: Optional[str] = None, chunk_size: int = 400) -> str:
    """
    Import a local text file (txt, md, csv, json, pdf) into the context store.

    After importing, use search_context() to retrieve only relevant passages
    instead of reading the full file, reducing token usage significantly.

    Args:
        file_path:  Absolute or home-relative path to the file.
        name:       Display name for this source (defaults to filename).
        chunk_size: Target tokens per chunk (default 400).
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return f"File not found: {path}"

    display_name = name or path.name
    suffix = path.suffix.lower()

    try:
        if suffix == ".pdf":
            text = _read_pdf(path)
        elif suffix == ".json":
            raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            text = json.dumps(raw, ensure_ascii=False, indent=2)
        else:
            text = path.read_text(encoding="utf-8", errors="replace")

        if not text.strip():
            return f"File '{display_name}' is empty or has no extractable text."

        info = cm.add_source(name=display_name, origin=f"local:{path}", text=text, chunk_size=chunk_size)
        return (
            f"Imported '{display_name}' (source_id={info['source_id']}).\n"
            f"Chunks: {info['chunks']}  |  Total tokens: {info['total_tokens']:,}\n"
            f"Use search_context(query) to retrieve only relevant passages."
        )
    except Exception as e:
        return f"Error importing file: {e}"


@mcp.tool()
def list_sources() -> str:
    """List all imported NotebookLM sources with their token counts."""
    sources = cm.list_sources()
    if not sources:
        return "No sources imported yet. Use import_drive_file or import_local_file."

    total_tokens = sum(s["total_tokens"] for s in sources)
    lines = [f"Imported sources ({len(sources)} total | {total_tokens:,} tokens stored):\n"]
    for s in sources:
        lines.append(
            f"  [{s['id']}] {s['name']}\n"
            f"       chunks={s['chunks']}  tokens≈{s['total_tokens']:,}  "
            f"added={s['added']}  origin={s['origin']}"
        )
    return "\n".join(lines)


@mcp.tool()
def remove_source(source_id: int) -> str:
    """
    Remove an imported source from the context store.

    Args:
        source_id: ID shown by list_sources().
    """
    removed = cm.remove_source(source_id)
    if removed:
        return f"Source {source_id} removed."
    return f"Source {source_id} not found."


@mcp.tool()
def get_source_full(source_id: int, max_tokens: int = 2000) -> str:
    """
    Retrieve up to max_tokens of raw text from a source (sequential chunks).

    Prefer search_context() for targeted retrieval. Use this only when you
    need a contiguous excerpt from the beginning of a document.

    Args:
        source_id:  ID shown by list_sources().
        max_tokens: Maximum tokens to return (default 2000).
    """
    text = cm.get_source_text(source_id, max_tokens=max_tokens)
    if text is None:
        return f"Source {source_id} not found."
    return text


@mcp.tool()
def clear_all_context() -> str:
    """Delete ALL imported sources and their chunks from the context store."""
    deleted = cm.clear_all()
    return f"Deleted {deleted} sources. Context store is now empty."


@mcp.tool()
def import_drive_folder(folder_id_or_url: str, chunk_size: int = 400) -> str:
    """
    Import all files from a Google Drive folder into the context store.

    Args:
        folder_id_or_url: Google Drive folder ID or URL.
        chunk_size:       Target tokens per chunk (default 400).
    """
    if not dc.CREDS_PATH.exists():
        return "Google Drive credentials not configured. Run: python setup_credentials.py"

    folder_id = dc._extract_file_id(folder_id_or_url)
    try:
        files = dc.list_drive_folder(folder_id)
    except Exception as e:
        return f"Error listing folder: {e}"

    if not files:
        return "Folder is empty or not accessible."

    results = []
    for f in files:
        try:
            _, text = dc.get_file_text(f["id"])
            if not text.strip():
                results.append(f"  SKIP  {f['name']} (no text)")
                continue
            info = cm.add_source(name=f["name"], origin=f"drive:{f['id']}", text=text, chunk_size=chunk_size)
            results.append(f"  OK    {f['name']}  chunks={info['chunks']}  tokens≈{info['total_tokens']:,}")
        except Exception as e:
            results.append(f"  ERROR {f['name']}: {e}")

    return f"Imported {len(files)} files from folder:\n" + "\n".join(results)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_pdf(path: Path) -> str:
    raw = path.read_bytes()
    return dc._extract_pdf_text(raw)


# ── Entry point ───────────────────────────────────────────────────────────────

import asyncio


def main():
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
