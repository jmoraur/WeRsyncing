"""QAbstractListModel exposing the connections tree to QML.

Thin wrapper: the controller does the data shaping (DB + mounts +
probes join → row dicts), the model just turns that into a Qt model
with role names. `viewMode` switches between the dest-grouped and
source-grouped row sequences.
"""
from PySide6.QtCore import (
    Property, QAbstractListModel, QModelIndex, Qt, Signal, Slot,
)

from rsync_app.controllers import ConnectionsController
from rsync_app.db import Database
from rsync_app.mounts import MountWatcher
from rsync_app.probes import RemoteProbeWatcher


# Role schema. Order is irrelevant; numbers must be distinct
# Qt.UserRole offsets. Roles populated per row type — fields not
# relevant to a given row type return "" / -1 / False.
_ROLE_KEYS = (
    "rowType", "nodeId", "label", "depth",
    "aggregate", "liveness", "deviceKind", "mountpoint",
    "sourcePath", "destDisplay", "destSubpath",
    "canSync",
    "containerId", "deviceId", "sourceLabelId", "bindingId",
    "containerLabel",
    "groupKey", "hasChildren", "collapsed", "canMoveUp", "canMoveDown",
)


def _annotate(rows: list[dict], view_mode: str) -> None:
    """Give each row its own group key (rows that can hold children) and
    its ancestors' keys, so collapsing a group can hide its subtree."""
    for r in rows:
        t = r["rowType"]
        c, d, s = r.get("containerId"), r.get("deviceId"), r.get("sourceLabelId")
        r["groupKey"], r["_parents"] = "", []
        r["canMoveUp"] = r["canMoveDown"] = False
        if t == "container":
            r["groupKey"] = f"c{c}"
        elif t == "device":
            r["groupKey"], r["_parents"] = f"d{d}", [f"c{c}"]
        elif t == "source":
            r["groupKey"] = f"s{s}"
        elif t == "connection":
            r["_parents"] = ([f"c{c}", f"d{d}"] if view_mode == "destination"
                             else [f"s{s}"])
        elif t == "importHeader":
            r["groupKey"] = "imports"
        elif t == "importJob":
            r["_parents"] = ["imports"]
    for i, r in enumerate(rows):
        key = r["groupKey"]
        r["hasChildren"] = bool(key) and any(
            key in x["_parents"] for x in rows[i + 1:])
    containers = [r for r in rows if r["rowType"] == "container"]
    for i, r in enumerate(containers):
        r["canMoveUp"] = i > 0
        r["canMoveDown"] = i < len(containers) - 1


class ConnectionsModel(QAbstractListModel):
    viewModeChanged = Signal()
    collapsedKeysChanged = Signal()

    _ROLE_BASE = Qt.UserRole + 1

    def __init__(self, controller: ConnectionsController, db: Database,
                 mounts: MountWatcher, probes: RemoteProbeWatcher,
                 parent=None):
        super().__init__(parent)
        self._controller = controller
        self._db = db
        self._mounts = mounts
        self._probes = probes
        self._view_mode = "destination"
        self._collapsed: set[str] = set()
        self._items: list[dict] = []
        self._roles = {
            self._ROLE_BASE + i: key.encode()
            for i, key in enumerate(_ROLE_KEYS)
        }
        db.db_changed.connect(self.refresh)
        mounts.changed.connect(self.refresh)
        probes.changed.connect(self.refresh)
        self.refresh()

    # --- viewMode property ------------------------------------------------

    def _get_view_mode(self) -> str:
        return self._view_mode

    def _set_view_mode(self, mode: str) -> None:
        if mode == self._view_mode:
            return
        if mode not in ("destination", "source"):
            raise ValueError(f"viewMode must be destination|source, got {mode!r}")
        self._view_mode = mode
        self.viewModeChanged.emit()
        self.refresh()

    viewMode = Property(str, _get_view_mode, _set_view_mode,
                        notify=viewModeChanged)

    # --- collapsed groups (UI state; persisted by QML Settings) ---------

    def _get_collapsed(self) -> str:
        return "|".join(sorted(self._collapsed))

    def _set_collapsed(self, value: str) -> None:
        keys = {k for k in (value or "").split("|") if k}
        if keys == self._collapsed:
            return
        self._collapsed = keys
        self.collapsedKeysChanged.emit()
        self.refresh()

    collapsedKeys = Property(str, _get_collapsed, _set_collapsed,
                             notify=collapsedKeysChanged)

    @Slot(str)
    def toggleCollapsed(self, key: str) -> None:
        self._set_collapsed(self._get_collapsed() + "|" + key
                            if key not in self._collapsed
                            else "|".join(self._collapsed - {key}))

    # --- QAbstractListModel API ------------------------------------------

    def roleNames(self):
        return dict(self._roles)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        key = self._roles.get(role)
        if key is None:
            return None
        item = self._items[index.row()]
        return item.get(key.decode(), "")

    # --- refresh ----------------------------------------------------------

    @Slot()
    def refresh(self) -> None:
        rows = (self._controller.byDestination()
                if self._view_mode == "destination"
                else self._controller.bySource())
        _annotate(rows, self._view_mode)
        for r in rows:
            r["collapsed"] = r["groupKey"] in self._collapsed
        rows = [r for r in rows
                if not self._collapsed.intersection(r["_parents"])]
        self.beginResetModel()
        self._items = rows
        self.endResetModel()
