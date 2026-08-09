"""Content index of the media archive, backing the import feature's dedup.

Lives in its own SQLite file (media_index.db, next to rsync.db) because it is
a rebuildable cache, not config — deleting it only costs re-hashing — and
because the import worker thread must own its own sqlite3 connection
(connections are thread-bound). No Qt in here; callers create connections and
pass them in, plus optional progress(str) / cancelled() callables.

Hashing is lazy: refresh() only stat-walks (seconds for tens of thousands of
files); a file's blake2b digest is computed the first time something
size-collides with it, then cached until the file's size/mtime changes.
"""

import hashlib
import os
import sqlite3
from pathlib import Path

from rsync_app.db import default_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_files (
    root     TEXT NOT NULL,
    relpath  TEXT NOT NULL,
    size     INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    hash     BLOB,
    PRIMARY KEY (root, relpath)
);
CREATE INDEX IF NOT EXISTS idx_archive_size ON archive_files(root, size);
CREATE INDEX IF NOT EXISTS idx_archive_hash ON archive_files(root, hash);
"""

_CHUNK = 4 * 1024 * 1024
_COMMIT_BATCH = 500


def index_path() -> Path:
    return default_path().with_name("media_index.db")


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or index_path()))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _never(_=None) -> bool:
    return False


def hash_file(path: str, cancelled=_never) -> bytes | None:
    """blake2b-256 of a file, read in 4 MB chunks. None if cancelled."""
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        while True:
            if cancelled():
                return None
            chunk = f.read(_CHUNK)
            if not chunk:
                return h.digest()
            h.update(chunk)


def copy_hash(src: str, dst: str) -> bytes:
    """Copy src to dst while hashing it, one read of the source. Returns the
    digest of what was written."""
    h = hashlib.blake2b(digest_size=32)
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            chunk = fsrc.read(_CHUNK)
            if not chunk:
                return h.digest()
            h.update(chunk)
            fdst.write(chunk)


def refresh(conn: sqlite3.Connection, root: str,
            progress=None, cancelled=_never) -> int:
    """Stat-walk `root` and sync the index rows for it: insert new files,
    reset the cached hash of changed ones, drop vanished ones. Returns the
    number of files seen. Cancellation leaves a consistent partial index."""
    known = {
        r["relpath"]: (r["size"], r["mtime_ns"])
        for r in conn.execute(
            "SELECT relpath, size, mtime_ns FROM archive_files WHERE root = ?",
            (root,),
        )
    }
    seen = set()
    pending = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if cancelled():
                conn.commit()
                return len(seen)
            if name.endswith(".part"):   # the importer's own copy temps
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root)
            seen.add(rel)
            if known.get(rel) != (st.st_size, st.st_mtime_ns):
                conn.execute(
                    "INSERT INTO archive_files(root, relpath, size, mtime_ns, hash)"
                    " VALUES (?, ?, ?, ?, NULL)"
                    " ON CONFLICT(root, relpath) DO UPDATE SET"
                    " size = excluded.size, mtime_ns = excluded.mtime_ns,"
                    " hash = NULL",
                    (root, rel, st.st_size, st.st_mtime_ns),
                )
                pending += 1
            if pending >= _COMMIT_BATCH:
                conn.commit()
                pending = 0
            if progress and len(seen) % 2000 == 0:
                progress(f"index: {len(seen):,} files scanned…")
    for rel in known.keys() - seen:
        conn.execute(
            "DELETE FROM archive_files WHERE root = ? AND relpath = ?",
            (root, rel),
        )
    conn.commit()
    return len(seen)


def count(conn: sqlite3.Connection, root: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM archive_files WHERE root = ?", (root,)
    ).fetchone()
    return row["n"]


def get_row(conn: sqlite3.Connection, root: str, relpath: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM archive_files WHERE root = ? AND relpath = ?",
        (root, relpath),
    ).fetchone()
    return dict(row) if row else None


def by_size(conn: sqlite3.Connection, root: str, size: int) -> list[dict]:
    return [
        dict(r) for r in conn.execute(
            "SELECT * FROM archive_files WHERE root = ? AND size = ?",
            (root, size),
        )
    ]


def ensure_hash(conn: sqlite3.Connection, root: str, row: dict,
                cancelled=_never) -> bytes | None:
    """Return the digest for an index row, hashing the live file if the cache
    is empty. Re-stats first; a changed file is re-recorded (hash reset) and
    hashed fresh, a vanished file drops its row. None on cancel/vanish."""
    full = os.path.join(root, row["relpath"])
    try:
        st = os.stat(full)
    except OSError:
        conn.execute(
            "DELETE FROM archive_files WHERE root = ? AND relpath = ?",
            (root, row["relpath"]),
        )
        conn.commit()
        return None
    if (st.st_size, st.st_mtime_ns) != (row["size"], row["mtime_ns"]):
        row = dict(row, size=st.st_size, mtime_ns=st.st_mtime_ns, hash=None)
    elif row["hash"] is not None:
        return row["hash"]
    digest = hash_file(full, cancelled)
    if digest is None:
        return None
    upsert_file(conn, root, row["relpath"], st.st_size, st.st_mtime_ns, digest)
    return digest


def upsert_file(conn: sqlite3.Connection, root: str, relpath: str,
                size: int, mtime_ns: int, digest: bytes | None) -> None:
    conn.execute(
        "INSERT INTO archive_files(root, relpath, size, mtime_ns, hash)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(root, relpath) DO UPDATE SET"
        " size = excluded.size, mtime_ns = excluded.mtime_ns,"
        " hash = excluded.hash",
        (root, relpath, size, mtime_ns, digest),
    )
    conn.commit()


def dup_groups(conn: sqlite3.Connection, root: str,
               progress=None, cancelled=_never) -> list[list[dict]]:
    """Groups (≥2 members) of content-identical files under root. Only files
    whose size collides with another's are ever read."""
    groups = []
    size_rows = conn.execute(
        "SELECT size FROM archive_files WHERE root = ?"
        " GROUP BY size HAVING COUNT(*) > 1 ORDER BY size DESC",
        (root,),
    ).fetchall()
    for i, srow in enumerate(size_rows):
        if cancelled():
            break
        if progress:
            progress(f"comparing size group {i + 1:,} of {len(size_rows):,}…")
        by_hash: dict[bytes, list[dict]] = {}
        for row in by_size(conn, root, srow["size"]):
            digest = ensure_hash(conn, root, row, cancelled)
            if digest is None:
                continue
            by_hash.setdefault(digest, []).append(dict(row, hash=digest))
        groups.extend(g for g in by_hash.values() if len(g) > 1)
    return groups
