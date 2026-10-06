"""One-shot screenshot driver. Launches the app, waits for the window
to render, calls QQuickWindow.grabWindow(), saves to a path, exits.

Usage:
    python -m scripts.screenshot /tmp/out.png [--delay-ms 1500]
"""
import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path)
    ap.add_argument("--delay-ms", type=int, default=1500)
    ap.add_argument("--view-mode", choices=("group", "source"),
                    default="group")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("RsyncApp")
    QQuickStyle.setStyle("Fusion")

    db = Database()
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
    model.viewMode = args.view_mode
    engine.load(Path("rsync_app/qml/Main.qml").resolve())
    if not engine.rootObjects():
        print("ERROR: engine failed to load Main.qml", file=sys.stderr)
        return 1

    win = engine.rootObjects()[0]

    def grab_and_quit():
        img = win.grabWindow()
        ok = img.save(str(args.output))
        if not ok:
            print(f"ERROR: failed to save to {args.output}", file=sys.stderr)
            app.exit(2)
            return
        print(f"Saved {args.output}")
        app.exit(0)

    QTimer.singleShot(args.delay_ms, grab_and_quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
