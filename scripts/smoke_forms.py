"""Smoke driver for Step 4 forms.

Seeds a few DB rows so the tree renders non-empty, launches the app,
opens each dialog via findChild(objectName), screenshots each, then
exits. Cleans up the seeded rows on exit so re-runs are idempotent.
"""
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from rsync_app.controllers import ConnectionsController
from rsync_app.db import Database
from rsync_app.models import ConnectionsModel
from rsync_app.mounts import MountWatcher
from rsync_app.probes import RemoteProbeWatcher
from rsync_app.runner import SyncRunner
from rsync_app.theme import ThemeBridge

OUT = Path("/tmp/rsync_app_smoke")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName("RsyncApp")
    QQuickStyle.setStyle("Fusion")

    db = Database()
    # A fixture path of our own — a real user folder (e.g. ~/Pictures) may
    # already be registered as a source, and source paths are UNIQUE.
    smoke_src_dir = OUT / "source-fixture"
    smoke_src_dir.mkdir(parents=True, exist_ok=True)
    smoke_src = str(smoke_src_dir)
    sid = db.add_source_label(label="Photos-SMOKE", path=smoke_src)
    cid = db.add_dest_container(label="HDD-case-SMOKE")
    did = db.add_dest_device(container_id=cid, label="Backup-SMOKE",
                             kind="local",
                             uuid="ffffffff-ffff-ffff-ffff-smoke0000001")
    did_remote = db.add_dest_device(container_id=cid, label="unraid-SMOKE",
                                    kind="remote",
                                    network_target="user@nas:/mnt/backup",
                                    rsh="ssh -p 2222")
    bid = db.add_binding({"source_label_id": sid, "dest_device_id": did,
                          "dest_subpath": "photos",
                          "opt_compress": True, "opt_dry_run": True})
    # Second binding on the same device so the device-scope sync
    # confirmation has more than one row to show.
    bid2 = db.add_binding({"source_label_id": sid, "dest_device_id": did,
                           "dest_subpath": "photos-archive",
                           "opt_delete": True})
    # Custom-ownership binding on the remote device: exercises the
    # OwnershipEditor parse-back and the device-level --rsh in the preview.
    bid3 = db.add_binding({"source_label_id": sid,
                           "dest_device_id": did_remote,
                           "dest_subpath": "media",
                           "chown_mode": "custom",
                           "chown_value": "nobody:users",
                           "chmod_value": "D755,F644"})
    # Import-job fixture: real folders so the confirm dialog preflights
    # clean (bar the archive-not-scanned warning, which we want visible).
    imp_root = OUT / "import-fixture"
    imp_dump = imp_root / "dump"
    imp_archive = imp_root / "archive"
    imp_dest = imp_archive / "2026" / "2026.08 - Smoke Event"
    imp_dump.mkdir(parents=True, exist_ok=True)
    imp_dest.mkdir(parents=True, exist_ok=True)
    iid = db.add_import_job(label="Phone-photos-SMOKE",
                            dump_path=str(imp_dump),
                            dest_path=str(imp_dest),
                            archive_root=str(imp_archive))

    watcher = MountWatcher()
    probes = RemoteProbeWatcher(db)
    runner = SyncRunner()
    controller = ConnectionsController(db, watcher, probes, runner)
    model = ConnectionsModel(controller, db, watcher, probes)

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("connections", controller)
    ctx.setContextProperty("connectionsModel", model)
    ctx.setContextProperty("remoteProbes", probes)
    ctx.setContextProperty("syncRunner", runner)
    theme = ThemeBridge()
    ctx.setContextProperty("themeBridge", theme)
    engine.load(Path("rsync_app/qml/Main.qml").resolve())
    if not engine.rootObjects():
        print("ERROR: engine failed", file=sys.stderr)
        return 1

    win = engine.rootObjects()[0]
    rc = {"code": 0}

    def grab(name: str) -> None:
        path = OUT / f"{name}.png"
        ok = win.grabWindow().save(str(path))
        if not ok:
            rc["code"] = 2
            print(f"FAIL save {path}", file=sys.stderr)
        else:
            print(f"saved {path}")

    def find(name: str) -> QObject | None:
        return win.findChild(QObject, name)

    def open_dialog(name: str, props: dict | None = None) -> QObject | None:
        dlg = find(name)
        if dlg is None:
            print(f"ERROR: dialog {name!r} not found", file=sys.stderr)
            rc["code"] = 3
            return None
        if props:
            for k, v in props.items():
                dlg.setProperty(k, v)
        # Dialog has slot open()
        ok = dlg.metaObject().invokeMethod(dlg, "open")
        if not ok:
            print(f"ERROR: open() invoke failed on {name}", file=sys.stderr)
            rc["code"] = 4
        return dlg

    def close_dialog(dlg: QObject) -> None:
        if dlg is None:
            return
        dlg.metaObject().invokeMethod(dlg, "close")

    # Sequence of screenshot steps. Each waits long enough for the
    # dialog's open animation to settle before grabbing.
    steps: list = []

    def add(label: str, action):
        steps.append((label, action))

    # 1. Page
    add("01_page", lambda: None)

    # 2. ConnectionForm (new) — exercises every field + preview binding.
    cf_state = {"dlg": None}
    def open_cf_new():
        cf_state["dlg"] = open_dialog("connectionForm",
                                      {"bindingId": -1,
                                       "initialSourceId": -1,
                                       "initialDeviceId": -1})
    add("02_connection_form_new", open_cf_new)
    add("__close", lambda: close_dialog(cf_state["dlg"]))

    # 3. ConnectionForm (edit)
    cf_edit = {"dlg": None}
    def open_cf_edit():
        cf_edit["dlg"] = open_dialog("connectionForm", {"bindingId": bid})
    add("03_connection_form_edit", open_cf_edit)
    add("__close", lambda: close_dialog(cf_edit["dlg"]))

    # 3b. ConnectionForm (edit, custom ownership on the remote device)
    cf_custom = {"dlg": None}
    def open_cf_custom():
        cf_custom["dlg"] = open_dialog("connectionForm", {"bindingId": bid3})
    add("03b_connection_form_edit_custom", open_cf_custom)
    add("__close", lambda: close_dialog(cf_custom["dlg"]))

    # 4. SourceLabelForm (edit)
    sf_state = {"dlg": None}
    def open_sf():
        sf_state["dlg"] = open_dialog("sourceLabelForm",
                                      {"sourceLabelId": sid,
                                       "initialLabel": "Photos-SMOKE",
                                       "initialPath": smoke_src})
    add("04_source_label_form", open_sf)
    add("__close", lambda: close_dialog(sf_state["dlg"]))

    # 5. ContainerForm (new)
    contf_state = {"dlg": None}
    def open_contf():
        contf_state["dlg"] = open_dialog("containerForm",
                                         {"containerId": -1,
                                          "initialLabel": ""})
    add("05_container_form", open_contf)
    add("__close", lambda: close_dialog(contf_state["dlg"]))

    # 6. DeviceForm (edit local)
    df_state = {"dlg": None}
    def open_df_local():
        df_state["dlg"] = open_dialog("deviceForm",
                                      {"deviceId": did,
                                       "initialContainerId": cid,
                                       "initialKind": "local",
                                       "initialLabel": "Backup-SMOKE",
                                       "initialUuid": "ffffffff-ffff-ffff-ffff-smoke0000001",
                                       "initialNetworkTarget": ""})
    add("06_device_form_local", open_df_local)
    add("__close", lambda: close_dialog(df_state["dlg"]))

    # 7. DeviceForm (edit remote)
    dfr_state = {"dlg": None}
    def open_df_remote():
        dfr_state["dlg"] = open_dialog("deviceForm",
                                       {"deviceId": did_remote,
                                        "initialContainerId": cid,
                                        "initialKind": "remote",
                                        "initialLabel": "unraid-SMOKE",
                                        "initialUuid": "",
                                        "initialNetworkTarget": "user@nas:/mnt/backup"})
    add("07_device_form_remote", open_df_remote)
    add("__close", lambda: close_dialog(dfr_state["dlg"]))

    # 8. DeleteConfirmDialog (container — cascade warning)
    dc_state = {"dlg": None}
    def open_dc():
        dc_state["dlg"] = open_dialog("deleteConfirm",
                                      {"rowType": "container",
                                       "nodeId": cid,
                                       "label": "HDD-case-SMOKE"})
    add("08_delete_confirm", open_dc)
    add("__close", lambda: close_dialog(dc_state["dlg"]))

    # 9. SyncConfirmDialog (single connection — editable, live preview).
    scs_state = {"dlg": None}
    def open_scs():
        scs_state["dlg"] = open_dialog("syncConfirmDialog",
                                       {"scopeKind": "connection",
                                        "scopeId": bid})
    add("09_sync_confirm_single", open_scs)
    add("__close", lambda: close_dialog(scs_state["dlg"]))

    # 10. SyncConfirmDialog (device scope — multi-binding summary).
    scm_state = {"dlg": None}
    def open_scm():
        scm_state["dlg"] = open_dialog("syncConfirmDialog",
                                       {"scopeKind": "device",
                                        "scopeId": did})
    add("10_sync_confirm_multi", open_scm)
    add("__close", lambda: close_dialog(scm_state["dlg"]))

    # 11. ImportJobForm (edit — clean paths, live preflight issue list).
    ijf_state = {"dlg": None}
    def open_ijf():
        ijf_state["dlg"] = open_dialog("importJobForm",
                                       {"importJobId": iid,
                                        "initialLabel": "Phone-photos-SMOKE",
                                        "initialDump": str(imp_dump),
                                        "initialDest": str(imp_dest),
                                        "initialArchive": str(imp_archive)})
    add("11_import_job_form", open_ijf)
    add("__close", lambda: close_dialog(ijf_state["dlg"]))

    # 11b. ImportJobForm (new — empty fields, all three errors render).
    ijf_err = {"dlg": None}
    def open_ijf_err():
        ijf_err["dlg"] = open_dialog("importJobForm",
                                     {"importJobId": -1,
                                      "initialLabel": "",
                                      "initialDump": str(imp_dump) + "-missing",
                                      "initialDest": "",
                                      "initialArchive": ""})
    add("11b_import_job_form_errors", open_ijf_err)
    add("__close", lambda: close_dialog(ijf_err["dlg"]))

    # 11c. ImportJobForm in copy mode (source/destination labels switch,
    # copy-only helper text).
    ijf_copy = {"dlg": None}
    def open_ijf_copy():
        ijf_copy["dlg"] = open_dialog("importJobForm",
                                      {"importJobId": iid,
                                       "initialLabel": "Phone-photos-SMOKE",
                                       "initialDump": str(imp_dump),
                                       "initialDest": str(imp_dest),
                                       "initialArchive": str(imp_archive),
                                       "initialCopyMode": 1})
    add("11c_import_job_form_copy", open_ijf_copy)
    add("__close", lambda: close_dialog(ijf_copy["dlg"]))

    # 12. ImportConfirmDialog (loads the job via jobId on open).
    icd_state = {"dlg": None}
    def open_icd():
        icd_state["dlg"] = open_dialog("importConfirmDialog", {"jobId": iid})
    add("12_import_confirm", open_icd)
    add("__close", lambda: close_dialog(icd_state["dlg"]))

    # 12b. ImportConfirmDialog for a copy-mode job (no-deletion prose).
    icd_copy = {"dlg": None}
    def open_icd_copy():
        db.update_import_job(iid, copy_mode=1)
        icd_copy["dlg"] = open_dialog("importConfirmDialog", {"jobId": iid})
    add("12b_import_confirm_copy", open_icd_copy)
    add("__close", lambda: (close_dialog(icd_copy["dlg"]),
                            db.update_import_job(iid, copy_mode=0)))

    DELAY_MS = 400
    idx = {"i": 0}

    def cleanup_and_exit():
        try:
            db.delete_binding(bid)
        except Exception:
            pass
        try:
            db.delete_binding(bid2)
        except Exception:
            pass
        try:
            db.delete_binding(bid3)
        except Exception:
            pass
        for d in db.list_dest_devices():
            if d["label"].endswith("-SMOKE"):
                db.delete_dest_device(d["id"])
        try:
            db.delete_dest_container(cid)
        except Exception:
            pass
        try:
            db.delete_source_label(sid)
        except Exception:
            pass
        for j in db.list_import_jobs():
            if j["label"].endswith("-SMOKE"):
                db.delete_import_job(j["id"])
        app.exit(rc["code"])

    def tick():
        if idx["i"] >= len(steps):
            cleanup_and_exit()
            return
        label, action = steps[idx["i"]]
        idx["i"] += 1
        action()
        # Give the dialog a beat to render before snapping.
        def after():
            if not label.startswith("__"):
                grab(label)
            tick()
        QTimer.singleShot(DELAY_MS, after)

    QTimer.singleShot(800, tick)
    app.aboutToQuit.connect(probes.stop)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
