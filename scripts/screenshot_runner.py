"""Screenshot the OutputPanel populated with rsync jobs.

Boots the full UI, enqueues three jobs (one slow / one failing /
one quick), then takes a series of screenshots demonstrating the
M6 Step 4 panel features: filter chips, per-job collapse, copy
button, autoscroll-follow-tail hint.

Usage:
    python -m scripts.screenshot_runner /tmp/out_dir/
"""
import argparse
import shutil
import sys
import tempfile
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


def _find_output_panel(win) -> QObject:
    panel = win.findChild(QObject, "outputPanel")
    if panel is None:
        raise RuntimeError("OutputPanel not found in QML tree")
    return panel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir", type=Path)
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName("RsyncAppSmoke")
    QQuickStyle.setStyle("Fusion")

    # Throwaway database — never the real one.
    db = Database(Path(tempfile.mkdtemp()) / "rsync.db")
    watcher = MountWatcher()
    probes = RemoteProbeWatcher(db)
    runner = SyncRunner()
    controller = ConnectionsController(db, watcher, probes, runner)
    model = ConnectionsModel(controller, db, watcher, probes)
    # The controller's start-time mount guard would fail these demo jobs
    # (their /tmp dests aren't under any real device's mountpoint); this
    # script demos the output panel, not the guard, so switch it off.
    runner.set_pre_start_check(None)

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
        print("ERROR: engine failed to load Main.qml", file=sys.stderr)
        return 1

    win = engine.rootObjects()[0]
    win.resize(1000, 700)

    tmp = Path(tempfile.mkdtemp(prefix="rsync-shot-"))
    src_ok, dst_ok = tmp / "src_ok", tmp / "dst_ok"
    src_slow, dst_slow = tmp / "src_slow", tmp / "dst_slow"
    for d in (src_ok, dst_ok, src_slow, dst_slow):
        d.mkdir(parents=True)
    (src_ok / "hello.txt").write_text("hi\n")
    # Larger file + bwlimit so this job stays "running" for the screenshots.
    (src_slow / "big.bin").write_bytes(b"x" * (5 * 1024 * 1024))

    def enqueue_jobs():
        runner.enqueue([
            # Done quickly.
            {
                "argv": ["rsync", "--archive", "--verbose", "--dry-run",
                         str(src_ok) + "/", str(dst_ok) + "/"],
                "label": "Photos → BlackHDD",
                "destDeviceId": 1,
            },
            # Failed quickly (source missing).
            {
                "argv": ["rsync", "--archive", "--verbose",
                         "/nonexistent/path/", str(dst_ok) + "/"],
                "label": "Music → RedHDD",
                "destDeviceId": 2,
            },
            # Still running when the grabs happen (5 MiB at 50 KiB/s ≈ 100s).
            {
                "argv": ["rsync", "--archive", "--verbose",
                         "--progress", "--bwlimit=50",
                         str(src_slow) + "/", str(dst_slow) + "/"],
                "label": "Backup → BlueHDD",
                "destDeviceId": 3,
            },
        ])

    def grab(name):
        img = win.grabWindow()
        path = args.output_dir / name
        if not img.save(str(path)):
            print(f"ERROR: failed to save {path}", file=sys.stderr)
            return
        print(f"Saved {path}")

    def run_after_settle():
        panel = _find_output_panel(win)

        # 1. Default view: all jobs shown. Copy button + "following tail"
        # hint are static UI elements visible here.
        grab("01_default_all.png")

        # 2. Filter: Running.
        panel.setProperty("_filter", "running")
        QTimer.singleShot(200, lambda: grab("02_filter_running.png"))

        # 3. Filter: Failed.
        QTimer.singleShot(450, lambda: panel.setProperty("_filter", "failed"))
        QTimer.singleShot(650, lambda: grab("03_filter_failed.png"))

        # 4. Filter: Done.
        QTimer.singleShot(900, lambda: panel.setProperty("_filter", "done"))
        QTimer.singleShot(1100, lambda: grab("04_filter_done.png"))

        # Reset filter so the persisted Settings value isn't stuck.
        QTimer.singleShot(1350, lambda: panel.setProperty("_filter", "all"))
        QTimer.singleShot(1500, lambda: app.exit(0))

    def maybe_start():
        # Wait until at least one job is running + one terminal (done/failed).
        snap = runner.jobs()
        states = [j["state"] for j in snap]
        if (states.count("running") >= 1
                and (states.count("done") + states.count("failed")) >= 2):
            QTimer.singleShot(400, run_after_settle)
            runner.jobStateChanged.disconnect(maybe_start_wrapper)

    def maybe_start_wrapper(*_):
        maybe_start()

    runner.jobStateChanged.connect(maybe_start_wrapper)
    QTimer.singleShot(300, enqueue_jobs)
    QTimer.singleShot(30_000, lambda: (print("TIMEOUT"), app.exit(2)))

    rc = app.exec()
    # Cancel anything still running before the runner / tmp dir are torn down.
    runner.cancelAll()
    runner.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
