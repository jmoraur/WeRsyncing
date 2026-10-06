import shlex
import socket
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from rsync_app.db import Database

POLL_INTERVAL_MS = 30000
TCP_TIMEOUT_S = 2.0


class RemoteProbeWatcher(QObject):
    """TCP-reachability state for remote devices.

    State per device id: 'live' (probe succeeded), 'unreachable' (probe
    failed), or 'pending' (no result yet). Probes run on a single
    background worker thread so an unreachable host doesn't freeze the
    GUI thread for `TCP_TIMEOUT_S` seconds per device.
    """

    changed = Signal()
    # list of (device_id, state) pairs — dict signals don't marshal
    # cleanly across the Qt thread boundary (`_pythonToCppCopy` error).
    _resultsReady = Signal(list)

    def __init__(self, db: Database, parent: QObject | None = None):
        super().__init__(parent)
        self._db = db
        self._state: dict[int, str] = {}
        self._executor = ThreadPoolExecutor(max_workers=1)
        # QueuedConnection: handler runs in the GUI thread regardless of
        # which thread emits.
        self._resultsReady.connect(self._apply_results, Qt.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh)
        db.db_changed.connect(self._on_db_changed)
        self._refresh()
        self._timer.start()

    def state(self) -> dict[int, str]:
        return dict(self._state)

    def stop(self) -> None:
        """Shut down the worker thread cleanly. Wire to app.aboutToQuit."""
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @Slot()
    def refresh(self) -> None:
        self._refresh()

    @Slot(str, str, result=str)
    def probeTarget(self, target: str, rsh: str = "") -> str:
        """One-shot probe against an arbitrary `user@host:/path` string.

        Used by the DeviceForm 'Test connection' button before the device
        is saved — `rsh` is the form's unsaved ssh-command field, so a
        `-p 2222` in it is honored. Synchronous on the calling thread;
        the user clicked.
        """
        host = self._host_from_target(target)
        if not host:
            return "unreachable"
        return self._tcp_probe(host, self._port_from_rsh(rsh))

    @Slot(int, result=str)
    def probeOne(self, device_id: int) -> str:
        """Run a one-shot probe for one device (used by 'Test connection').

        Runs synchronously on the calling thread; the user invoked the
        button so a 2s wait on failure is expected behaviour.
        """
        device = next(
            (d for d in self._db.list_devices()
             if d["id"] == device_id and d["kind"] == "server"),
            None,
        )
        if device is None:
            return ""
        result = self._probe_device(device)
        if self._state.get(device_id) != result:
            self._state[device_id] = result
            self.changed.emit()
        return result

    def _on_db_changed(self) -> None:
        # Drop stale entries, seed pending for new remotes, kick a refresh
        # so probe results follow the device list.
        remote_ids = {
            d["id"] for d in self._db.list_devices()
            if d["kind"] == "server"
        }
        stale = set(self._state) - remote_ids
        added = remote_ids - set(self._state)
        if stale or added:
            for sid in stale:
                self._state.pop(sid, None)
            for aid in added:
                self._state[aid] = "pending"
            self.changed.emit()
        self._refresh()

    def _refresh(self) -> None:
        # Snapshot device list on the GUI thread; pass to worker.
        remotes = [
            dict(d) for d in self._db.list_devices()
            if d["kind"] == "server"
        ]
        if not remotes:
            if self._state:
                self._state = {}
                self.changed.emit()
            return
        self._executor.submit(self._run_probes, remotes)

    def _run_probes(self, devices: list[dict]) -> None:
        # Runs in the worker thread. Don't touch self._state or db from
        # here — emit results via the queued signal.
        results = [(d["id"], self._probe_device(d)) for d in devices]
        self._resultsReady.emit(results)

    @Slot(list)
    def _apply_results(self, results: list) -> None:
        new_state = {int(k): v for k, v in results}
        if new_state != self._state:
            self._state = new_state
            self.changed.emit()

    @staticmethod
    def _probe_device(device: dict) -> str:
        host = RemoteProbeWatcher._host_from_target(
            device.get("host") or ""
        )
        if not host:
            return "unreachable"
        port = RemoteProbeWatcher._port_from_rsh(device.get("rsh") or "")
        return RemoteProbeWatcher._tcp_probe(host, port)

    @staticmethod
    def _port_from_rsh(rsh: str) -> int:
        """Pull the ssh port out of a device's rsh string, default 22.

        Understands `-p 2222` and `-p2222` anywhere in the string; a
        malformed string (unbalanced quotes, non-numeric port) falls back
        to 22 rather than failing the probe.
        """
        try:
            tokens = shlex.split(rsh or "")
        except ValueError:
            return 22
        for i, tok in enumerate(tokens):
            if tok == "-p" and i + 1 < len(tokens):
                candidate = tokens[i + 1]
            elif tok.startswith("-p") and len(tok) > 2:
                candidate = tok[2:]
            else:
                continue
            if candidate.isdigit():
                return int(candidate)
        return 22

    @staticmethod
    def _host_from_target(target: str) -> str:
        """Parse host from `user@host`, `host`, or `user@host:/path`."""
        head = (target or "").strip().partition(":")[0]
        return head.rpartition("@")[2]

    @staticmethod
    def _tcp_probe(host: str, port: int) -> str:
        try:
            with socket.create_connection((host, port), timeout=TCP_TIMEOUT_S):
                return "live"
        except (OSError, ValueError):
            return "unreachable"
