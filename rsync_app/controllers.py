"""Controller layer between QML and the data/runtime layer.

ConnectionsController is a thin `@Slot`-decorated facade: QML drives
Database writes through it and pulls pre-shaped read snapshots.

The model is plain rsync: a job is from (device + path) → to (device +
path) + options. Devices are the laptop, drives (by filesystem UUID) and
servers (over ssh). Bundles (nestable) gather jobs that share one
from-device and one to-device; they only shape the list and what a sync
button runs. Every place
that shows or runs a job resolves its two sides with `_endpoint`, so the
list, the confirm popups and the command can never disagree.

Liveness join lives here: the tree builders merge MountWatcher +
RemoteProbeWatcher state into the row dicts they return.
"""
import os.path

from PySide6.QtCore import QObject, Slot

from rsync_app import hash_index
from rsync_app.db import JOB_COLS, JOB_DEFAULTS, Database
from rsync_app.importer import ImportWorker
from rsync_app.mounts import MountWatcher
from rsync_app.preflight import check_import_job, check_job
from rsync_app.probes import RemoteProbeWatcher
from rsync_app.rsync import OPTIONS, build_rsync_argv
from rsync_app.runner import SyncRunner


def _leaf(path: str) -> str:
    return os.path.basename((path or "").rstrip("/"))


