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
    }

    // ---- Routing ------------------------------------------------------

    function toggleExpanded(key) {
        const keys = _expandedJobs.split("|").filter(k => k)
        const i = keys.indexOf(key)
        if (i >= 0) keys.splice(i, 1)
        else keys.push(key)
        _expandedJobs = keys.join("|")
    }

    function newJob(groupId, toDeviceId) {
        jobForm.jobId = -1
        jobForm.initialGroupId = groupId
        jobForm.initialToDeviceId = toDeviceId
        jobForm.open()
    }

    function newGroup() {
        groupForm.groupId = -1
        groupForm.initialLabel = ""
        groupForm.open()
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
        if (kind === "group") {
            groupForm.groupId = id
            groupForm.initialLabel = connections.getGroup(id).label || ""
            groupForm.open()
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
                    MenuItem { text: "Group…"; onTriggered: page.newGroup() }
                    MenuItem { text: "Device…"; onTriggered: page.newDevice() }
                    MenuSeparator {}
                    MenuItem { text: "Import…"; onTriggered: page.newImportJob() }
                }
            }

            Item { Layout.fillWidth: true }

            Label {
                text: "View"
                opacity: 0.6
            }
            SegmentedControl {
                model: [
                    {value: "group", label: "Groups"},
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
                        onSyncRequested: (k, i, i2) => page.runSync(k, i, i2)
                        onEditRequested: (k, i) => page.editEntity(k, i)
                        onDeleteRequested: (k, i, l) => page.deleteEntity(k, i, l)
                        onNewJobRequested: (g, d) => page.newJob(g, d)
                        onNewDeviceRequested: page.newDevice()
                        onNewImportRequested: page.newImportJob()
                        onToggleRequested: key =>
                            connectionsModel.toggleCollapsed(key)
                        onExpandRequested: key => page.toggleExpanded(key)
                        onMoveRequested: (g, delta) =>
                            connections.moveGroup(g, delta)
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

    GroupForm       { id: groupForm; objectName: "groupForm" }
    DeviceForm      { id: deviceForm; objectName: "deviceForm" }
    JobForm         { id: jobForm; objectName: "jobForm" }
    ImportJobForm   { id: importJobForm; objectName: "importJobForm" }

    SyncConfirmWindow { id: syncConfirm; objectName: "syncConfirm" }
    ImportConfirmWindow { id: importConfirm; objectName: "importConfirm" }
    DeleteConfirmWindow {
        id: deleteConfirm
        objectName: "deleteConfirm"
        onConfirmed: (kind, id) => {
            if (kind === "group")          connections.deleteGroup(id)
            else if (kind === "device")    connections.deleteDevice(id)
            else if (kind === "job")       connections.deleteJob(id)
            else if (kind === "importJob") connections.deleteImportJob(id)
        }
    }
}
