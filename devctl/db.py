"""Persistent SQLite-backed project registry for devctl.

This is a drop-in replacement for the original db.py.  It keeps the same
public API (`list_projects`, `touch`, `add_project`, `remove_project`,
`get_project`) but makes three performance improvements:

1. Module-level connection reuse
   Opening a SQLite file has overhead (file stat, lock check, page cache
   init).  We open it once per process and reuse the connection object.

2. WAL journal mode
   Write-Ahead Logging lets reads proceed without blocking on a concurrent
   write.  For a CLI tool that may be called from scripts or multiplexers
   that run multiple instances in parallel, this avoids the occasional
   "database is locked" error and speeds up reads.

3. PRAGMA synchronous = NORMAL (instead of FULL)
   The default FULL syncs to disk on every commit.  NORMAL is still safe
   with WAL (you won't get corruption on a crash) but skips the expensive
   fsync on every write.

⚠  If your existing db.py stores data as JSON / TOML rather than SQLite,
   keep your original storage format and apply only the "defer imports"
   pattern from cli.py / jump.py.  The function signatures below are
   inferred from jump.py's usage of db.list_projects() and db.touch().
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TypedDict

# ── Paths ─────────────────────────────────────────────────────────────────────

_DB_DIR: Path = Path.home() / ".devctl"
_DB_PATH: Path = _DB_DIR / "devctl.db"

# ── Types ─────────────────────────────────────────────────────────────────────


class Project(TypedDict):
    name: str
    path: str


# ── Connection management ─────────────────────────────────────────────────────

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    """Return the module-level connection, creating it on first call."""
    global _conn
    if _conn is not None:
        return _conn

    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row  # allow dict-style row access

    # ── One-time setup pragmas ────────────────────────────────────────────────
    conn.execute("PRAGMA journal_mode=WAL")       # concurrent-friendly
    conn.execute("PRAGMA synchronous=NORMAL")     # safe + faster than FULL
    conn.execute("PRAGMA foreign_keys=ON")

    # ── Schema migration (idempotent) ─────────────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            name      TEXT NOT NULL PRIMARY KEY,
            path      TEXT NOT NULL,
            last_used REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    _conn = conn
    return _conn


# ── Public API ────────────────────────────────────────────────────────────────


def list_projects() -> list[Project]:
    """Return all registered projects, most-recently used first."""
    rows = _get_conn().execute(
        "SELECT name, path FROM projects ORDER BY last_used DESC"
    ).fetchall()
    return [{"name": r["name"], "path": r["path"]} for r in rows]


def get_project(name: str) -> Project | None:
    """Look up a single project by exact name; returns None if not found."""
    row = _get_conn().execute(
        "SELECT name, path FROM projects WHERE name = ?", (name,)
    ).fetchone()
    return {"name": row["name"], "path": row["path"]} if row else None


def add_project(name: str, path: str) -> None:
    """Register a project (upsert — updates path if name already exists)."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO projects (name, path, last_used)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET path = excluded.path
        """,
        (name, str(Path(path).expanduser().resolve()), time.time()),
    )
    conn.commit()


def remove_project(name: str) -> bool:
    """Unregister a project. Returns True if the project existed."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM projects WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount > 0


def touch(name: str) -> None:
    """Update last_used for *name* so it floats to the top of `list_projects`."""
    conn = _get_conn()
    conn.execute(
        "UPDATE projects SET last_used = ? WHERE name = ?",
        (time.time(), name),
    )
    conn.commit()