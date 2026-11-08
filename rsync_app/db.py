import shutil
import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Signal

from rsync_app.rsync import OPTIONS

_UNSET = object()

_BOOL_COLS = frozenset(o["key"] for o in OPTIONS)

# A job is rsync: from (device + path) → to (device + path) + options.
# Defaults for everything but the bundle and the two devices; option
# defaults come straight from the rsync catalog.
JOB_DEFAULTS = {
    "name": "",
    "from_path": "",
    "to_path": "",
    "path_mode": "contents",
    "chown_mode": "source",
    "chown_value": None,
    "chmod_value": None,
    **{o["key"]: int(o["default"]) for o in OPTIONS},
    "excludes": None,
}

# Every writable jobs column.
JOB_COLS = ("bundle_id", "from_device_id", "to_device_id", *JOB_DEFAULTS)

DEVICE_COLS = ("label", "kind", "uuid", "host", "rsh")

_OPT_COLS_SQL = ",\n    ".join(
    f"{o['key']} INTEGER NOT NULL DEFAULT {int(o['default'])} "
    f"CHECK ({o['key']} IN (0, 1))"
    for o in OPTIONS
)

_JOBS_SQL = f"""
CREATE TABLE IF NOT EXISTS {{table}} (
    id              INTEGER PRIMARY KEY,
    bundle_id       INTEGER REFERENCES bundles(id) ON DELETE SET NULL,
    name            TEXT NOT NULL DEFAULT '',
    from_device_id  INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    from_path       TEXT NOT NULL DEFAULT '',
    to_device_id    INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    to_path         TEXT NOT NULL DEFAULT '',
    path_mode       TEXT NOT NULL DEFAULT 'contents'
                    CHECK (path_mode IN ('contents', 'folder')),
    chown_mode      TEXT NOT NULL DEFAULT 'source'
                    CHECK (chown_mode IN ('source', 'dest', 'custom')),
    chown_value     TEXT,
    chmod_value     TEXT,
    {_OPT_COLS_SQL},
    excludes        TEXT
);
"""

