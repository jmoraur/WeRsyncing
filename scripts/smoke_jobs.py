"""Headless checks for the jobs model (0.10).

1. Conversion of a pre-0.10 database (bindings with a push/pull swap)
   into jobs with a real from and to — commands must come out identical.
2. Commands and checks read in data-flow order for every scope.
3. Two servers in one job is refused; a missing source folder blocks.

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
laptop_docs.mkdir()

# --- a 0.9-shaped database ----------------------------------------------------
c = sqlite3.connect(old)
c.executescript(f"""
CREATE TABLE source_labels (id INTEGER PRIMARY KEY, label TEXT, path TEXT);
CREATE TABLE dest_containers (id INTEGER PRIMARY KEY, label TEXT,
                              position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE dest_devices (id INTEGER PRIMARY KEY, container_id INTEGER,
    label TEXT, kind TEXT, uuid TEXT, network_target TEXT, rsh TEXT);
CREATE TABLE bindings (id INTEGER PRIMARY KEY, source_label_id INTEGER,
    dest_device_id INTEGER, dest_subpath TEXT, path_mode TEXT,
    chown_mode TEXT, chown_value TEXT, chmod_value TEXT,
    opt_archive INT DEFAULT 1, opt_verbose INT DEFAULT 1,
    opt_human_readable INT DEFAULT 1, opt_hardlinks INT DEFAULT 1,
    opt_partial INT DEFAULT 1, opt_progress INT DEFAULT 1,
    opt_compress INT DEFAULT 0, opt_dry_run INT DEFAULT 0,
    opt_delete INT DEFAULT 0, opt_update INT DEFAULT 0,
    opt_existing INT DEFAULT 0, opt_checksum INT DEFAULT 0,
    opt_inplace INT DEFAULT 0, excludes TEXT, direction TEXT);
INSERT INTO source_labels VALUES (1, 'Laptop', '{laptop_docs}'),
                                 (2, 'NAS', '/home/me/Backups/Appdata Backups');
INSERT INTO dest_containers VALUES (5, 'NAS', 0), (6, 'USB', 1);
INSERT INTO dest_devices VALUES
    (1, 6, 'USB', 'local', 'uuid-1', NULL, NULL),
    (2, 5, 'NAS', 'remote', NULL, 'root@nas:/mnt/user', NULL),
    (3, 5, 'NAS (whole box)', 'remote', NULL, 'root@nas:/', NULL);
INSERT INTO bindings (id, source_label_id, dest_device_id, dest_subpath,
                      path_mode, chown_mode, opt_delete, direction) VALUES
    (10, 1, 1, '/Documents/', 'contents', 'source', 1, 'push'),
    (11, 1, 2, 'storage/docs', 'contents', 'source', 0, 'push'),
    (12, 2, 3, 'mnt/user/backups/appdata', 'contents', 'source', 1, 'pull');
""")
c.commit()
c.close()

db = Database(old)
Database(old)  # reopening must be a no-op
print("conversion")
check("backup file written", (tmp / "rsync.db.pre-jobs.bak").exists())
devices = {d["label"]: d for d in db.list_devices()}
check("laptop device exists", devices["Laptop"]["kind"] == "laptop")
check("two server entries for one box became one device",
      [d["label"] for d in db.list_devices() if d["kind"] == "server"]
      == ["NAS"])
check("group ids kept", {g["id"] for g in db.list_groups()} == {5, 6})
jobs = {j["id"]: j for j in db.list_jobs()}
check("job ids kept", set(jobs) == {10, 11, 12})
pull = jobs[12]
check("pull job: from = NAS server path",
      pull["from_device_id"] == devices["NAS"]["id"]
      and pull["from_path"] == "/mnt/user/backups/appdata")
check("pull job: to = laptop folder, name kept",
      pull["to_device_id"] == devices["Laptop"]["id"]
      and pull["name"] == "Appdata Backups")
check("drive path stored inside the drive", jobs[10]["to_path"] == "Documents")

ctl = ConnectionsController(db, Fake({"uuid-1": "/run/media/me/USB"}),
                            Fake({devices["NAS"]["id"]: "live"}), Fake({}))
cmd = {r["id"]: r["command"] for r in ctl.jobsForScope("group", 5, 0)
       + ctl.jobsForScope("group", 6, 0)}
print("commands")
check("push to drive", cmd[10][-2:] == [f"{laptop_docs}/",
                                        "/run/media/me/USB/Documents/"])
check("push to server", cmd[11][-1] == "root@nas:/mnt/user/storage/docs/")
check("pull reads server → laptop",
      cmd[12][-2:] == ["root@nas:/mnt/user/backups/appdata/",
                       "/home/me/Backups/Appdata Backups/"])
titles = {r["id"]: r["title"] for r in ctl.jobsForScope("group", 5, 0)}
check("titles are from-device > name",
      titles == {11: "Laptop > Documents", 12: "NAS > Appdata Backups"})
check("single-job scope builds the same command as the group scope",
      ctl.jobsForScope("job", 12, 0)[0]["command"] == cmd[12])
nas, lap = devices["NAS"]["id"], devices["Laptop"]["id"]
check("source → destination scope picks only that pair",
      [r["id"] for r in ctl.jobsForScope("sourceDevice", nas, lap)] == [12]
      and [r["id"] for r in ctl.jobsForScope("sourceDevice", lap, nas)] == [11])
src_rows = [(r["rowType"], r["depth"], r["label"]) for r in ctl.bySource()
            if r["rowType"] in ("source", "device", "job")]
check("Sources view: source → destination → jobs",
      ("source", 0, "NAS") in src_rows and ("device", 1, "Laptop") in src_rows
      and ("job", 2, "Appdata Backups") in src_rows)
draft = dict(ctl.getJob(12))
check("a job's saved row previews the same command",
      ctl.previewCommand(draft) == cmd[12])

print("checks")
codes = lambda issues: {i["code"] for i in issues}
check("delete warning names the destination",
      any("Appdata Backups" in i["message"]
          for i in ctl.jobsForScope("job", 12, 0)[0]["issues"]
          if i["code"] == "W_DELETE_ENABLED"))
bad = dict(draft, from_device_id=devices["Laptop"]["id"],
           from_path=str(tmp / "missing"))
check("missing source folder blocks", "E_FROM_MISSING"
      in codes(ctl.preflightDraft(bad)))
two = dict(draft, to_device_id=devices["NAS"]["id"], to_path="/x")
check("server → server is refused", "E_TWO_SERVERS"
      in codes(ctl.preflightDraft(two)))
gone = Fake({})
ctl2 = ConnectionsController(db, gone, Fake({}), Fake({}))
check("unplugged drive blocks", "E_TO_UNAVAILABLE"
      in codes(ctl2.jobsForScope("job", 10, 0)[0]["issues"]))
check("pre-start guard stops a job whose drive is gone",
      "not connected" in (ctl2._job_pre_start_error(10, cmd[10]) or ""))

print("smoke_jobs: all checks passed")
