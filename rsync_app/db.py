import os.path
import shutil
import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Signal

from rsync_app.rsync import OPTIONS

_UNSET = object()

_BOOL_COLS = frozenset(o["key"] for o in OPTIONS)

# A job is rsync: from (device + path) → to (device + path) + options.
# Defaults for everything but the group and the two devices; option
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
JOB_COLS = ("group_id", "from_device_id", "to_device_id", *JOB_DEFAULTS)

DEVICE_COLS = ("label", "kind", "uuid", "host", "rsh")

_OPT_COLS_SQL = ",\n    ".join(
    f"{o['key']} INTEGER NOT NULL DEFAULT {int(o['default'])} "
    f"CHECK ({o['key']} IN (0, 1))"
    for o in OPTIONS
)

# Devices are places folders live: the laptop itself, a drive (found by
# filesystem UUID, path relative to its mountpoint) or a server reached
# over ssh (`host` = "user@host", path absolute on the server).
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS groups (
    id       INTEGER PRIMARY KEY,
    label    TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS devices (
    id     INTEGER PRIMARY KEY,
    label  TEXT NOT NULL,
    kind   TEXT NOT NULL CHECK (kind IN ('laptop', 'drive', 'server')),
    uuid   TEXT UNIQUE,
    host   TEXT,
    rsh    TEXT,
    CHECK (
        (kind = 'laptop' AND uuid IS NULL AND host IS NULL) OR
        (kind = 'drive'  AND uuid IS NOT NULL AND host IS NULL) OR
        (kind = 'server' AND host IS NOT NULL AND uuid IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY,
    group_id        INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS import_jobs (
    id           INTEGER PRIMARY KEY,
    label        TEXT NOT NULL,
    dump_path    TEXT NOT NULL,
    dest_path    TEXT NOT NULL,
    archive_root TEXT NOT NULL,
    copy_mode    INTEGER NOT NULL DEFAULT 0 CHECK (copy_mode IN (0, 1))
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


def _split_target(target: str) -> tuple[str, str]:
    """'root@nas:/mnt/user' -> ('root@nas', '/mnt/user')."""
    host, _, path = (target or "").partition(":")
    return host, path or "/"


def _join(base: str, sub: str) -> str:
    base, sub = (base or "").rstrip("/"), (sub or "").strip("/")
    return f"{base}/{sub}" if sub else (base or "/")


class Database(QObject):
    db_changed = Signal()

    def __init__(self, path: Path | None = None, parent=None):
        super().__init__(parent)
        self.path = path or default_path()
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        old = self._has_table("bindings")
        self._conn.executescript(SCHEMA)
        self._migrate_import_copy_mode()
        if old:
            self._migrate_to_jobs()
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

    def _migrate_to_jobs(self) -> None:
        """One-time conversion of the pre-0.10 model (source_labels /
        dest_containers / dest_devices / bindings with a push/pull swap)
        into groups / devices / jobs where every job states its real
        from and to. Ids of groups and jobs are kept. Expects the 0.9 shape
        (older DBs: run 0.9 once first). Writes rsync.db.pre-jobs.bak."""
        backup = self.path.with_name(self.path.name + ".pre-jobs.bak")
        self._conn.commit()
        if self.path.exists() and not backup.exists():
            shutil.copy2(self.path, backup)
        q = lambda sql: [dict(r) for r in self._conn.execute(sql)]
        containers = q("SELECT * FROM dest_containers")
        old_devices = q("SELECT * FROM dest_devices ORDER BY id")
        sources = {s["id"]: s for s in q("SELECT * FROM source_labels")}
        bindings = q("SELECT * FROM bindings ORDER BY id")

        c = self._conn
        c.execute("BEGIN")
        for g in containers:
            c.execute("INSERT INTO groups(id, label, position) VALUES (?,?,?)",
                      (g["id"], g["label"], g.get("position") or 0))
        laptop = c.execute("INSERT INTO devices(label, kind) VALUES "
                           "('Laptop', 'laptop')").lastrowid
        # Old servers carried a base path in their target; one server
        # (same user@host + ssh settings) becomes one device. Its name is
        # the old entry with the most jobs.
        uses = {}
        for b in bindings:
            uses[b["dest_device_id"]] = uses.get(b["dest_device_id"], 0) + 1
        new_id, servers = {}, {}
        for d in sorted(old_devices, key=lambda d: -uses.get(d["id"], 0)):
            if d["kind"] == "local":
                new_id[d["id"]] = c.execute(
                    "INSERT INTO devices(label, kind, uuid) VALUES "
                    "(?, 'drive', ?)", (d["label"], d["uuid"])).lastrowid
                continue
            host, _ = _split_target(d["network_target"])
            key = (host, d.get("rsh") or "")
            if key not in servers:
                servers[key] = c.execute(
                    "INSERT INTO devices(label, kind, host, rsh) VALUES "
                    "(?, 'server', ?, ?)",
                    (d["label"], host, d.get("rsh") or None)).lastrowid
            new_id[d["id"]] = servers[key]

        devices_by_old = {d["id"]: d for d in old_devices}
        opt_keys = [o["key"] for o in OPTIONS]
        for b in bindings:
            d = devices_by_old[b["dest_device_id"]]
            s = sources[b["source_label_id"]]
            if d["kind"] == "local":
                dev_path = (b["dest_subpath"] or "").strip("/")
            else:
                dev_path = _join(_split_target(d["network_target"])[1],
                                 b["dest_subpath"])
            laptop_side = (laptop, s["path"])
            dev_side = (new_id[d["id"]], dev_path)
            pull = b.get("direction") == "pull"
            frm, to = (dev_side, laptop_side) if pull else (laptop_side, dev_side)
            # Pull jobs were titled by their laptop folder ("Appdata
            # Backups"), not the server folder name; keep that title.
            name = os.path.basename(s["path"].rstrip("/")) if pull else ""
            row = {
                "id": b["id"], "group_id": d["container_id"], "name": name,
                "from_device_id": frm[0], "from_path": frm[1],
                "to_device_id": to[0], "to_path": to[1],
                "path_mode": b["path_mode"], "chown_mode": b["chown_mode"],
                "chown_value": b["chown_value"],
                "chmod_value": b["chmod_value"],
                "excludes": b["excludes"],
                **{k: b.get(k, int(o["default"]))
                   for k, o in zip(opt_keys, OPTIONS)},
            }
            c.execute(f"INSERT INTO jobs({', '.join(row)}) VALUES "
                      f"({', '.join('?' * len(row))})", list(row.values()))
        for t in ("bindings", "dest_devices", "source_labels",
                  "dest_containers"):
            c.execute(f"DROP TABLE {t}")
        c.execute("COMMIT")

    # --- groups -------------------------------------------------------------

    def add_group(self, *, label: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO groups(label, position) VALUES "
            "(?, (SELECT COALESCE(MAX(position) + 1, 0) FROM groups))",
            (label,))
        self._conn.commit()
        self.db_changed.emit()
        return cur.lastrowid

    def update_group(self, group_id: int, *, label=_UNSET) -> None:
        changes = self._collect_changes(["label"], locals())
        self._apply_update("groups", group_id, changes)

    def delete_group(self, group_id: int) -> None:
        self._conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        self._conn.commit()
        self.db_changed.emit()

    def list_groups(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM groups ORDER BY position, label")]

    def move_group(self, group_id: int, delta: int) -> None:
        """Swap a group with its neighbour (delta -1 = up, +1 = down).
        Renumbers every group 0..n-1 so positions stay distinct."""
        ids = [g["id"] for g in self.list_groups()]
        if group_id not in ids:
            return
        i = ids.index(group_id)
        j = i + delta
        if not 0 <= j < len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        self._conn.executemany(
            "UPDATE groups SET position = ? WHERE id = ?",
            [(pos, gid) for pos, gid in enumerate(ids)])
        self._conn.commit()
        self.db_changed.emit()

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
        # Laptop first, then by name.
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM devices ORDER BY kind != 'laptop', label")]

    # --- jobs ---------------------------------------------------------------

    def add_job(self, values: dict) -> int:
        """Insert a job. Unknown keys are ignored, missing option columns
        take the catalog defaults. Needs group_id and both device ids."""
        row = dict(JOB_DEFAULTS)
        row.update({k: _coerce(k, v) for k, v in values.items()
                    if k in JOB_COLS})
        if not all(row.get(k) for k in
                   ("group_id", "from_device_id", "to_device_id")):
            raise ValueError("job needs group_id, from_device_id, to_device_id")
        cur = self._conn.execute(
            f"INSERT INTO jobs({', '.join(row)}) VALUES "
            f"({', '.join('?' * len(row))})", list(row.values()))
        self._conn.commit()
        self.db_changed.emit()
        return cur.lastrowid

    def update_job(self, job_id: int, values: dict) -> None:
        """Update the given columns of a job; unknown keys are ignored."""
        self._apply_update("jobs", job_id,
                           {k: _coerce(k, v) for k, v in values.items()
                            if k in JOB_COLS})

    def delete_job(self, job_id: int) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()
        self.db_changed.emit()

    def list_jobs(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM jobs ORDER BY id")]

    # --- import_jobs --------------------------------------------------------

    def add_import_job(self, *, label: str, dump_path: str, dest_path: str,
                       archive_root: str, copy_mode: int = 0) -> int:
        cur = self._conn.execute(
            "INSERT INTO import_jobs(label, dump_path, dest_path, archive_root,"
            " copy_mode) VALUES (?, ?, ?, ?, ?)",
            (label, dump_path, dest_path, archive_root, int(bool(copy_mode))),
        )
        self._conn.commit()
        self.db_changed.emit()
        return cur.lastrowid

    def update_import_job(self, job_id: int, *, label=_UNSET, dump_path=_UNSET,
                          dest_path=_UNSET, archive_root=_UNSET,
                          copy_mode=_UNSET) -> None:
        if copy_mode is not _UNSET:
            copy_mode = int(bool(copy_mode))
        changes = self._collect_changes(
            ["label", "dump_path", "dest_path", "archive_root", "copy_mode"],
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
