"""Context manager: chunking, SQLite storage, TF-IDF search."""

import sqlite3
import json
import re
import math
from pathlib import Path
from collections import Counter
from typing import Optional

DB_PATH = Path.home() / ".notebooklm_mcp" / "context.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL,
            origin  TEXT NOT NULL,
            added   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            seq       INTEGER NOT NULL,
            text      TEXT NOT NULL,
            tokens    INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tfidf_index (
            term      TEXT NOT NULL,
            chunk_id  INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            tf_idf    REAL NOT NULL,
            PRIMARY KEY (term, chunk_id)
        );
    """)
    conn.commit()


# ── Tokenisation ──────────────────────────────────────────────────────────────

def _tokenise(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúñüàâéêèîïôùûüçœ]{2,}", text.lower())


def _estimate_tokens(text: str) -> int:
    # ~4 chars per token (GPT-style approximation)
    return max(1, len(text) // 4)


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, max_tokens: int = 400, overlap_tokens: int = 50) -> list[str]:
    """Split text into overlapping chunks by paragraph then sentence."""
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        p_tokens = _estimate_tokens(para)
        if p_tokens > max_tokens:
            # Split long paragraph by sentence
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                s_tokens = _estimate_tokens(sent)
                if current_tokens + s_tokens > max_tokens and current:
                    chunks.append(" ".join(current))
                    # Keep last overlap_tokens worth of sentences for context
                    overlap: list[str] = []
                    ov = 0
                    for s in reversed(current):
                        if ov + _estimate_tokens(s) <= overlap_tokens:
                            overlap.insert(0, s)
                            ov += _estimate_tokens(s)
                        else:
                            break
                    current = overlap
                    current_tokens = ov
                current.append(sent)
                current_tokens += s_tokens
        else:
            if current_tokens + p_tokens > max_tokens and current:
                chunks.append("\n\n".join(current))
                current = []
                current_tokens = 0
            current.append(para)
            current_tokens += p_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text[:2000]]


# ── TF-IDF ────────────────────────────────────────────────────────────────────

def _rebuild_tfidf(conn: sqlite3.Connection, source_id: int) -> None:
    """Recompute TF-IDF for all chunks of a source and update the index."""
    chunk_rows = conn.execute(
        "SELECT id, text FROM chunks WHERE source_id = ?", (source_id,)
    ).fetchall()

    doc_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if doc_count == 0:
        return

    term_doc_freq: Counter = Counter()
    chunk_tf: list[tuple[int, Counter]] = []

    for row in chunk_rows:
        tokens = _tokenise(row["text"])
        tf = Counter(tokens)
        total = sum(tf.values()) or 1
        # Normalize TF
        tf_norm = {t: c / total for t, c in tf.items()}
        chunk_tf.append((row["id"], tf_norm))
        term_doc_freq.update(set(tokens))

    # Delete old entries for these chunks
    chunk_ids = [r["id"] for r in chunk_rows]
    if chunk_ids:
        placeholders = ",".join("?" * len(chunk_ids))
        conn.execute(f"DELETE FROM tfidf_index WHERE chunk_id IN ({placeholders})", chunk_ids)

    # Insert new entries
    rows_to_insert = []
    for chunk_id, tf_norm in chunk_tf:
        for term, tf_val in tf_norm.items():
            df = term_doc_freq.get(term, 1)
            idf = math.log((doc_count + 1) / (df + 1)) + 1
            rows_to_insert.append((term, chunk_id, tf_val * idf))

    conn.executemany(
        "INSERT OR REPLACE INTO tfidf_index (term, chunk_id, tf_idf) VALUES (?,?,?)",
        rows_to_insert,
    )
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def add_source(name: str, origin: str, text: str, chunk_size: int = 400) -> dict:
    """Chunk and store content; return source info."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO sources (name, origin) VALUES (?, ?)", (name, origin)
    )
    source_id = cur.lastrowid

    chunks = _chunk_text(text, max_tokens=chunk_size)
    for seq, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks (source_id, seq, text, tokens) VALUES (?,?,?,?)",
            (source_id, seq, chunk, _estimate_tokens(chunk)),
        )
    conn.commit()
    _rebuild_tfidf(conn, source_id)
    conn.close()

    total_tokens = _estimate_tokens(text)
    return {
        "source_id": source_id,
        "name": name,
        "chunks": len(chunks),
        "total_tokens": total_tokens,
    }


def search_context(query: str, top_k: int = 5) -> list[dict]:
    """Return top_k most relevant chunks for the query."""
    conn = _get_conn()
    query_terms = _tokenise(query)
    if not query_terms:
        conn.close()
        return []

    placeholders = ",".join("?" * len(query_terms))
    rows = conn.execute(
        f"""
        SELECT t.chunk_id, SUM(t.tf_idf) AS score,
               c.text, c.tokens, s.name, s.id as source_id
        FROM tfidf_index t
        JOIN chunks c ON c.id = t.chunk_id
        JOIN sources s ON s.id = c.source_id
        WHERE t.term IN ({placeholders})
        GROUP BY t.chunk_id
        ORDER BY score DESC
        LIMIT ?
        """,
        (*query_terms, top_k),
    ).fetchall()
    conn.close()
    return [
        {
            "chunk_id": r["chunk_id"],
            "score": round(r["score"], 4),
            "text": r["text"],
            "tokens": r["tokens"],
            "source": r["name"],
            "source_id": r["source_id"],
        }
        for r in rows
    ]


def list_sources() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("""
        SELECT s.id, s.name, s.origin, s.added,
               COUNT(c.id) AS chunks,
               COALESCE(SUM(c.tokens), 0) AS total_tokens
        FROM sources s
        LEFT JOIN chunks c ON c.source_id = s.id
        GROUP BY s.id
        ORDER BY s.added DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_source(source_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_source_text(source_id: int, max_tokens: Optional[int] = None) -> Optional[str]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT text, tokens FROM chunks WHERE source_id = ? ORDER BY seq",
        (source_id,),
    ).fetchall()
    conn.close()
    if not rows:
        return None
    parts = []
    used = 0
    for r in rows:
        if max_tokens and used + r["tokens"] > max_tokens:
            break
        parts.append(r["text"])
        used += r["tokens"]
    return "\n\n".join(parts)


def clear_all() -> int:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM sources")
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted
