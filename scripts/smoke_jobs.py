"""Headless checks for the jobs model (0.11: devices on top, bundles).

1. Conversion of a 0.10 database (groups + jobs.group_id): groups go,
   their order becomes the device order, every job keeps its id, columns
   and command.
2. Bundles: nest, the rule (one device pair, one level), unbundle, take
   out, empty bundles disappear, a job that changes devices leaves its
   bundle. Both views show the same bundles.
3. Commands and checks read in data-flow order for every scope.

Run: PYTHONPATH=. python3 -sP scripts/smoke_jobs.py
"""
import sqlite3
import tempfile
from pathlib import Path

from rsync_app.controllers import ConnectionsController
from rsync_app.db import Database


def check(label, condition):
    assert condition, label
    print(f"  ok  {label}")


class Fake:
    def __init__(self, state):
        self._s = state

    def state(self):
        return dict(self._s)

    def refresh(self):
        pass

    def set_pre_start_check(self, fn):
        pass


tmp = Path(tempfile.mkdtemp())
old = tmp / "rsync.db"
laptop_docs = tmp / "Documents"
for sub in ("Family", "Jan"):
    (laptop_docs / sub).mkdir(parents=True)

# --- a 0.10-shaped database ---------------------------------------------------
OPTS = ("opt_archive", "opt_verbose", "opt_human_readable", "opt_hardlinks",
        "opt_partial", "opt_progress", "opt_compress", "opt_dry_run",
        "opt_delete", "opt_update", "opt_existing", "opt_checksum",
        "opt_inplace")
COLS = ("id, name, from_device_id, from_path, to_device_id, to_path, "
        "chown_mode, chown_value, chmod_value, excludes, " + ", ".join(OPTS))
c = sqlite3.connect(old)
c.executescript(f"""
CREATE TABLE groups (id INTEGER PRIMARY KEY, label TEXT NOT NULL,
                     position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE devices (id INTEGER PRIMARY KEY, label TEXT NOT NULL,
    kind TEXT NOT NULL, uuid TEXT UNIQUE, host TEXT, rsh TEXT);
CREATE TABLE jobs (id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    from_device_id INTEGER NOT NULL, from_path TEXT NOT NULL DEFAULT '',
    to_device_id INTEGER NOT NULL, to_path TEXT NOT NULL DEFAULT '',
    path_mode TEXT NOT NULL DEFAULT 'contents',
    chown_mode TEXT NOT NULL DEFAULT 'source',
    chown_value TEXT, chmod_value TEXT,
    {", ".join(f"{o} INTEGER NOT NULL DEFAULT 0" for o in OPTS)},
    excludes TEXT);
CREATE TABLE import_jobs (id INTEGER PRIMARY KEY, label TEXT NOT NULL,
    dump_path TEXT NOT NULL, dest_path TEXT NOT NULL,
    archive_root TEXT NOT NULL, copy_mode INTEGER NOT NULL DEFAULT 0);
INSERT INTO groups VALUES (5, 'NAS', 0), (6, 'USB', 1);
INSERT INTO devices VALUES (1, 'Laptop', 'laptop', NULL, NULL, NULL),
    (2, 'USB', 'drive', 'uuid-1', NULL, NULL),
    (3, 'NAS', 'server', NULL, 'root@nas', NULL);
INSERT INTO jobs (id, group_id, name, from_device_id, from_path,
                  to_device_id, to_path, chown_mode, chown_value,
                  chmod_value, opt_archive, opt_delete, excludes) VALUES
    (10, 6, '', 1, '{laptop_docs}/Family', 2, 'Documents/Family',
        'source', NULL, NULL, 1, 1, NULL),
    (11, 5, '', 1, '{laptop_docs}/Family', 3, '/mnt/user/docs/Family',
        'custom', '99:100', 'D775,F664', 1, 1, '.tresorit/'),
    (12, 5, 'Appdata Backups', 3, '/mnt/user/backups/appdata', 1,
        '/home/me/Backups/Appdata Backups', 'source', NULL, NULL, 1, 1, NULL),
    (13, 5, '', 1, '{laptop_docs}/Jan', 3, '/mnt/user/docs/Jan',
        'custom', '99:100', 'D775,F664', 1, 1, NULL);
""")
c.commit()
before = {r[0]: r[1:] for r in c.execute(f"SELECT {COLS} FROM jobs")}
c.close()

db = Database(old)
Database(old)  # reopening must be a no-op
print("conversion")
check("backup file written", (tmp / "rsync.db.pre-bundles.bak").exists())
check("groups table gone", not db._has_table("groups"))
check("device order follows the old group order (NAS, USB, then the rest)",
      [d["label"] for d in db.list_devices()] == ["NAS", "USB", "Laptop"])
c = sqlite3.connect(old)
after = {r[0]: r[1:] for r in c.execute(f"SELECT {COLS} FROM jobs")}
c.close()
check("every job kept its id and every column", after == before)
check("no job in a bundle yet",
      all(j["bundle_id"] is None for j in db.list_jobs()))

LAP, USB, NAS = 1, 2, 3
ctl = ConnectionsController(db, Fake({"uuid-1": "/run/media/me/USB"}),
                            Fake({NAS: "live"}), Fake({}))
cmd = {r["id"]: r["command"] for r in ctl.jobsForScope("to", USB, 0)
       + ctl.jobsForScope("to", NAS, 0) + ctl.jobsForScope("to", LAP, 0)}
