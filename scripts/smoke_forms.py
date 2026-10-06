"""Screenshot driver for the page, every form and every pop-up window.

Uses a throwaway database (never the real one) with a fake connected
drive and fake servers (one up, one down), opens each form / window
through the page's own routing functions, screenshots it to
/tmp/rsync_app_smoke/, and walks sync confirmations through their
popups. Runs are blocked by the runner's pre-start check, so nothing is
ever copied.

Run: PYTHONPATH=. python3 -sP scripts/smoke_forms.py
"""
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from rsync_app.controllers import ConnectionsController
from rsync_app.db import Database
from rsync_app.models import ConnectionsModel
from rsync_app.runner import SyncRunner
from rsync_app.theme import ThemeBridge

OUT = Path("/tmp/rsync_app_smoke")
DRIVE_UUID = "ffffffff-ffff-ffff-ffff-smoke0000001"


class FakeMounts(QObject):
    changed = Signal()

    def __init__(self, mounts):
        super().__init__()
        self._m = mounts

    def state(self):
        return dict(self._m)

    def refresh(self):
        pass


class FakeProbes(QObject):
    changed = Signal()

    def __init__(self, state):
        super().__init__()
        self._s = state

    def state(self):
        return dict(self._s)

    @Slot(str, str, result=str)
    def probeTarget(self, target, rsh=""):
        return "live"


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    laptop_dir = OUT / "laptop" / "Pictures"
    drive_dir = OUT / "drive"
    (laptop_dir / "2026").mkdir(parents=True)
    (drive_dir / "Pictures").mkdir(parents=True)

    app = QApplication(sys.argv)
    app.setApplicationName("RsyncAppSmoke")
    QQuickStyle.setStyle("Fusion")

    db = Database(OUT / "smoke.db")
    laptop = next(d["id"] for d in db.list_devices() if d["kind"] == "laptop")
    drive = db.add_device({"label": "X10 Pro", "kind": "drive",
                           "uuid": DRIVE_UUID})
    nas = db.add_device({"label": "NAS", "kind": "server",
                         "host": "root@192.168.1.7"})
    office = db.add_device({"label": "Office server", "kind": "server",
                            "host": "jm@office.example"})
    j_pic = db.add_job({"from_device_id": laptop,
                        "from_path": str(laptop_dir), "to_device_id": drive,
                        "to_path": "Pictures", "opt_delete": 1})
    db.add_job({"from_device_id": laptop,
                "from_path": str(laptop_dir), "to_device_id": drive,
                "to_path": "Pictures-copy", "path_mode": "folder"})
    db.add_job({"from_device_id": laptop,
                "from_path": str(laptop_dir), "to_device_id": nas,
                "to_path": "/mnt/user/pictures", "chown_mode": "custom",
                "chown_value": "nobody:users", "chmod_value": "D775,F664",
                "opt_delete": 1})
    j_pull = db.add_job({"name": "Appdata Backups",
                         "from_device_id": nas,
                         "from_path": "/mnt/user/backups_system/appdata",
                         "to_device_id": laptop,
                         "to_path": str(OUT / "laptop" / "Appdata Backups"),
                         "opt_delete": 1})
    db.add_job({"from_device_id": laptop,
                "from_path": str(laptop_dir), "to_device_id": office,
                "to_path": "/srv/pictures"})
    # Bundles: Documents (Family, Jan) and Videos > Plex (Movies).
    j_fam = db.add_job({"name": "Family", "from_device_id": laptop,
                        "from_path": str(laptop_dir), "to_device_id": nas,
                        "to_path": "/mnt/user/docs/Family", "opt_delete": 1})
    j_jan = db.add_job({"name": "Jan", "from_device_id": laptop,
                        "from_path": str(laptop_dir), "to_device_id": nas,
                        "to_path": "/mnt/user/docs/Jan", "opt_delete": 1})
    j_mov = db.add_job({"name": "Movies", "from_device_id": laptop,
                        "from_path": str(laptop_dir), "to_device_id": nas,
                        "to_path": "/mnt/user/media/Plex/Movies"})
    docs = db.add_bundle(label="Documents", from_device_id=laptop,
                         to_device_id=nas, job_ids=[j_fam, j_jan])
    plex = db.add_bundle(label="Plex", from_device_id=laptop,
                         to_device_id=nas, job_ids=[j_mov])
    db.add_bundle(label="Videos", from_device_id=laptop, to_device_id=nas,
                  bundle_ids=[plex])
    imp = OUT / "import"
    (imp / "dump").mkdir(parents=True)
    (imp / "archive" / "2026").mkdir(parents=True)
    iid = db.add_import_job(label="Phone photos", dump_path=str(imp / "dump"),
                            dest_path=str(imp / "archive" / "2026"),
                            archive_root=str(imp / "archive"))

    mounts = FakeMounts({DRIVE_UUID: str(drive_dir)})
    probes = FakeProbes({nas: "live", office: "unreachable"})
    runner = SyncRunner()
    controller = ConnectionsController(db, mounts, probes, runner)
    runner.set_pre_start_check(lambda ref, argv: "smoke run — not started")
    model = ConnectionsModel(controller, db, mounts, probes)

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("connections", controller)
    ctx.setContextProperty("connectionsModel", model)
    ctx.setContextProperty("remoteProbes", probes)
    ctx.setContextProperty("syncRunner", runner)
    theme = ThemeBridge()
    theme.setMode("dark")
    ctx.setContextProperty("themeBridge", theme)
    engine.load(Path("rsync_app/qml/Main.qml").resolve())
    if not engine.rootObjects():
        print("ERROR: engine failed", file=sys.stderr)
        return 1
    win = engine.rootObjects()[0]
    win.setProperty("width", 1200)
    win.setProperty("height", 760)
    rc = {"code": 0}
    current = {"obj": None}   # what the next screenshot shows

    def find(name: str) -> QObject:
        obj = win.findChild(QObject, name)
        if obj is None:
            print(f"ERROR: {name!r} not found", file=sys.stderr)
            rc["code"] = 3
        return obj

    def js(name: str, code: str, shows: str | None = None):
        """Run `code` with `o` = the object called `name`; the screenshot
        then shows the object called `shows` (default: the main window)."""
        fn = engine.evaluate(f"(function(o) {{ {code} }})")
        res = fn.call([engine.newQObject(find(name))])
        if res.isError():
            print(f"ERROR in {code!r}: {res.toString()}", file=sys.stderr)
            rc["code"] = 5
        current["obj"] = find(shows) if shows else None

    def close(name: str):
        obj = find(name)
        obj.metaObject().invokeMethod(obj, "close")
        current["obj"] = None

    def grab(label: str) -> None:
        obj = current["obj"]
        target = obj if isinstance(obj, QQuickWindow) else win
        path = OUT / f"{label}.png"
        if target.grabWindow().save(str(path)):
            print(f"saved {path}")
        else:
            rc["code"] = 2
            print(f"FAIL save {path}", file=sys.stderr)

    steps: list = []

    def add(label, action):
        steps.append((label, action))

    add("01_page_destinations", lambda: None)
    add("01b_page_jobs_expanded", lambda: js(
        "page", f"o.toggleExpanded('j{j_pic}'); o.toggleExpanded('j{j_pull}')"))
    add("01c_page_sources", lambda: model.setProperty("viewMode", "source"))
    add("__", lambda: model.setProperty("viewMode", "group"))
    # Selection + right-click menu: a bundleable pair, then a refused mix.
    add("01d_selected", lambda: js(
        "page", f"o.toggleExpanded('j{j_pic}'); o.toggleExpanded('j{j_pull}');"
        f" o.select('j{j_fam}', 0); o.select('j{j_jan}', Qt.ControlModifier)"))
    add("01e_menu", lambda: js("page", f"o.openMenu('j{j_jan}')"))
    add("__", lambda: js("rowMenu", "o.close()"))
    add("01f_menu_refused", lambda: js(
        "page", f"o.select('j{j_pic}', Qt.ControlModifier);"
        f" o.openMenu('j{j_pic}')"))
    add("__", lambda: js("rowMenu", "o.close()"))
    add("__", lambda: js("page", "o._selection = []"))
    add("01g_menu_bundle", lambda: js("page", f"o.openMenu('b{plex}')"))
    add("__", lambda: js("rowMenu", "o.close()"))

    for label, code, form in (
            ("02_job_form_new", f"o.newJob({laptop}, {drive})", "jobForm"),
            ("03_job_form_edit_pull", f"o.editEntity('job', {j_pull})",
             "jobForm"),
            ("04_bundle_form_new", f"o.newBundle(['j{j_fam}'])",
             "bundleForm"),
            ("04b_bundle_form_rename", f"o.editEntity('bundle', {docs})",
             "bundleForm"),
            ("05_device_form_drive", f"o.editEntity('device', {drive})",
             "deviceForm"),
            ("06_device_form_server", f"o.editEntity('device', {nas})",
             "deviceForm")):
        add(label, lambda code=code: js("page", code))
        add("__", lambda form=form: close(form))

    add("07_delete_confirm_device", lambda: js(
        "page", f"o.deleteEntity('device', {office}, 'Office server')",
        "deleteConfirm"))
    add("__", lambda: close("deleteConfirm"))

    # One job: job popup → options open → its --delete warning → Yes runs it.
    add("08_sync_job_popup", lambda: js(
        "page", f"o.runSync('job', {j_pic}, 0)", "syncConfirm"))
    add("08b_sync_job_options", lambda: js(
        "syncConfirm", "o._optionsOpen = true", "syncConfirm"))
    add("08c_sync_warning_popup", lambda: js(
        "syncConfirm", "o._optionsOpen = false; o.yes()", "syncConfirm"))
    add("__", lambda: js("syncConfirm", "o.yes()"))
    # A bundle: its jobs at any depth.
    add("09_sync_bundle_popup", lambda: js(
        "page", f"o.runSync('bundle', {docs}, 0)", "syncConfirm"))
    add("__", lambda: close("syncConfirm"))
    # A whole source device: the first popup lists the job that can't
    # run; Yes to all on the job popup, then a warning popup still comes.
    add("09a_sync_source_popup", lambda: js(
        "page", f"o.runSync('from', {laptop}, 0)", "syncConfirm"))
    add("09b_sync_source_warning", lambda: js(
        "syncConfirm", "o.yesToAll()", "syncConfirm"))
    add("__", lambda: js("syncConfirm", "o.yesToAll()"))
    add("10_activity", lambda: None)

    add("11_import_confirm", lambda: js(
        "page", f"o.runSync('import', {iid}, 0)", "importConfirm"))
    add("__", lambda: close("importConfirm"))

    add("12_page_light", lambda: theme.setMode("light"))
    add("12b_sync_popup_light", lambda: js(
        "page", f"o.runSync('job', {j_pull}, 0)", "syncConfirm"))
    add("__", lambda: close("syncConfirm"))

    idx = {"i": 0}

    def tick():
        if idx["i"] >= len(steps):
            print("runs queued:", [(j["label"], j["state"])
                                   for j in runner.jobs()])
            app.exit(rc["code"])
            return
        label, action = steps[idx["i"]]
        idx["i"] += 1
        action()

        def after():
            if not label.startswith("__"):
                grab(label)
            tick()
        QTimer.singleShot(450, after)

    QTimer.singleShot(800, tick)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
