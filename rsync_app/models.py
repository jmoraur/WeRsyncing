"""QAbstractListModel exposing the connections tree to QML.

Thin wrapper: the controller does the data shaping (DB + mounts +
probes join → row dicts), the model just turns that into a Qt model
with role names. `viewMode` switches between the destination-first
("group", the stored name since 0.10) and source-first row sequences.
"""
from PySide6.QtCore import (
    Property, QAbstractListModel, QModelIndex, Qt, Signal, Slot,
)

from rsync_app.controllers import ConnectionsController
from rsync_app.db import Database
from rsync_app.mounts import MountWatcher
from rsync_app.probes import RemoteProbeWatcher


# Role schema. Rows from the controller carry whichever fields apply;
# missing ones read as "" / -1 / False via _ROLE_DEFAULTS.
_ROLE_DEFAULTS = {
    "rowType": "", "label": "", "depth": 0,
    "aggregate": "", "liveness": "", "where": "",
    "fromDisplay": "", "toDisplay": "", "optsSummary": "",
    "canSync": False,
    "scopeKind": "", "scopeId": -1, "scopeId2": -1,
    "editKind": "", "editId": -1,
    "deviceId": -1, "fromDeviceId": -1, "toDeviceId": -1,
    "jobKey": "", "selectKey": "", "builtIn": False,
    "groupKey": "", "hasChildren": False, "collapsed": False,
    "canMoveUp": False, "canMoveDown": False,
}
_ROLE_KEYS = tuple(_ROLE_DEFAULTS)


def _annotate(rows: list[dict]) -> None:
    """Rows arrive with their own groupKey and ancestor keys (_parents)
    from the controller; work out which have children and which top
    rows can move."""
    for i, r in enumerate(rows):
        key = r.get("groupKey", "")
        r["hasChildren"] = bool(key) and any(
            key in x.get("_parents", []) for x in rows[i + 1:])
    tops = [r for r in rows if r["rowType"] == "top"]
    for i, r in enumerate(tops):
        r["canMoveUp"] = i > 0
        r["canMoveDown"] = i < len(tops) - 1


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
        self._view_mode = "group"
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
        if mode not in ("group", "source"):
            raise ValueError(f"viewMode must be group|source, got {mode!r}")
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

    @Slot(result=list)
    def selectKeys(self) -> list[str]:
        """Selectable rows (jobs, bundles) shown right now, in order."""
        return [r["selectKey"] for r in self._items if r.get("selectKey")]

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
        k = key.decode()
        return item.get(k, _ROLE_DEFAULTS[k])

    # --- refresh ----------------------------------------------------------

    @Slot()
    def refresh(self) -> None:
        rows = (self._controller.byDestination()
                if self._view_mode == "group"
                else self._controller.bySource())
        _annotate(rows)
        for r in rows:
            r["collapsed"] = r.get("groupKey", "") in self._collapsed
        rows = [r for r in rows
                if not self._collapsed.intersection(r.get("_parents", []))]
        self.beginResetModel()
        self._items = rows
        self.endResetModel()