print("commands")
check("push to drive", cmd[10][-2:] == [f"{laptop_docs}/Family/",
                                        "/run/media/me/USB/Documents/Family/"])
check("push to server", cmd[11][-1] == "root@nas:/mnt/user/docs/Family/")
check("pull reads server → laptop",
      cmd[12][-2:] == ["root@nas:/mnt/user/backups/appdata/",
                       "/home/me/Backups/Appdata Backups/"])
check("titles are from-device > name",
      {r["id"]: r["title"] for r in ctl.jobsForScope("from", NAS, 0)}
      == {12: "NAS > Appdata Backups"})
check("from scope gives the same commands as the to scopes",
      {r["id"]: r["command"] for r in ctl.jobsForScope("from", LAP, 0)}
      == {k: cmd[k] for k in (10, 11, 13)})
check("pair scope picks only that pair",
      [r["id"] for r in ctl.jobsForScope("pair", LAP, NAS)] == [11, 13]
      and [r["id"] for r in ctl.jobsForScope("pair", NAS, LAP)] == [12])
check("single-job scope builds the same command",
      ctl.jobsForScope("job", 12, 0)[0]["command"] == cmd[12])
draft = dict(ctl.getJob(12))
check("a job's saved row previews the same command",
      ctl.previewCommand(draft) == cmd[12])

print("bundles")
bundle_of = lambda jid: db._conn.execute(
    "SELECT bundle_id FROM jobs WHERE id = ?", (jid,)).fetchone()[0]
check("rule: different device pairs can't be bundled",
      ctl.bundleProblem(["j10", "j11"])
      == "they don't share the same source and destination")
check("rule: one job alone can be bundled", ctl.bundleProblem(["j12"]) == "")
docs = ctl.bundle(["j11", "j13"], "Documents")
check("bundle made, both jobs inside",
      bundle_of(11) == docs and bundle_of(13) == docs)
fam = ctl.bundle(["j11"], "Family only")
check("bundle inside a bundle", ctl.getBundle(fam)["parent_id"] == docs)
check("rule: items at different levels can't be bundled",
      ctl.bundleProblem(["j11", "j13"]) == "they are not at the same level")
check("bundle scope runs every job inside, at any depth",
      [r["id"] for r in ctl.jobsForScope("bundle", docs, 0)] == [11, 13]
      and [r["id"] for r in ctl.jobsForScope("bundle", fam, 0)] == [11])
check("bundle scope keeps the commands",
      {r["id"]: r["command"] for r in ctl.jobsForScope("bundle", docs, 0)}
      == {11: cmd[11], 13: cmd[13]})


def shape(rows):
    return [(r["rowType"], r["depth"], r["label"]) for r in rows
            if r["rowType"] in ("top", "device", "bundle", "job")]


dest = shape(ctl.byDestination())
src = shape(ctl.bySource())
check("Destinations: to-device → from-device → bundles → jobs",
      dest[dest.index(("top", 0, "NAS")):][:6] == [
          ("top", 0, "NAS"), ("device", 1, "Laptop"),
          ("bundle", 2, "Documents"), ("bundle", 3, "Family only"),
          ("job", 4, "Family"), ("job", 3, "Jan")])
check("Sources: from-device → to-device → the same bundles",
      src[src.index(("device", 1, "NAS")):][:5] == [
          ("device", 1, "NAS"), ("bundle", 2, "Documents"),
          ("bundle", 3, "Family only"), ("job", 4, "Family"),
          ("job", 3, "Jan")])
check("take out: only for things inside a bundle",
      ctl.canTakeOut(["j11"]) and not ctl.canTakeOut(["j12"]))
ctl.takeOut(["j11"])
check("taken out: job one level up, emptied bundle removed",
      bundle_of(11) == docs and not ctl.getBundle(fam))
inner = ctl.bundle(["j13"], "Jan only")
ctl.unbundle(docs)
check("unbundle: what it held moves up a level",
      ctl.getBundle(inner)["parent_id"] is None and bundle_of(11) is None)
ctl.updateJob(13, {"to_device_id": USB, "to_path": "Documents/Jan"})
check("a job moved to other devices leaves its bundle (bundle removed)",
      bundle_of(13) is None and not ctl.getBundle(inner))
# Sources view top rows: NAS, Laptop (USB has no outgoing jobs).
ctl.moveDevice(LAP, -1, "source")
check("↑ on a top row swaps it with the top row above in that view",
      [d["label"] for d in db.list_devices()] == ["Laptop", "USB", "NAS"])

print("checks")
codes = lambda issues: {i["code"] for i in issues}
check("delete warning names the destination",
      any("Appdata Backups" in i["message"]
          for i in ctl.jobsForScope("job", 12, 0)[0]["issues"]
          if i["code"] == "W_DELETE_ENABLED"))
bad = dict(draft, from_device_id=LAP, from_path=str(tmp / "missing"))
check("missing source folder blocks", "E_FROM_MISSING"
      in codes(ctl.preflightDraft(bad)))
two = dict(draft, to_device_id=NAS, to_path="/x")
check("server → server is refused", "E_TWO_SERVERS"
      in codes(ctl.preflightDraft(two)))
ctl2 = ConnectionsController(db, Fake({}), Fake({}), Fake({}))
check("unplugged drive blocks", "E_TO_UNAVAILABLE"
      in codes(ctl2.jobsForScope("job", 10, 0)[0]["issues"]))
check("pre-start guard stops a job whose drive is gone",
      "not connected" in (ctl2._job_pre_start_error(10, cmd[10]) or ""))

print("smoke_jobs: all checks passed")
