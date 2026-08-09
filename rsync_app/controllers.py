"""Controller layer between QML and the data/runtime layer.

ConnectionsController is a thin `@Slot`-decorated facade so QML can drive
Database writes and pull pre-shaped read snapshots. The redux model has
one controller — connections are the only first-class concept.

QML can't pass kwarg-only Python calls, so writes use positional args.
For the binding form (15+ fields) we accept a QVariantMap (dict) on
add/update — keeps the call sites readable.

Liveness join lives here: the tree builders (`byDestination`,
`bySource`) merge MountWatcher + RemoteProbeWatcher state into the
row dicts they return. The model is a thin wrapper that just exposes
those rows to QML.
"""
import os.path

from PySide6.QtCore import QObject, Signal, Slot

from rsync_app import hash_index
from rsync_app.db import BINDING_COLS, BINDING_DEFAULTS, Database
from rsync_app.importer import ImportWorker
from rsync_app.mounts import MountWatcher
from rsync_app.preflight import check_binding, check_import_job
from rsync_app.probes import RemoteProbeWatcher
from rsync_app.rsync import OPTIONS, build_rsync_argv
from rsync_app.runner import SyncRunner


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
    # writes — source_labels
    # =====================================================================

    @Slot(str, str, result=int)
    def addSourceLabel(self, label: str, path: str) -> int:
        return self._db.add_source_label(label=label, path=path)

    @Slot(int, str, str)
    def updateSourceLabel(self, source_label_id: int, label: str,
                          path: str) -> None:
        self._db.update_source_label(source_label_id, label=label, path=path)

    @Slot(int)
    def deleteSourceLabel(self, source_label_id: int) -> None:
        self._db.delete_source_label(source_label_id)

    # =====================================================================
    # writes — dest_containers
    # =====================================================================

    @Slot(str, result=int)
    def addDestContainer(self, label: str) -> int:
        return self._db.add_dest_container(label=label)

    @Slot(int, str)
    def updateDestContainer(self, container_id: int, label: str) -> None:
        self._db.update_dest_container(container_id, label=label)

    @Slot(int)
    def deleteDestContainer(self, container_id: int) -> None:
        self._db.delete_dest_container(container_id)

    # =====================================================================
    # writes — dest_devices
    # =====================================================================

    @staticmethod
    def _device_fields(draft: dict) -> dict:
        """Normalize a device draft: kind decides which identity column
        is kept; the other is forced NULL (schema CHECK requires it)."""
        kind = draft.get("kind") or "local"
        return {
            "container_id": int(draft.get("container_id") or 0),
            "label": str(draft.get("label") or ""),
            "kind": kind,
            "uuid": (draft.get("uuid") or None) if kind == "local" else None,
            "network_target": (draft.get("network_target") or None)
                              if kind == "remote" else None,
            "rsh": (draft.get("rsh") or None) if kind == "remote" else None,
        }

    @Slot("QVariantMap", result=int)
    def addDestDevice(self, draft: dict) -> int:
        return self._db.add_dest_device(**self._device_fields(draft))

    @Slot(int, "QVariantMap")
    def updateDestDevice(self, device_id: int, draft: dict) -> None:
        self._db.update_dest_device(device_id, **self._device_fields(draft))

    @Slot(int)
    def deleteDestDevice(self, device_id: int) -> None:
        self._db.delete_dest_device(device_id)

    # =====================================================================
    # writes — bindings
    # =====================================================================

    @Slot("QVariantMap", result=int)
    def addBinding(self, draft: dict) -> int:
        return self._db.add_binding(draft)

    @Slot(int, "QVariantMap")
    def updateBinding(self, binding_id: int, draft: dict) -> None:
        self._db.update_binding(binding_id, draft)

    @Slot(int)
    def deleteBinding(self, binding_id: int) -> None:
        self._db.delete_binding(binding_id)

    # =====================================================================
    # writes — import_jobs
    # =====================================================================

    @staticmethod
    def _import_job_fields(draft: dict) -> dict:
        return {
            "label": str(draft.get("label") or "").strip(),
            "dump_path": str(draft.get("dump_path") or "").strip(),
            "dest_path": str(draft.get("dest_path") or "").strip(),
            "archive_root": str(draft.get("archive_root") or "").strip(),
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
        """Hand a batch of jobs to the runner.

        Each job is `{argv, label, destDeviceId}`. The runner groups by
        device and runs in parallel across devices.
        """
        self._runner.enqueue(jobs)

    def _job_pre_start_error(self, dest_device_id: int,
                             argv: list[str]) -> str | None:
        """Start-time guard for queued jobs (runner pre_start_check).

        The argv was resolved when the user confirmed the run; a local
        device can unmount — or remount at a different path — while the
        job waits in its queue. rsync pointed at a stale mountpoint would
        write into (or --delete against) a directory on the root
        filesystem, so re-verify against live mount state here.
        """
        d = next((row for row in self._db.list_dest_devices()
                  if row["id"] == dest_device_id), None)
        if d is None:
            return "destination device no longer exists"
        if d["kind"] != "local":
            # Remote failure modes are loud (ssh/rsync error out); there
            # is no wrong-path write hazard to guard against.
            return None
        self._mounts.refresh()
        mp = self._mounts.state().get(d["uuid"])
        if not mp:
            return "destination device is not mounted"
        if not os.path.ismount(mp):
            return f"destination mountpoint is gone: {mp}"
        dest = argv[-1] if argv else ""
        root = mp.rstrip("/") or "/"
        prefix = root if root.endswith("/") else root + "/"
        if dest != root and not dest.startswith(prefix):
            return (f"destination path {dest!r} is outside the current "
                    f"mountpoint {mp!r} — device remounted since the "
                    f"run was confirmed")
        return None

    # =====================================================================
    # reads — picker snapshots
    # =====================================================================

    @Slot(int, result="QVariantMap")
    def getSourceLabel(self, source_label_id: int) -> dict:
        return next((r for r in self._db.list_source_labels()
                     if r["id"] == source_label_id), {})

    @Slot(int, result="QVariantMap")
    def getDestContainer(self, container_id: int) -> dict:
        return next((r for r in self._db.list_dest_containers()
                     if r["id"] == container_id), {})

    @Slot(int, result="QVariantMap")
    def getDestDevice(self, device_id: int) -> dict:
        return next((r for r in self._db.list_dest_devices()
                     if r["id"] == device_id), {})

    @Slot(int, result="QVariantMap")
    def getBinding(self, binding_id: int) -> dict:
        return next((r for r in self._db.list_bindings()
                     if r["id"] == binding_id), {})

    @Slot(int, result=str)
    def deviceMountpoint(self, device_id: int) -> str:
        """Live mountpoint for a local, currently-mounted device, else "".

        Drives the "Browse…" button on the connection form's dest-subpath
        field: browsing only makes sense for a local disk that's mounted
        right now (remote targets can't be walked with a local file dialog,
        and an unmounted disk has no path to open).
        """
        d = self.getDestDevice(device_id)
        if not d or d.get("kind") != "local":
            return ""
        return self._mounts.state().get(d.get("uuid"), "")

    @Slot(result=list)
    def pickableSources(self) -> list[dict]:
        return [{**r, "display": self._source_display(r)}
                for r in self._db.list_source_labels()]

    @Slot(result=list)
    def pickableContainers(self) -> list[dict]:
        return self._db.list_dest_containers()

    @Slot(int, result=list)
    def pickableDevices(self, container_id: int) -> list[dict]:
        return self._db.list_dest_devices(container_id=container_id)

    @Slot(result=list)
    def unassignedUuids(self) -> list[dict]:
        """Mounted UUIDs not yet defined as a dest_device.

        Returns `[{uuid, mountpoint}, ...]`. Drives the inline UUID
        dropdown on the device form. UUIDs already defined on any
        dest_device are filtered out so the user can't accidentally
        register the same drive twice.
        """
        taken = {d["uuid"] for d in self._db.list_dest_devices()
                 if d["uuid"]}
        return [
            {"uuid": uuid, "mountpoint": mp}
            for uuid, mp in sorted(
                self._mounts.state().items(), key=lambda x: x[1].lower()
            )
            if uuid not in taken
        ]

    # =====================================================================
    # reads — tree shapes (consumed by ConnectionsModel)
    # =====================================================================

    @Slot(result=list)
    def byDestination(self) -> list[dict]:
        """Row list for the Group-by-Destination tree view.

        Sequence: container → device → connection (depth 0/1/2). Each
        device row carries live mount/probe state; the container row
        aggregates ("2/3 mounted"); connection rows are disabled if the
        underlying dest is unreachable.
        """
        rows: list[dict] = []
        containers = self._db.list_dest_containers()
        devices_by_container = self._group(
            self._db.list_dest_devices(), key="container_id"
        )
        bindings_by_device = self._group(
            self._db.list_bindings(), key="dest_device_id"
        )
        sources_by_id = {s["id"]: s for s in self._db.list_source_labels()}
        mounts = self._mounts.state()
        probe_state = self._probes.state()

        for c in containers:
            container_devices = devices_by_container.get(c["id"], [])
            rows.append(self._container_row(c, container_devices,
                                            mounts, probe_state,
                                            bindings_by_device))
            for d in container_devices:
                live = self._device_liveness(d, mounts, probe_state)
                rows.append(self._device_row(d, c["id"], live, mounts,
                                             bindings_by_device))
                device_bindings = sorted(
                    bindings_by_device.get(d["id"], []),
                    key=lambda b: sources_by_id.get(
                        b["source_label_id"], {"label": ""}
                    )["label"].lower(),
                )
                for b in device_bindings:
                    rows.append(self._connection_row(
                        b, c["id"], d, live, mounts, sources_by_id,
                        depth=2,
                    ))
        rows.extend(self._import_rows())
        return rows

    @Slot(result=list)
    def bySource(self) -> list[dict]:
        """Row list for the Group-by-Source tree view.

        Sequence: source_label → connection (depth 0/1). The
        destination grouping in the user's sketch is purely visual
        indentation — there's no synthetic container header row in
        this view; connection rows label themselves with the resolved
        dest target.
        """
        rows: list[dict] = []
        sources = self._db.list_source_labels()
        bindings_by_source = self._group(
            self._db.list_bindings(), key="source_label_id"
        )
        devices_by_id = {d["id"]: d for d in self._db.list_dest_devices()}
        containers_by_id = {c["id"]: c
                            for c in self._db.list_dest_containers()}
        mounts = self._mounts.state()
        probe_state = self._probes.state()

        for s in sources:
            source_bindings = bindings_by_source.get(s["id"], [])
            rows.append(self._source_row(s, source_bindings, devices_by_id,
                                         mounts, probe_state))
            def _sort_key(b, devices=devices_by_id,
                          containers=containers_by_id):
                dev = devices.get(b["dest_device_id"])
                if dev is None:
                    return ("", "")
                container = containers.get(dev["container_id"])
                return (
                    (container["label"].lower() if container else ""),
                    dev["label"].lower(),
                )
            for b in sorted(source_bindings, key=_sort_key):
                d = devices_by_id.get(b["dest_device_id"])
                if d is None:
                    continue  # FK should prevent, but be defensive
                live = self._device_liveness(d, mounts, probe_state)
                container_id = d["container_id"]
                rows.append(self._connection_row(
                    b, container_id, d, live, mounts,
                    sources_by_id={s["id"]: s},
                    depth=1,
                    container_label=containers_by_id[container_id]["label"]
                                    if container_id in containers_by_id
                                    else "",
                    # In source-grouped view the parent row is already
                    # the source label, so the connection's primary
                    # label points to the *destination* device instead
                    # — otherwise every child row would just repeat
                    # the source name.
                    label_override=d["label"],
                ))
        rows.extend(self._import_rows())
        return rows

    # =====================================================================
    # reads — scopes + preview
    # =====================================================================

    @Slot(str, int, result=list)
    def bindingsForScope(self, kind: str, scope_id: int) -> list[dict]:
        """Bindings under a given scope, for the sync confirmation dialog.

        `kind` ∈ "connection" | "device" | "container" | "source".
        Returns rows that include resolved-path info so the dialog can
        list each rsync command without re-resolving.
        """
        all_bindings = self._db.list_bindings()
        sources_by_id = {s["id"]: s for s in self._db.list_source_labels()}
        devices_by_id = {d["id"]: d for d in self._db.list_dest_devices()}
        mounts = self._mounts.state()

        if kind == "connection":
            picked = [b for b in all_bindings if b["id"] == scope_id]
        elif kind == "device":
            picked = [b for b in all_bindings
                      if b["dest_device_id"] == scope_id]
        elif kind == "container":
            device_ids = {d["id"] for d in self._db.list_dest_devices()
                          if d["container_id"] == scope_id}
            picked = [b for b in all_bindings
                      if b["dest_device_id"] in device_ids]
        elif kind == "source":
            picked = [b for b in all_bindings
                      if b["source_label_id"] == scope_id]
        else:
            raise ValueError(f"unknown scope kind {kind!r}")

        probe_state = self._probes.state()
        out: list[dict] = []
        for b in picked:
            s = sources_by_id.get(b["source_label_id"])
            d = devices_by_id.get(b["dest_device_id"])
            if s is None or d is None:
                continue
            reachable = self._device_liveness(d, mounts, probe_state) == "live"
            out.append({
                "id": b["id"],
                "sourceLabel": s["label"],
                "sourceDisplay": self._source_display(s),
                "sourcePath": s["path"],
                "destLabel": d["label"],
                "destFull": self._dest_full_label(d, b["dest_subpath"]),
                "destDeviceId": d["id"],
                "destDisplay": self._dest_display(d, b["dest_subpath"], mounts),
                "destReachable": reachable,
                "command": self._build_argv_for(b, s, d, mounts),
                "issues": check_binding(
                    b, {"path": s["path"]},
                    self._preflight_dest_ctx(d, b["dest_subpath"] or "",
                                             mounts, reachable),
                ),
            })
        return out

    @Slot(result="QVariantList")
    def optionCatalog(self) -> list:
        """The rsync option catalog for QML option lists.

        One entry per toggle: {key, flag, description, default, baseline}.
        Single-sourced from rsync_app.rsync.OPTIONS.
        """
        return [dict(o) for o in OPTIONS]

    def _resolve_draft(self, draft: dict):
        """Merge a partial draft over the catalog defaults and resolve its
        source/device rows. Returns (binding_row, source, device) or None
        while the draft is incomplete (form still being filled)."""
        sid = draft.get("source_label_id")
        did = draft.get("dest_device_id")
        if not sid or not did:
            return None
        s = next((row for row in self._db.list_source_labels()
                  if row["id"] == sid), None)
        d = next((row for row in self._db.list_dest_devices()
                  if row["id"] == did), None)
        if s is None or d is None:
            return None
        binding_row = dict(BINDING_DEFAULTS)
        binding_row.update({k: v for k, v in draft.items()
                            if k in BINDING_COLS and v is not None})
        return binding_row, s, d

    @Slot("QVariantMap", result=list)
    def previewCommand(self, draft: dict) -> list[str]:
        """Compute the rsync argv for a draft binding (used by form preview).

        `draft` should contain at least `source_label_id` and
        `dest_device_id`. Missing-piece previews return [] — the form
        decides whether to display.
        """
        resolved = self._resolve_draft(draft)
        if resolved is None:
            return []
        binding_row, s, d = resolved
        return self._build_argv_for(binding_row, s, d, self._mounts.state())

    @Slot("QVariantMap", result=list)
    def preflightDraft(self, draft: dict) -> list[dict]:
        """Run pre-flight checks against a draft binding.

        Same draft shape as `previewCommand`. Returns issues against the
        currently-resolved source path + dest live state. Re-called from
        the sync confirmation dialog whenever the user flips a toggle.
        """
        resolved = self._resolve_draft(draft)
        if resolved is None:
            return []
        binding_row, s, d = resolved
        mounts = self._mounts.state()
        reachable = self._device_liveness(
            d, mounts, self._probes.state()
        ) == "live"
        return check_binding(
            binding_row,
            {"path": s["path"]},
            self._preflight_dest_ctx(d, binding_row["dest_subpath"],
                                     mounts, reachable),
        )

    @Slot("QVariantMap", result="QVariantMap")
    def resolvedDestination(self, draft: dict) -> dict:
        """Live resolved destination path for a draft, for display under
        the subpath field: {"path": str, "note": str}.

        Needs only dest_device_id (+ dest_subpath); unlike previewCommand
        it renders before a source is picked. The note is plain-language
        ("will be created" / "drive is not connected"); remote paths get
        no note — the app never inspects the server.
        """
        did = draft.get("dest_device_id")
        if not did:
            return {"path": "", "note": ""}
        d = next((row for row in self._db.list_dest_devices()
                  if row["id"] == did), None)
        if d is None:
            return {"path": "", "note": ""}
        sub = (draft.get("dest_subpath") or "").strip("/")
        if d["kind"] == "local":
            mp = self._mounts.state().get(d["uuid"])
            if not mp:
                return {"path": "", "note": "drive is not connected"}
            path = f"{mp.rstrip('/')}/{sub}" if sub else mp
            note = "" if os.path.isdir(path) else "will be created"
            return {"path": path, "note": note}
        target = (d.get("network_target") or "").rstrip("/")
        return {"path": f"{target}/{sub}" if sub else target, "note": ""}

    # =====================================================================
    # internals — row construction
    # =====================================================================

    @staticmethod
    def _group(rows: list[dict], *, key: str) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r[key], []).append(r)
        return out

    def _container_row(self, c: dict, container_devices: list[dict],
                       mounts: dict, probe_state: dict,
                       bindings_by_device: dict) -> dict:
        live_count = sum(
            1 for d in container_devices
            if self._device_liveness(d, mounts, probe_state) == "live"
        )
        if container_devices:
            aggregate = f"{live_count}/{len(container_devices)} reachable"
        else:
            aggregate = "empty"
        can_sync = any(
            self._device_liveness(d, mounts, probe_state) == "live"
            and bindings_by_device.get(d["id"])
            for d in container_devices
        )
        return {
            "rowType": "container",
            "nodeId": c["id"],
            "label": c["label"],
            "depth": 0,
            "aggregate": aggregate,
            "liveness": "",
            "deviceKind": "",
            "mountpoint": "",
            "sourcePath": "",
            "destDisplay": "",
            "destSubpath": "",
            "canSync": bool(can_sync),
            "containerId": c["id"],
            "deviceId": -1,
            "sourceLabelId": -1,
            "bindingId": -1,
        }

    def _device_row(self, d: dict, container_id: int, liveness: str,
                    mounts: dict, bindings_by_device: dict) -> dict:
        mp = mounts.get(d["uuid"], "") if d["kind"] == "local" else ""
        can_sync = liveness == "live" and bool(bindings_by_device.get(d["id"]))
        return {
            "rowType": "device",
            "nodeId": d["id"],
            "label": d["label"],
            "depth": 1,
            "aggregate": "",
            "liveness": liveness,
            "deviceKind": d["kind"],
            "mountpoint": mp,
            "sourcePath": "",
            "destDisplay": (d["network_target"] or "") if d["kind"] == "remote"
                           else mp,
            "destSubpath": "",
            "canSync": bool(can_sync),
            "containerId": container_id,
            "deviceId": d["id"],
            "sourceLabelId": -1,
            "bindingId": -1,
        }

    def _connection_row(self, b: dict, container_id: int, d: dict,
                        liveness: str, mounts: dict,
                        sources_by_id: dict, depth: int,
                        container_label: str = "",
                        label_override: str | None = None) -> dict:
        s = sources_by_id.get(b["source_label_id"])
        source_label = self._source_display(s)
        source_path = s["path"] if s else ""
        return {
            "rowType": "connection",
            "nodeId": b["id"],
            "label": label_override if label_override is not None
                     else source_label,
            "depth": depth,
            "aggregate": "",
            "liveness": liveness,
            "deviceKind": d["kind"],
            "mountpoint": mounts.get(d["uuid"], "") if d["kind"] == "local"
                          else "",
            "sourcePath": source_path,
            "destDisplay": self._dest_display(d, b["dest_subpath"], mounts),
            "destSubpath": b["dest_subpath"],
            "canSync": liveness == "live",
            "containerId": container_id,
            "deviceId": d["id"],
            "sourceLabelId": b["source_label_id"],
            "bindingId": b["id"],
            "containerLabel": container_label,
        }

    def _import_rows(self) -> list[dict]:
        """The Imports section, appended to both tree views (it has no
        device/source grouping of its own). Absent while no import job is
        defined. `sourcePath`/`destDisplay` carry dump → destination for
        the row's secondary line; `canSync` gates the run button on the
        error-free-ness of a quick preflight."""
        jobs = self._db.list_import_jobs()
        if not jobs:
            return []
        rows: list[dict] = [{
            "rowType": "importHeader",
            "nodeId": -1,
            "label": "Imports",
            "depth": 0,
            "aggregate": "",
            "liveness": "",
            "deviceKind": "",
            "mountpoint": "",
            "sourcePath": "",
            "destDisplay": "",
            "destSubpath": "",
            "canSync": False,
            "containerId": -1,
            "deviceId": -1,
            "sourceLabelId": -1,
            "bindingId": -1,
        }]
        for j in jobs:
            errors = [i for i in check_import_job(j)
                      if i["severity"] == "error"]
            rows.append({
                "rowType": "importJob",
                "nodeId": j["id"],
                "label": j["label"],
                "depth": 1,
                "aggregate": "",
                "liveness": "live" if not errors else "not_mounted",
                "deviceKind": "",
                "mountpoint": "",
                "sourcePath": j["dump_path"],
                "destDisplay": j["dest_path"],
                "destSubpath": "",
                "canSync": not errors,
                "containerId": -1,
                "deviceId": -1,
                "sourceLabelId": -1,
                "bindingId": -1,
            })
        return rows

    def _source_row(self, s: dict, bindings: list[dict],
                    devices_by_id: dict, mounts: dict,
                    probe_state: dict) -> dict:
        reachable_count = sum(
            1 for b in bindings
            if (d := devices_by_id.get(b["dest_device_id"]))
            and self._device_liveness(d, mounts, probe_state) == "live"
        )
        aggregate = (f"{reachable_count}/{len(bindings)} reachable"
                     if bindings else "no connections")
        return {
            "rowType": "source",
            "nodeId": s["id"],
            "label": self._source_display(s),
            "depth": 0,
            "aggregate": aggregate,
            "liveness": "",
            "deviceKind": "",
            "mountpoint": "",
            "sourcePath": s["path"],
            "destDisplay": "",
            "destSubpath": "",
            "canSync": reachable_count > 0,
            "containerId": -1,
            "deviceId": -1,
            "sourceLabelId": s["id"],
            "bindingId": -1,
        }

    @staticmethod
    def _device_liveness(d: dict, mounts: dict, probe_state: dict) -> str:
        if d["kind"] == "local":
            return "live" if mounts.get(d["uuid"]) else "not_mounted"
        return probe_state.get(d["id"], "pending")

    @staticmethod
    def _source_display(s: dict | None) -> str:
        """Display name for a source: '<group label> > <folder name>'.

        `label` is a non-unique group name (e.g. "Laptop"); the folder name
        is the last segment of the stored path. Degrades to whichever part
        exists if one is empty.
        """
        if not s:
            return "<missing source>"
        label = (s.get("label") or "").strip()
        folder = os.path.basename((s.get("path") or "").rstrip("/"))
        if label and folder:
            return f"{label} > {folder}"
        return label or folder or "<source>"

    @staticmethod
    def _dest_full_label(d: dict, dest_subpath: str) -> str:
        """Human label for the destination: device > subpath.

        Trailing/leading slashes on the subpath are stripped; an empty
        subpath leaves just the device name.
        """
        sub = (dest_subpath or "").strip("/")
        return f"{d['label']} > {sub}" if sub else d["label"]

    @staticmethod
    def _dest_display(d: dict, dest_subpath: str, mounts: dict) -> str:
        sub = dest_subpath or ""
        if d["kind"] == "local":
            mp = mounts.get(d["uuid"])
            if not mp:
                return f"<unmounted>/{sub}" if sub else "<unmounted>"
            return f"{mp.rstrip('/')}/{sub}" if sub else mp
        target = (d["network_target"] or "").rstrip("/")
        return f"{target}/{sub}" if sub else target

    @staticmethod
    def _preflight_dest_ctx(d: dict, dest_subpath: str, mounts: dict,
                            reachable: bool) -> dict:
        if d["kind"] == "local":
            return {
                "kind": "local",
                "base": mounts.get(d["uuid"]) or "",
                "subpath": dest_subpath,
                "available": bool(mounts.get(d["uuid"])),
            }
        return {
            "kind": "remote",
            "base": d.get("network_target") or "",
            "subpath": dest_subpath,
            "available": reachable,
        }

    def _build_argv_for(self, binding_row: dict, source_label: dict,
                        dest_device: dict, mounts: dict) -> list[str]:
        source_ctx = {
            "kind": "local",
            "base": source_label["path"],
            "subpath": "",
        }
        if dest_device["kind"] == "local":
            mp = mounts.get(dest_device["uuid"]) or "<unmounted>"
            dest_ctx = {
                "kind": "local",
                "base": mp,
                "subpath": binding_row.get("dest_subpath", "") or "",
            }
        else:
            dest_ctx = {
                "kind": "remote",
                "base": dest_device["network_target"] or "",
                "subpath": binding_row.get("dest_subpath", "") or "",
                "rsh": dest_device.get("rsh") or "",
            }
        return build_rsync_argv(binding_row, source_ctx, dest_ctx)