class ConnectionsController(QObject):
    def __init__(self, db: Database, mounts: MountWatcher,
                 probes: RemoteProbeWatcher, runner: SyncRunner,
                 parent=None):
        super().__init__(parent)
        self._db = db
        self._mounts = mounts
        self._probes = probes
        self._runner = runner
        runner.set_pre_start_check(self._job_pre_start_error)

    # =====================================================================
    # writes — bundles + device order
    # =====================================================================
    # Selection keys: "j<id>" = job, "b<id>" = bundle.

    def _items(self, keys: list) -> list[dict] | None:
        """Selected jobs / bundles as {kind, id, pair, parent}; None if a
        key no longer exists."""
        jobs = {j["id"]: j for j in self._db.list_jobs()}
        bundles = {b["id"]: b for b in self._db.list_bundles()}
        out = []
        for k in keys:
            kind, id_ = str(k)[:1], int(str(k)[1:] or 0)
            row = (jobs if kind == "j" else bundles).get(id_)
            if row is None:
                return None
            parent = row["bundle_id"] if kind == "j" else row["parent_id"]
            out.append({"kind": kind, "id": id_,
                        "pair": (row["from_device_id"], row["to_device_id"]),
                        "parent": parent if parent in bundles else None})
        return out

    @Slot("QVariantList", result=str)
    def bundleProblem(self, keys: list) -> str:
        """Why these can't be bundled together ("" = they can)."""
        items = self._items(keys)
        if not items:
            return "nothing selected"
        if len({i["pair"] for i in items}) > 1:
            return "they don't share the same source and destination"
        if len({i["parent"] for i in items}) > 1:
            return "they are not at the same level"
        return ""

    @Slot("QVariantList", result=bool)
    def canTakeOut(self, keys: list) -> bool:
        items = self._items(keys)
        return bool(items) and all(i["parent"] is not None for i in items)

    @Slot("QVariantList", str, result=int)
    def bundle(self, keys: list, label: str) -> int:
        if self.bundleProblem(keys) or not label.strip():
            return -1
        items = self._items(keys)
        frm, to = items[0]["pair"]
        return self._db.add_bundle(
            label=label.strip(), from_device_id=frm, to_device_id=to,
            parent_id=items[0]["parent"],
            job_ids=[i["id"] for i in items if i["kind"] == "j"],
            bundle_ids=[i["id"] for i in items if i["kind"] == "b"])

    @Slot(int, str)
    def renameBundle(self, bundle_id: int, label: str) -> None:
        if label.strip():
            self._db.rename_bundle(bundle_id, label.strip())

    @Slot(int)
    def unbundle(self, bundle_id: int) -> None:
        self._db.unbundle(bundle_id)

    @Slot("QVariantList")
    def takeOut(self, keys: list) -> None:
        items = self._items(keys) or []
        self._db.take_out(
            job_ids=[i["id"] for i in items if i["kind"] == "j"],
            bundle_ids=[i["id"] for i in items if i["kind"] == "b"])

    @Slot(int, result="QVariantMap")
    def getBundle(self, bundle_id: int) -> dict:
        return next((b for b in self._db.list_bundles()
                     if b["id"] == bundle_id), {})

    @Slot(int, int, str)
    def moveDevice(self, device_id: int, delta: int, view: str) -> None:
        """↑/↓ on a top row: swap with the neighbouring top row of the
        view the user is looking at."""
        side = "to_device_id" if view == "group" else "from_device_id"
        self._db.move_device(device_id, delta,
                             among=[j[side] for j in self._db.list_jobs()])

    # =====================================================================
    # writes — devices
    # =====================================================================

    @staticmethod
    def _device_fields(draft: dict) -> dict:
        """Normalize a device draft: kind decides which identity column
        is kept; the others are forced NULL (schema CHECK requires it)."""
        kind = draft.get("kind") or "drive"
        return {
            "label": str(draft.get("label") or "").strip(),
            "kind": kind,
            "uuid": (draft.get("uuid") or None) if kind == "drive" else None,
            "host": (str(draft.get("host") or "").strip() or None)
                    if kind == "server" else None,
            "rsh": (str(draft.get("rsh") or "").strip() or None)
                   if kind == "server" else None,
        }

    @Slot("QVariantMap", result=int)
    def addDevice(self, draft: dict) -> int:
        return self._db.add_device(self._device_fields(draft))

    @Slot(int, "QVariantMap")
    def updateDevice(self, device_id: int, draft: dict) -> None:
        self._db.update_device(device_id, self._device_fields(draft))

    @Slot(int)
    def deleteDevice(self, device_id: int) -> None:
        self._db.delete_device(device_id)

    # =====================================================================
    # writes — jobs
    # =====================================================================

    @staticmethod
    def _job_fields(draft: dict) -> dict:
        out = {k: v for k, v in draft.items() if k in JOB_COLS}
        for k in ("from_path", "to_path", "name"):
            if k in out:
                out[k] = str(out[k] or "").strip()
        return out

    @Slot("QVariantMap", result=int)
    def addJob(self, draft: dict) -> int:
        return self._db.add_job(self._job_fields(draft))

    @Slot(int, "QVariantMap")
    def updateJob(self, job_id: int, draft: dict) -> None:
        self._db.update_job(job_id, self._job_fields(draft))

    @Slot(int)
    def deleteJob(self, job_id: int) -> None:
        self._db.delete_job(job_id)

    # =====================================================================
    # writes — import_jobs
    # =====================================================================

    @staticmethod
    def _import_job_fields(draft: dict) -> dict:
        def path(key):
            return os.path.expanduser(str(draft.get(key) or "").strip())
        return {
            "label": str(draft.get("label") or "").strip(),
            "dump_path": path("dump_path"),
            "dest_path": path("dest_path"),
            "archive_root": path("archive_root"),
            "copy_mode": int(bool(draft.get("copy_mode"))),
        }

    @Slot("QVariantMap", result=int)
    def addImportJob(self, draft: dict) -> int:
        return self._db.add_import_job(**self._import_job_fields(draft))

    @Slot(int, "QVariantMap")
    def updateImportJob(self, job_id: int, draft: dict) -> None:
        self._db.update_import_job(job_id, **self._import_job_fields(draft))

    @Slot(int)
    def deleteImportJob(self, job_id: int) -> None:
        self._db.delete_import_job(job_id)

    @Slot(int, result="QVariantMap")
    def getImportJob(self, job_id: int) -> dict:
        return next((r for r in self._db.list_import_jobs()
                     if r["id"] == job_id), {})

    # =====================================================================
    # import execution — ImportWorker jobs through the same runner
    # =====================================================================

    @Slot("QVariantMap", result=list)
    def preflightImportDraft(self, draft: dict) -> list[dict]:
        job = self._import_job_fields(draft)
        return check_import_job(
            job, index_empty=self._import_index_empty(job["archive_root"])
        )

    @Slot(int, bool)
    def runImport(self, job_id: int, dry_run: bool) -> None:
        job = self.getImportJob(job_id)
        if not job:
            return
        mode = "dry_run" if dry_run else "import"
        suffix = " (dry run)" if dry_run else ""
        worker = ImportWorker(job, mode)
        # Negative queue key: repeat runs of the same import job serialize,
        # real device ids are >= 1 so rsync jobs are never blocked.
        self._runner.enqueue_worker(
            f"Import — {job['label']}{suffix}", -job_id, worker
        )

    @Slot(int)
    def runArchiveDupReport(self, job_id: int) -> None:
        job = self.getImportJob(job_id)
        if not job:
            return
        worker = ImportWorker(job, "dup_report")
        self._runner.enqueue_worker(
            f"Archive duplicates — {job['label']}", -job_id, worker
        )

    @Slot(int)
    def runArchiveDedupe(self, job_id: int) -> None:
        job = self.getImportJob(job_id)
        if not job:
            return
        worker = ImportWorker(job, "dedupe")
        self._runner.enqueue_worker(
            f"Archive clean-up — {job['label']}", -job_id, worker
        )

    @staticmethod
    def _import_index_empty(archive_root: str) -> bool:
        """True when the archive has no index rows yet (first-run warning).
        Short-lived main-thread connection; the worker owns its own."""
        if not archive_root:
            return False
        try:
            conn = hash_index.connect()
            try:
                return hash_index.count(
                    conn, os.path.realpath(archive_root)) == 0
            finally:
                conn.close()
        except Exception:
            return False

    # =====================================================================
    # sync execution — delegates to SyncRunner
    # =====================================================================

    @Slot("QVariantList")
    def runSync(self, jobs: list) -> None:
        """Hand jobs to the runner: `[{argv, label, destDeviceId, jobId}]`.
        The runner queues per destination device (one writer per disk) and
        runs different devices in parallel."""
        self._runner.enqueue(jobs)

    def _job_pre_start_error(self, job_id: int,
                             argv: list[str]) -> str | None:
        """Start-time guard for queued jobs (runner pre_start_check).

        The argv was resolved when the user confirmed the run; a drive can
        be unplugged — or come back at another mountpoint — while the job
        waits in its queue. rsync pointed at a stale mountpoint would read
        an empty folder (and --delete the other side against it) or write
        onto the laptop's own disk, so each drive side is re-verified.
        """
        job = self._job(job_id)
        if job is None:
            return "the job no longer exists"
        devices = self._devices_by_id()
        self._mounts.refresh()
        mounts = self._mounts.state()
        for dev_key, arg in (("from_device_id", argv[-2]),
                             ("to_device_id", argv[-1])):
            d = devices.get(job[dev_key])
            if d is None:
                return "a device of this job no longer exists"
            if d["kind"] != "drive":
                continue
            mp = mounts.get(d["uuid"])
            if not mp:
                return f"{d['label']} is not connected"
            if not os.path.ismount(mp):
                return f"{d['label']}: mountpoint is gone: {mp}"
            root = mp.rstrip("/") or "/"
            if not (arg.rstrip("/") == root
                    or arg.startswith(root.rstrip("/") + "/")):
                return (f"{d['label']} is now mounted at {mp!r}, not where "
                        f"the run was confirmed ({arg!r})")
        return None

    # =====================================================================
    # reads — single rows + pickers
    # =====================================================================

    @Slot(int, result="QVariantMap")
    def getDevice(self, device_id: int) -> dict:
        return self._devices_by_id().get(device_id, {})

    @Slot(int, result="QVariantMap")
    def getJob(self, job_id: int) -> dict:
        return self._job(job_id) or {}

    @Slot(str, int, result=int)
    def jobCountFor(self, kind: str, id_: int) -> int:
        """Jobs that go with a device when it's deleted."""
        if kind == "device":
            return sum(1 for j in self._db.list_jobs()
                       if id_ in (j["from_device_id"], j["to_device_id"]))
        return 0

    @Slot(result=list)
    def pickableDevices(self) -> list[dict]:
        kinds = {"laptop": "this computer", "drive": "drive",
                 "server": "server"}
        return [{**d, "display": f"{d['label']}  ({kinds[d['kind']]})"}
                for d in self._db.list_devices()]

    @Slot(int, result=str)
    def browseRoot(self, device_id: int) -> str:
        """Folder a "Browse…" button can open for this device: home for
        the laptop, the live mountpoint for a connected drive, "" for a
        server or an unplugged drive (nothing local to walk)."""
        d = self.getDevice(device_id)
        if d.get("kind") == "laptop":
            return os.path.expanduser("~")
        if d.get("kind") == "drive":
            return self._mounts.state().get(d.get("uuid"), "")
        return ""

    @Slot(result=list)
    def unassignedUuids(self) -> list[dict]:
        """Mounted UUIDs not yet defined as a drive: `[{uuid, mountpoint}]`.
        Drives the drive picker on the device form."""
        taken = {d["uuid"] for d in self._db.list_devices() if d["uuid"]}
        return [
            {"uuid": uuid, "mountpoint": mp}
            for uuid, mp in sorted(self._mounts.state().items(),
                                   key=lambda x: x[1].lower())
            if uuid not in taken
        ]

    @Slot(result="QVariantList")
    def optionCatalog(self) -> list:
        """The rsync option catalog for QML option lists: one entry per
        toggle {key, flag, description, default, baseline}."""
        return [dict(o) for o in OPTIONS]

    # =====================================================================
    # reads — draft preview / checks (job form + confirm popups)
    # =====================================================================

    def _resolve_draft(self, draft: dict):
        """(job_row, from_endpoint, to_endpoint) for a draft, or None while
        a device is still unpicked."""
        devices = self._devices_by_id()
        frm = devices.get(draft.get("from_device_id") or 0)
        to = devices.get(draft.get("to_device_id") or 0)
        if frm is None or to is None:
            return None
        row = dict(JOB_DEFAULTS)
        row.update({k: v for k, v in draft.items()
                    if k in JOB_COLS and v is not None})
        mounts, probes = self._mounts.state(), self._probes.state()
        return (row,
                self._endpoint(frm, row["from_path"], mounts, probes),
                self._endpoint(to, row["to_path"], mounts, probes))

    @Slot("QVariantMap", result=list)
    def previewCommand(self, draft: dict) -> list[str]:
        resolved = self._resolve_draft(draft)
        return build_rsync_argv(*resolved) if resolved else []

    @Slot("QVariantMap", result=list)
    def preflightDraft(self, draft: dict) -> list[dict]:
        resolved = self._resolve_draft(draft)
        return check_job(*resolved) if resolved else []

    @Slot(int, str, result="QVariantMap")
    def resolvedPath(self, device_id: int, path: str) -> dict:
        """Where a device + path really points, for display under a path
        field: {"path": str, "note": str}."""
        d = self.getDevice(device_id)
        if not d or (not path and d["kind"] != "drive"):
            return {"path": "", "note": ""}
        e = self._endpoint(d, path, self._mounts.state(),
                           self._probes.state())
        if d["kind"] == "drive" and not e["available"]:
            return {"path": "", "note": "drive is not connected"}
        if d["kind"] == "server":
            return {"path": e["path"], "note": ""}
        return {"path": e["path"],
                "note": "" if os.path.isdir(e["path"]) else "doesn't exist yet"}

    # =====================================================================
    # reads — sync scopes
    # =====================================================================

    @Slot(str, int, int, result=list)
    def jobsForScope(self, kind: str, scope_id: int, scope_id2: int) -> list:
        """Jobs a sync button covers, resolved for the confirm popups.

        kind: "job" (id) · "to" (to-device id) · "from" (from-device id)
        · "pair" (from-device id, to-device id) · "bundle" (id; every job
        inside it, at any depth).
        """
        if kind == "bundle":
            inside = self._subtree(scope_id, self._db.list_bundles())

        def picked(j):
            if kind == "job":
                return j["id"] == scope_id
            if kind == "to":
                return j["to_device_id"] == scope_id
            if kind == "from":
                return j["from_device_id"] == scope_id
            if kind == "pair":
                return (j["from_device_id"] == scope_id
                        and j["to_device_id"] == scope_id2)
            if kind == "bundle":
                return j["bundle_id"] in inside
            raise ValueError(f"unknown scope kind {kind!r}")

        ctx = self._ctx()
        out = []
        for j in sorted(filter(picked, self._db.list_jobs()),
                        key=lambda j: self._title(j, ctx).lower()):
            v = self._view(j, ctx)
            if v is None:
                continue
            row, frm, to = v["row"], v["from"], v["to"]
            out.append({
                "id": j["id"],
                "title": v["title"],
                "fromDisplay": frm["path"],
                "toDisplay": to["path"],
                "destDeviceId": j["to_device_id"],
                "ready": v["ready"],
                "command": build_rsync_argv(row, frm, to),
                "issues": check_job(row, frm, to),
            })
        return out

    # =====================================================================
    # reads — tree shapes (consumed by ConnectionsModel)
    # =====================================================================

    @Slot(result=list)
    def byDestination(self) -> list[dict]:
        """Destinations view: to-device → from-device → bundles → jobs,
        then Imports and Devices."""
        return self._tree("to_device_id", "from_device_id", "to")

    @Slot(result=list)
    def bySource(self) -> list[dict]:
        """Sources view: from-device → to-device → bundles → jobs."""
        return self._tree("from_device_id", "to_device_id", "from")

    def _tree(self, top: str, sub: str, scope: str) -> list[dict]:
        ctx = self._ctx()
        views = [v for j in self._db.list_jobs() if (v := self._view(j, ctx))]
        bundles = self._db.list_bundles()
        rows: list[dict] = []
        for t in self._ordered_devices({v["job"][top] for v in views}, ctx):
            tviews = [v for v in views if v["job"][top] == t["id"]]
            tkey = f"{scope}{t['id']}"
            ready = sum(1 for v in tviews if v["ready"])
            rows.append({
                **self._device_cols(t, ctx),
                "rowType": "top", "depth": 0,
                "groupKey": tkey, "_parents": [],
                "aggregate": f"{ready}/{len(tviews)} ready",
                "canSync": ready > 0,
                "scopeKind": scope, "scopeId": t["id"], "scopeId2": 0,
            })
            for s in self._ordered_devices(
                    {v["job"][sub] for v in tviews}, ctx):
                pviews = [v for v in tviews if v["job"][sub] == s["id"]]
                frm, to = ((s, t) if top == "to_device_id" else (t, s))
                skey = f"{tkey}-{s['id']}"
                rows.append({
                    **self._device_cols(s, ctx),
                    "rowType": "device", "depth": 1,
                    "groupKey": skey, "_parents": [tkey],
                    "canSync": any(v["ready"] for v in pviews),
                    "scopeKind": "pair", "scopeId": frm["id"],
                    "scopeId2": to["id"],
                    "fromDeviceId": frm["id"], "toDeviceId": to["id"],
                })
                pair = [b for b in bundles
                        if (b["from_device_id"], b["to_device_id"])
                        == (frm["id"], to["id"])]
                rows += self._bundle_rows(pviews, pair, None, 2, [tkey, skey])
        rows += self._import_rows() + self._device_list_rows(ctx)
        return rows

    def _bundle_rows(self, views: list, bundles: list, parent, depth: int,
                     parents: list) -> list[dict]:
        """One level of a device pair: its bundles (each followed by its
        own rows), then its jobs. A parent that isn't one of this pair's
        bundles counts as the top level, so nothing can go missing."""
        ids = {b["id"] for b in bundles}
        up = lambda p: p if p in ids else None
        rows = []
        for b in bundles:
            if up(b["parent_id"]) != parent:
                continue
            key = f"b{b['id']}"
            inside = self._subtree(b["id"], bundles)
            bviews = [v for v in views if v["job"]["bundle_id"] in inside]
            rows.append({
                "rowType": "bundle", "label": b["label"], "depth": depth,
                "groupKey": key, "_parents": parents, "selectKey": key,
                "aggregate": (f"{len(bviews)} job" if len(bviews) == 1
                              else f"{len(bviews)} jobs"),
                "canSync": any(v["ready"] for v in bviews),
                "scopeKind": "bundle", "scopeId": b["id"], "scopeId2": 0,
                "editKind": "bundle", "editId": b["id"],
                "fromDeviceId": b["from_device_id"],
                "toDeviceId": b["to_device_id"],
            })
            rows += self._bundle_rows(views, bundles, b["id"], depth + 1,
                                      parents + [key])
        for v in sorted((v for v in views
                         if up(v["job"]["bundle_id"]) == parent),
                        key=lambda v: v["name"].lower()):
            rows.append(self._job_row(v, v["name"], depth, parents))
        return rows

    @staticmethod
    def _subtree(bundle_id: int, bundles: list) -> set:
        """A bundle's id plus the ids of every bundle inside it."""
        out, todo = set(), [bundle_id]
        while todo:
            b = todo.pop()
            if b not in out:
                out.add(b)
                todo += [x["id"] for x in bundles if x["parent_id"] == b]
        return out

    # =====================================================================
    # internals
    # =====================================================================

    def _job(self, job_id: int) -> dict | None:
        return next((j for j in self._db.list_jobs() if j["id"] == job_id),
                    None)

    def _devices_by_id(self) -> dict[int, dict]:
        return {d["id"]: d for d in self._db.list_devices()}

    def _ctx(self) -> dict:
        return {"devices": self._devices_by_id(),
                "mounts": self._mounts.state(),
                "probes": self._probes.state()}

    @staticmethod
    def _endpoint(d: dict, path: str, mounts: dict, probes: dict) -> dict:
        """One side of a job, resolved: what rsync gets as the path, plus
        whether the device is there right now."""
        path = path or ""
        if d["kind"] == "laptop":
            return {"kind": "laptop", "device": d["label"],
                    "path": os.path.expanduser(path), "available": True,
                    "rsh": ""}
        if d["kind"] == "drive":
            mp = mounts.get(d["uuid"])
            sub = path.strip("/")
            base = (mp or "<not connected>").rstrip("/")
            return {"kind": "drive", "device": d["label"],
                    "path": f"{base}/{sub}" if sub else (mp or base),
                    "available": bool(mp), "rsh": ""}
        return {"kind": "server", "device": d["label"],
                "path": f"{d['host']}:{path or '/'}",
                "available": probes.get(d["id"]) == "live",
                "rsh": d.get("rsh") or ""}

    @staticmethod
    def _liveness(d: dict, mounts: dict, probes: dict) -> str:
        if d["kind"] == "laptop":
            return "live"
        if d["kind"] == "drive":
            return "live" if mounts.get(d["uuid"]) else "not_mounted"
        return probes.get(d["id"], "pending")

    def _title(self, j: dict, ctx: dict) -> str:
        d = ctx["devices"].get(j["from_device_id"])
        name = j["name"] or _leaf(j["from_path"])
        label = d["label"] if d else "?"
        return f"{label} > {name}" if name else label

    def _view(self, j: dict, ctx: dict) -> dict | None:
        """A job with both sides resolved — the single source for every
        row, popup and command."""
        frm = ctx["devices"].get(j["from_device_id"])
        to = ctx["devices"].get(j["to_device_id"])
        if frm is None or to is None:
            return None
        m, p = ctx["mounts"], ctx["probes"]
        e_from = self._endpoint(frm, j["from_path"], m, p)
        e_to = self._endpoint(to, j["to_path"], m, p)
        return {
            "job": j, "row": j, "from": e_from, "to": e_to,
            "title": self._title(j, ctx),
            "name": j["name"] or _leaf(j["from_path"]) or frm["label"],
            "ready": e_from["available"] and e_to["available"]
                     and not (frm["kind"] == to["kind"] == "server"),
        }

    def _ordered_devices(self, ids: set, ctx: dict) -> list[dict]:
        # list_devices order: the user's order, then laptop first, name.
        return [d for d in self._db.list_devices() if d["id"] in ids]

    def _device_cols(self, d: dict, ctx: dict) -> dict:
        m = ctx["mounts"]
        where = ("this computer" if d["kind"] == "laptop"
                 else (m.get(d["uuid"]) or "not connected")
                 if d["kind"] == "drive" else d["host"])
        return {"label": d["label"], "deviceId": d["id"],
                "liveness": self._liveness(d, m, ctx["probes"]),
                "where": where, "editKind": "device", "editId": d["id"]}

    def _job_row(self, v: dict, label: str, depth: int,
                 parents: list) -> dict:
        j = v["job"]
        return {
            "rowType": "job", "label": label, "depth": depth,
            "groupKey": "", "_parents": parents,
            "jobKey": f"j{j['id']}", "selectKey": f"j{j['id']}",
            "liveness": "live" if v["ready"] else "not_mounted",
            "fromDisplay": v["from"]["path"],
            "toDisplay": v["to"]["path"],
            "optsSummary": self._opts_summary(j),
            "canSync": v["ready"],
            "scopeKind": "job", "scopeId": j["id"], "scopeId2": 0,
            "editKind": "job", "editId": j["id"],
            "fromDeviceId": j["from_device_id"],
            "toDeviceId": j["to_device_id"],
        }

    def _device_list_rows(self, ctx: dict) -> list[dict]:
        """The Devices section: every device, so ones without jobs can
        still be edited or deleted. The laptop is built in."""
        rows = [{"rowType": "devicesHeader", "label": "Devices", "depth": 0,
                 "groupKey": "devices", "_parents": []}]
        for d in self._db.list_devices():
            rows.append({**self._device_cols(d, ctx),
                         "rowType": "deviceEntry", "depth": 1,
                         "_parents": ["devices"],
                         "builtIn": d["kind"] == "laptop"})
        return rows

    def _import_rows(self) -> list[dict]:
        """The Imports section. Absent while no import job is defined.
        `canSync` gates the run button on a quick preflight."""
        jobs = self._db.list_import_jobs()
        if not jobs:
            return []
        rows = [{"rowType": "importHeader", "label": "Imports", "depth": 0,
                 "groupKey": "imports", "_parents": []}]
        for j in jobs:
            errors = [i for i in check_import_job(j)
                      if i["severity"] == "error"]
            rows.append({
                "rowType": "importJob", "label": j["label"], "depth": 1,
                "_parents": ["imports"], "jobKey": f"i{j['id']}",
                "aggregate": "copy" if j.get("copy_mode") else "",
                "liveness": "live" if not errors else "not_mounted",
                "fromDisplay": j["dump_path"], "toDisplay": j["dest_path"],
                "optsSummary": ("copy (source untouched)"
                                if j.get("copy_mode")
                                else "move, duplicates deleted"),
                "canSync": not errors,
                "scopeKind": "import", "scopeId": j["id"], "scopeId2": 0,
                "editKind": "importJob", "editId": j["id"],
            })
        return rows

    @staticmethod
    def _opts_summary(j: dict) -> str:
        """Only what differs from the defaults, e.g. '--delete · dry run'."""
        parts = []
        for o in OPTIONS:
            on = bool(j.get(o["key"]))
            if on != o["default"]:
                parts.append(o["flag"] if on else "no " + o["flag"])
        if j.get("path_mode") == "folder":
            parts.append("copies the folder itself")
        mode = j.get("chown_mode")
        if mode == "dest":
            parts.append("owner like destination")
        elif mode == "custom":
            parts.append("owner " + " ".join(
                v for v in (j.get("chown_value"), j.get("chmod_value")) if v))
        n = len([x for x in (j.get("excludes") or "").splitlines()
                 if x.strip()])
        if n:
            parts.append(f"skips {n}")
        return "  ·  ".join(parts) or "defaults"