# Devices are places folders live: the laptop itself, a drive (found by
# filesystem UUID, path relative to its mountpoint) or a server reached
# over ssh (`host` = "user@host", path absolute on the server). Their
# position orders the top rows of the list.
# A bundle holds jobs (and other bundles) that share one from-device and
# one to-device; it only shapes the list and what a sync button runs.
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS devices (
    id       INTEGER PRIMARY KEY,
    label    TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('laptop', 'drive', 'server')),
    uuid     TEXT UNIQUE,
    host     TEXT,
    rsh      TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    CHECK (
        (kind = 'laptop' AND uuid IS NULL AND host IS NULL) OR
        (kind = 'drive'  AND uuid IS NOT NULL AND host IS NULL) OR
        (kind = 'server' AND host IS NOT NULL AND uuid IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS bundles (
    id              INTEGER PRIMARY KEY,
    label           TEXT NOT NULL,
    parent_id       INTEGER REFERENCES bundles(id) ON DELETE CASCADE,
    from_device_id  INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    to_device_id    INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id           INTEGER PRIMARY KEY,
    label        TEXT NOT NULL,
    dump_path    TEXT NOT NULL,
    dest_path    TEXT NOT NULL,
    archive_root TEXT NOT NULL,
    copy_mode    INTEGER NOT NULL DEFAULT 0 CHECK (copy_mode IN (0, 1)),
    phone        TEXT NOT NULL DEFAULT ''
);
"""


def default_path() -> Path:
    base = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    base.mkdir(parents=True, exist_ok=True)
    return base / "rsync.db"


def _coerce(col: str, value):
    if col in _BOOL_COLS and value is not None:
        return int(bool(value))
    return value


class Database(QObject):
    db_changed = Signal()

    def __init__(self, path: Path | None = None, parent=None):
        super().__init__(parent)
        self.path = path or default_path()
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._has_table("bindings"):
            raise RuntimeError(
                "This database is from before WeRsyncing 0.10; open it with "
                "0.10 once to convert it first.")
        has_groups = self._has_table("groups")
        self._conn.executescript(SCHEMA)
        self._migrate_import_copy_mode()
        if has_groups:
            self._migrate_to_bundles()
        self._conn.executescript(_JOBS_SQL.format(table="jobs"))
        if not self._conn.execute(
                "SELECT 1 FROM devices WHERE kind = 'laptop'").fetchone():
            self._conn.execute(
                "INSERT INTO devices(label, kind) VALUES ('Laptop', 'laptop')")
        self._conn.commit()

    def _has_table(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,)).fetchone() is not None

    def _migrate_import_copy_mode(self) -> None:
        """Add copy_mode to a pre-existing import_jobs table (0 = the
        original move-and-clean behavior)."""
        have = {row["name"] for row in
                self._conn.execute("PRAGMA table_info(import_jobs)")}
        if "copy_mode" not in have:
            self._conn.execute(
                "ALTER TABLE import_jobs ADD COLUMN copy_mode "
                "INTEGER NOT NULL DEFAULT 0 CHECK (copy_mode IN (0, 1))"
            )
        if "phone" not in have:     # 0.12: which phone a copy job reads
            self._conn.execute(
                "ALTER TABLE import_jobs ADD COLUMN phone TEXT NOT NULL DEFAULT ''"
            )

    def _migrate_to_bundles(self) -> None:
        """One-time conversion of the 0.10 shape (groups + jobs.group_id)
        to 0.11: groups go, their order becomes the order of the devices
        named like them (others follow), jobs lose group_id and start
        outside any bundle. Every job keeps its id and its columns.
        Writes rsync.db.pre-bundles.bak first."""
        backup = self.path.with_name(self.path.name + ".pre-bundles.bak")
        self._conn.commit()
        if self.path.exists() and not backup.exists():
            shutil.copy2(self.path, backup)
        c = self._conn
        cols = {r["name"] for r in c.execute("PRAGMA table_info(devices)")}
        if "position" not in cols:
            c.execute("ALTER TABLE devices ADD COLUMN position "
                      "INTEGER NOT NULL DEFAULT 0")
        groups = [g["label"] for g in c.execute(
            "SELECT label FROM groups ORDER BY position, label")]
        devices = [dict(d) for d in c.execute(
            "SELECT id, label, kind FROM devices "
            "ORDER BY kind != 'laptop', label")]
        rank = lambda d: (groups.index(d["label"]) if d["label"] in groups
                          else len(groups))
        c.executemany("UPDATE devices SET position = ? WHERE id = ?",
                      [(pos, d["id"]) for pos, d in
                       enumerate(sorted(devices, key=rank))])
        keep = [r["name"] for r in c.execute("PRAGMA table_info(jobs)")
                if r["name"] != "group_id"]
        c.commit()
        c.execute("BEGIN")
        c.execute(_JOBS_SQL.format(table="jobs_new"))
        c.execute(f"INSERT INTO jobs_new({', '.join(keep)}) "
                  f"SELECT {', '.join(keep)} FROM jobs")
        c.execute("DROP TABLE jobs")
        c.execute("ALTER TABLE jobs_new RENAME TO jobs")
        c.execute("DROP TABLE groups")
        c.execute("COMMIT")

    # --- devices ------------------------------------------------------------

    def add_device(self, values: dict) -> int:
        row = {k: values.get(k) for k in DEVICE_COLS}
        cur = self._conn.execute(
            f"INSERT INTO devices({', '.join(row)}) VALUES "
            f"({', '.join('?' * len(row))})", list(row.values()))
        self._conn.commit()
        self.db_changed.emit()
        return cur.lastrowid

    def update_device(self, device_id: int, values: dict) -> None:
        self._apply_update("devices", device_id,
                           {k: v for k, v in values.items()
                            if k in DEVICE_COLS and k != "kind"})

    def delete_device(self, device_id: int) -> None:
        # The laptop is built in and can't be deleted.
        self._conn.execute(
            "DELETE FROM devices WHERE id = ? AND kind != 'laptop'",
            (device_id,))
        self._conn.commit()
        self.db_changed.emit()

    def list_devices(self) -> list[dict]:
        # The user's order (↑/↓ on the top rows), then laptop first, name.
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM devices ORDER BY position, kind != 'laptop', label")]

    def move_device(self, device_id: int, delta: int,
                    among: list[int] | None = None) -> None:
        """Swap a device with its neighbour among `among` (the top rows
        the user sees; all devices if None). delta -1 = up, +1 = down.
        Renumbers every device so positions stay distinct."""
        ids = [d["id"] for d in self.list_devices()]
        shown = [i for i in ids if among is None or i in among]
        if device_id not in shown:
            return
        k = shown.index(device_id) + delta
        if not 0 <= k < len(shown):
            return
        i, j = ids.index(device_id), ids.index(shown[k])
        ids[i], ids[j] = ids[j], ids[i]
        self._conn.executemany(
            "UPDATE devices SET position = ? WHERE id = ?",
            [(pos, did) for pos, did in enumerate(ids)])
        self._conn.commit()
        self.db_changed.emit()

    # --- bundles ------------------------------------------------------------

    def list_bundles(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM bundles ORDER BY label COLLATE NOCASE, id")]

    def add_bundle(self, *, label: str, from_device_id: int,
                   to_device_id: int, parent_id: int | None = None,
                   job_ids=(), bundle_ids=()) -> int:
        """Create a bundle and move the given jobs / bundles into it."""
        c = self._conn
        bid = c.execute(
            "INSERT INTO bundles(label, parent_id, from_device_id, "
            "to_device_id) VALUES (?, ?, ?, ?)",
            (label, parent_id, from_device_id, to_device_id)).lastrowid
        c.executemany("UPDATE jobs SET bundle_id = ? WHERE id = ?",
                      [(bid, j) for j in job_ids])
        c.executemany("UPDATE bundles SET parent_id = ? WHERE id = ?",
                      [(bid, b) for b in bundle_ids if b != bid])
        self._commit_pruned()
        return bid

    def rename_bundle(self, bundle_id: int, label: str) -> None:
        self._apply_update("bundles", bundle_id, {"label": label})

    def unbundle(self, bundle_id: int) -> None:
        """Remove a bundle; what it held moves up one level."""
        c = self._conn
        row = c.execute("SELECT parent_id FROM bundles WHERE id = ?",
                        (bundle_id,)).fetchone()
        if row is None:
            return
        c.execute("UPDATE jobs SET bundle_id = ? WHERE bundle_id = ?",
                  (row["parent_id"], bundle_id))
        c.execute("UPDATE bundles SET parent_id = ? WHERE parent_id = ?",
                  (row["parent_id"], bundle_id))
        c.execute("DELETE FROM bundles WHERE id = ?", (bundle_id,))
        self._commit_pruned()

    def take_out(self, job_ids=(), bundle_ids=()) -> None:
        """Move jobs / bundles one level up, out of the bundle they're in."""
        c = self._conn
        c.executemany(
            "UPDATE jobs SET bundle_id = (SELECT parent_id FROM bundles "
            "WHERE bundles.id = jobs.bundle_id) WHERE id = ?",
            [(j,) for j in job_ids])
        c.executemany(
            "UPDATE bundles SET parent_id = (SELECT p.parent_id FROM bundles p "
            "WHERE p.id = bundles.parent_id) WHERE id = ?",
            [(b,) for b in bundle_ids])
        self._commit_pruned()

    def _commit_pruned(self) -> None:
        """Drop bundles left empty (no jobs, no bundles inside), commit,
        notify."""
        while self._conn.execute(
                "DELETE FROM bundles WHERE id NOT IN (SELECT bundle_id FROM "
                "jobs WHERE bundle_id IS NOT NULL) AND id NOT IN (SELECT "
                "parent_id FROM bundles WHERE parent_id IS NOT NULL)"
                ).rowcount:
            pass
        self._conn.commit()
        self.db_changed.emit()

    # --- jobs ---------------------------------------------------------------

    def add_job(self, values: dict) -> int:
        """Insert a job. Unknown keys are ignored, missing option columns
        take the catalog defaults. Needs both device ids."""
        row = dict(JOB_DEFAULTS)
        row.update({k: _coerce(k, v) for k, v in values.items()
                    if k in JOB_COLS})
        if not all(row.get(k) for k in ("from_device_id", "to_device_id")):
            raise ValueError("job needs from_device_id and to_device_id")
        cur = self._conn.execute(
            f"INSERT INTO jobs({', '.join(row)}) VALUES "
            f"({', '.join('?' * len(row))})", list(row.values()))
        self._conn.commit()
        self.db_changed.emit()
        return cur.lastrowid

    def update_job(self, job_id: int, values: dict) -> None:
        """Update the given columns of a job; unknown keys are ignored.
        A job whose devices no longer match its bundle leaves the bundle."""
        self._apply_update("jobs", job_id,
                           {k: _coerce(k, v) for k, v in values.items()
                            if k in JOB_COLS})
        self._conn.execute(
            "UPDATE jobs SET bundle_id = NULL WHERE id = ? AND bundle_id IN "
            "(SELECT b.id FROM bundles b WHERE b.from_device_id != "
            "jobs.from_device_id OR b.to_device_id != jobs.to_device_id)",
            (job_id,))
        self._commit_pruned()

    def delete_job(self, job_id: int) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._commit_pruned()

    def list_jobs(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM jobs ORDER BY id")]

    # --- import_jobs --------------------------------------------------------

    def add_import_job(self, *, label: str, dump_path: str, dest_path: str,
                       archive_root: str, copy_mode: int = 0,
                       phone: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO import_jobs(label, dump_path, dest_path, archive_root,"
            " copy_mode, phone) VALUES (?, ?, ?, ?, ?, ?)",
            (label, dump_path, dest_path, archive_root, int(bool(copy_mode)),
             phone),
        )
        self._conn.commit()
        self.db_changed.emit()
        return cur.lastrowid

    def update_import_job(self, job_id: int, *, label=_UNSET, dump_path=_UNSET,
                          dest_path=_UNSET, archive_root=_UNSET,
                          copy_mode=_UNSET, phone=_UNSET) -> None:
        if copy_mode is not _UNSET:
            copy_mode = int(bool(copy_mode))
        changes = self._collect_changes(
            ["label", "dump_path", "dest_path", "archive_root", "copy_mode",
             "phone"],
            locals(),
        )
        self._apply_update("import_jobs", job_id, changes)

    def delete_import_job(self, job_id: int) -> None:
        self._conn.execute(
            "DELETE FROM import_jobs WHERE id = ?", (job_id,)
        )
        self._conn.commit()
        self.db_changed.emit()

    def list_import_jobs(self) -> list[dict]:
        return [
            dict(r) for r in self._conn.execute(
                "SELECT * FROM import_jobs ORDER BY label"
            )
        ]

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _collect_changes(cols: list[str], scope: dict) -> dict:
        changes = {}
        for col in cols:
            v = scope[col]
            if v is _UNSET:
                continue
            changes[col] = _coerce(col, v)
        return changes

    def _apply_update(self, table: str, row_id: int, changes: dict) -> None:
        if not changes:
            return
        sets = ", ".join(f"{c} = ?" for c in changes)
        self._conn.execute(
            f"UPDATE {table} SET {sets} WHERE id = ?",
            [*changes.values(), row_id],
        )
        self._conn.commit()
        self.db_changed.emit()
