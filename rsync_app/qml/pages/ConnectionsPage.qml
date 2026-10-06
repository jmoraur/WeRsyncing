import QtCore
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../forms"

// The one page: jobs on the left, activity (runs + log) on the right.
Item {
    id: page
    objectName: "page"

    // Width of the jobs pane, persisted. The SplitView briefly lays the
    // pane out at its minimum while the window fills; only save widths
    // after that settles, or the user's width is lost.
    property int _jobsWidth: 520
    property bool _widthReady: false
    Timer { interval: 500; running: true; onTriggered: page._widthReady = true }
    // Job details open/closed: "|"-joined jobKeys ("j12", "i3"). Jobs are
    // folded by default, so this stores the open ones.
    property string _expandedJobs: ""
    readonly property var _expanded: {
        const out = {}
        for (const k of _expandedJobs.split("|")) if (k) out[k] = true
        return out
    }
    // Selected jobs / bundles ("j<id>" / "b<id>"), in list order.
    property var _selection: []
    property string _anchor: ""
    readonly property var _selected: {
        const out = {}
        for (const k of _selection) out[k] = true
        return out
    }

    // viewMode can't be aliased — connectionsModel is a context property
    // (no QML id). Push on startup; capture changes via Connections.
    Settings {
        id: uiSettings
        category: "ui"
        property string viewMode: "group"
        property string collapsedKeys: ""
        property alias expandedJobs: page._expandedJobs
        property alias jobsWidth: page._jobsWidth
        property alias outputFilter: outputPanel._filter
    }

    Component.onCompleted: {
        // Views were "destination"/"source" before 0.10.
        connectionsModel.viewMode = uiSettings.viewMode === "source"
                                    ? "source" : "group"
        connectionsModel.collapsedKeys = uiSettings.collapsedKeys
    }

    Connections {
        target: connectionsModel
        function onViewModeChanged() {
            uiSettings.viewMode = connectionsModel.viewMode
        }
        function onCollapsedKeysChanged() {
            uiSettings.collapsedKeys = connectionsModel.collapsedKeys
        }
        // Drop selected rows that are gone (bundled away, deleted).
        function onModelReset() {
            const shown = connectionsModel.selectKeys()
            page._selection = page._selection.filter(k => shown.includes(k))
        }
    }

    // ---- Selection + right-click menu ---------------------------------

    function select(key, modifiers) {
        if (modifiers & Qt.ControlModifier) {
            _selection = _selected[key] ? _selection.filter(k => k !== key)
                                        : _inOrder(_selection.concat([key]))
            _anchor = key
        } else if ((modifiers & Qt.ShiftModifier) && _anchor !== "") {
            const keys = connectionsModel.selectKeys()
            const a = keys.indexOf(_anchor), b = keys.indexOf(key)
            if (a < 0 || b < 0) { _selection = [key]; _anchor = key; return }
            _selection = keys.slice(Math.min(a, b), Math.max(a, b) + 1)
        } else {
            _selection = [key]
            _anchor = key
        }
    }

    function _inOrder(keys) {
        return connectionsModel.selectKeys().filter(k => keys.includes(k))
    }

    function openMenu(key) {
        if (!_selected[key]) { _selection = [key]; _anchor = key }
        rowMenu.keys = _selection
        rowMenu.popup()
    }

    function newBundle(keys) {
        bundleForm.bundleId = -1
        bundleForm.keys = keys
        bundleForm.initialLabel = ""
        bundleForm.open()
    }

    function renameBundle(id) {
        bundleForm.bundleId = id
        bundleForm.initialLabel = connections.getBundle(id).label || ""
        bundleForm.open()
    }

    // ---- Routing ------------------------------------------------------

    function toggleExpanded(key) {
        const keys = _expandedJobs.split("|").filter(k => k)
        const i = keys.indexOf(key)
        if (i >= 0) keys.splice(i, 1)
        else keys.push(key)
        _expandedJobs = keys.join("|")
    }

    function newJob(fromDeviceId, toDeviceId) {
        jobForm.jobId = -1
        jobForm.initialFromDeviceId = fromDeviceId
        jobForm.initialToDeviceId = toDeviceId
        jobForm.open()
    }

    function newDevice() {
        deviceForm.deviceId = -1
        deviceForm.open()
    }

    function newImportJob() {
        importJobForm.importJobId = -1
        importJobForm.initialLabel = ""
        importJobForm.initialDump = ""
        importJobForm.initialDest = ""
        importJobForm.initialArchive = ""
        importJobForm.initialCopyMode = 0
        importJobForm.open()
    }

    function editEntity(kind, id) {
        if (kind === "bundle") {
            renameBundle(id)
        } else if (kind === "device") {
            deviceForm.deviceId = id
            deviceForm.open()
        } else if (kind === "job") {
            jobForm.jobId = id
            jobForm.open()
        } else if (kind === "importJob") {
            const row = connections.getImportJob(id)
            if (!row.id) return
            importJobForm.importJobId = id
            importJobForm.initialLabel = row.label || ""
            importJobForm.initialDump = row.dump_path || ""
            importJobForm.initialDest = row.dest_path || ""
            importJobForm.initialArchive = row.archive_root || ""
            importJobForm.initialCopyMode = row.copy_mode || 0
            importJobForm.open()
        }
    }

    function deleteEntity(kind, id, label) {
        deleteConfirm.kind = kind
        deleteConfirm.itemId = id
        deleteConfirm.label = label
        deleteConfirm.open()
    }

    function runSync(kind, id, id2) {
        if (kind === "import") importConfirm.openFor(id)
        else syncConfirm.openFor(kind, id, id2)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.s3
        spacing: Theme.s3

        // ---- Toolbar --------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3

            Wordmark { Layout.rightMargin: Theme.s3 }

            Button {
                text: "New"
                icon.name: "list-add"
                icon.color: Theme.textColor
                highlighted: true
                onClicked: newMenu.open()

                Menu {
                    id: newMenu
                    y: parent.height
                    MenuItem { text: "Job…"; onTriggered: page.newJob(-1, -1) }
                    MenuItem { text: "Device…"; onTriggered: page.newDevice() }
                    MenuSeparator {}
                    MenuItem { text: "Import…"; onTriggered: page.newImportJob() }
                }
            }

            Item { Layout.fillWidth: true }

            Label {
                text: "Group by"
                opacity: 0.6
            }
            SegmentedControl {
                model: [
                    {value: "group", label: "Destinations"},
                    {value: "source", label: "Sources"},
                ]
                value: connectionsModel.viewMode
                onActivated: v => connectionsModel.viewMode = v
            }

            ToolButton {
                icon.name: "contrast"
                icon.color: Theme.textColor
                onClicked: themeMenu.open()

                Menu {
                    id: themeMenu
                    objectName: "themeMenu"
                    y: parent.height

                    Repeater {
                        model: [
                            {value: "system", label: "System"},
                            {value: "light", label: "Light"},
                            {value: "dark", label: "Dark"},
                        ]
                        MenuItem {
                            required property var modelData
                            text: modelData.label
                            checkable: true
                            checked: themeBridge.mode === modelData.value
                            onTriggered: themeBridge.setMode(modelData.value)
                        }
                    }
                }
            }
        }

        // ---- Jobs | Activity ------------------------------------------
        SplitView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            // Wide, easy-to-catch grip with a visible line.
            handle: Item {
                implicitWidth: 12
                Rectangle {
                    anchors.centerIn: parent
                    width: parent.SplitHandle.hovered
                           || parent.SplitHandle.pressed ? 4 : 2
                    height: parent.height - 2 * Theme.s3
                    radius: 2
                    color: parent.SplitHandle.pressed ? Theme.accent
                         : parent.SplitHandle.hovered
                           ? Theme.tint(Theme.accent, 0.7)
                         : Theme.border
                }
            }

            Item {
                SplitView.preferredWidth: page._jobsWidth
                SplitView.minimumWidth: 320
                onWidthChanged: if (page._widthReady) page._jobsWidth = width

                ListView {
                    id: tree
                    anchors.fill: parent
                    model: connectionsModel
                    spacing: 2
                    clip: true
                    ScrollBar.vertical: ScrollBar {}

                    delegate: TreeRow {
                        width: tree.width - Theme.s2
                        expanded: !!page._expanded[jobKey]
                        selected: !!page._selected[selectKey]
                        onSelectRequested: (k, m) => page.select(k, m)
                        onMenuRequested: k => page.openMenu(k)
                        onSyncRequested: (k, i, i2) => page.runSync(k, i, i2)
                        onEditRequested: (k, i) => page.editEntity(k, i)
                        onDeleteRequested: (k, i, l) => page.deleteEntity(k, i, l)
                        onNewJobRequested: (g, d) => page.newJob(g, d)
                        onNewDeviceRequested: page.newDevice()
                        onNewImportRequested: page.newImportJob()
                        onToggleRequested: key =>
                            connectionsModel.toggleCollapsed(key)
                        onExpandRequested: key => page.toggleExpanded(key)
                        onMoveRequested: (d, delta) =>
                            connections.moveDevice(d, delta,
                                                   connectionsModel.viewMode)
                    }
                }
            }

            OutputPanel {
                id: outputPanel
                objectName: "outputPanel"
                SplitView.fillWidth: true
                SplitView.minimumWidth: 320
            }
        }
    }

    // ---- Forms (instantiated once, reused) ----------------------------

    BundleForm {
        id: bundleForm
        objectName: "bundleForm"
        onDone: page._selection = []
    }
    DeviceForm      { id: deviceForm; objectName: "deviceForm" }
    JobForm         { id: jobForm; objectName: "jobForm" }
    ImportJobForm   { id: importJobForm; objectName: "importJobForm" }

    SyncConfirmWindow { id: syncConfirm; objectName: "syncConfirm" }
    ImportConfirmWindow { id: importConfirm; objectName: "importConfirm" }
    DeleteConfirmWindow {
        id: deleteConfirm
        objectName: "deleteConfirm"
        onConfirmed: (kind, id) => {
            if (kind === "device")         connections.deleteDevice(id)
            else if (kind === "job")       connections.deleteJob(id)
            else if (kind === "importJob") connections.deleteImportJob(id)
        }
    }

    // Right-click on a job or bundle; acts on the whole selection.
    Menu {
        id: rowMenu
        objectName: "rowMenu"
        property var keys: []
        readonly property bool _oneBundle: keys.length === 1
                                           && keys[0].startsWith("b")
        readonly property bool _oneJob: keys.length === 1
                                        && keys[0].startsWith("j")
        readonly property bool _allBundles: keys.length > 0
                                            && keys.every(k => k.startsWith("b"))
        property string _problem: ""
        property bool _canTakeOut: false
        onAboutToShow: {
            _problem = connections.bundleProblem(keys)
            _canTakeOut = connections.canTakeOut(keys)
        }

        // Entries that don't apply are hidden; Bundle… stays, dimmed,
        // with the reason it can't be used.
        width: Math.max(220, bundleItem.implicitWidth + 40)

        MenuItem {
            id: bundleItem
            text: rowMenu._problem === "" ? "Bundle…"
                  : "Bundle… — " + rowMenu._problem
            enabled: rowMenu._problem === ""
            opacity: enabled ? 1.0 : 0.45
            onTriggered: page.newBundle(rowMenu.keys)
        }
        MenuItem {
            text: "Take out of bundle"
            visible: rowMenu._canTakeOut
            height: visible ? implicitHeight : 0
            onTriggered: {
                connections.takeOut(rowMenu.keys)
                page._selection = []
            }
        }
        MenuItem {
            text: rowMenu.keys.length > 1 ? "Unbundle all" : "Unbundle"
            visible: rowMenu._allBundles
            height: visible ? implicitHeight : 0
            onTriggered: {
                for (const k of rowMenu.keys)
                    connections.unbundle(parseInt(k.slice(1)))
                page._selection = []
            }
        }
        MenuItem {
            text: rowMenu._oneBundle ? "Rename…" : "Edit…"
            visible: rowMenu._oneBundle || rowMenu._oneJob
            height: visible ? implicitHeight : 0
            onTriggered: page.editEntity(rowMenu._oneBundle ? "bundle" : "job",
                                         parseInt(rowMenu.keys[0].slice(1)))
        }
    }
}
