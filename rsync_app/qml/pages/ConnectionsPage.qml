import QtCore
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../forms"

Item {
    id: page
    objectName: "page"

    // Persisted via Settings below. Drives OutputPanel SplitView height.
    property int _outputHeight: 280

    // Gates the OutputPanel's onHeightChanged writeback. During the first
    // ~500ms the SplitView briefly sizes the panel to its minimumHeight
    // (120) while the parent fills — without this gate, that transient
    // value gets persisted and the user's real drag is lost.
    property bool _heightCaptureReady: false

    // viewMode can't be aliased — connectionsModel is a context property
    // (no QML id). Push on startup; capture changes via Connections.
    Settings {
        id: uiSettings
        category: "ui"
        property string viewMode: "destination"
        property alias outputCollapsed: outputPanel.collapsed
        property alias outputHeight: page._outputHeight
        property alias outputFilter: outputPanel._filter
    }

    Component.onCompleted: {
        connectionsModel.viewMode = uiSettings.viewMode
        heightReadyTimer.start()
    }

    Timer {
        id: heightReadyTimer
        interval: 500
        onTriggered: page._heightCaptureReady = true
    }

    Connections {
        target: connectionsModel
        function onViewModeChanged() {
            uiSettings.viewMode = connectionsModel.viewMode
        }
    }

    // ---- Form routing ------------------------------------------------
    // Page-level dispatch: a `+ new` / `edit` click on a row routes to
    // the right form with the right prefills. `del` opens the cascade-
    // aware confirm dialog which calls the controller on Yes.

    function newConnection(prefillDeviceId) {
        connectionForm.bindingId = -1
        connectionForm.initialSourceId = -1
        connectionForm.initialDeviceId = prefillDeviceId !== undefined
                                        ? prefillDeviceId : -1
        connectionForm.open()
    }

    function newDevice(prefillContainerId) {
        deviceForm.deviceId = -1
        deviceForm.initialContainerId = prefillContainerId !== undefined
                                       ? prefillContainerId : -1
        deviceForm.initialKind = "local"
        deviceForm.initialLabel = ""
        deviceForm.initialUuid = ""
        deviceForm.initialNetworkTarget = ""
        deviceForm.initialRsh = ""
        deviceForm.open()
    }

    function editEntity(rowType, nodeId) {
        if (rowType === "source") {
            const row = connections.getSourceLabel(nodeId)
            if (!row.id) return
            sourceLabelForm.sourceLabelId = nodeId
            sourceLabelForm.initialLabel = row.label || ""
            sourceLabelForm.initialPath = row.path || ""
            sourceLabelForm.open()
        } else if (rowType === "container") {
            const row = connections.getDestContainer(nodeId)
            if (!row.id) return
            containerForm.containerId = nodeId
            containerForm.initialLabel = row.label || ""
            containerForm.open()
        } else if (rowType === "device") {
            const row = connections.getDestDevice(nodeId)
            if (!row.id) return
            deviceForm.deviceId = nodeId
            deviceForm.initialContainerId = row.container_id || -1
            deviceForm.initialKind = row.kind || "local"
            deviceForm.initialLabel = row.label || ""
            deviceForm.initialUuid = row.uuid || ""
            deviceForm.initialNetworkTarget = row.network_target || ""
            deviceForm.initialRsh = row.rsh || ""
            deviceForm.open()
        } else if (rowType === "connection") {
            connectionForm.bindingId = nodeId
            connectionForm.initialSourceId = -1
            connectionForm.initialDeviceId = -1
            connectionForm.open()
        } else if (rowType === "importJob") {
            const row = connections.getImportJob(nodeId)
            if (!row.id) return
            importJobForm.importJobId = nodeId
            importJobForm.initialLabel = row.label || ""
            importJobForm.initialDump = row.dump_path || ""
            importJobForm.initialDest = row.dest_path || ""
            importJobForm.initialArchive = row.archive_root || ""
            importJobForm.initialCopyMode = row.copy_mode || 0
            importJobForm.open()
        }
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

    function deleteEntity(rowType, nodeId, label) {
        deleteConfirm.rowType = rowType
        deleteConfirm.nodeId = nodeId
        deleteConfirm.label = label
        deleteConfirm.open()
    }

    function runSync(scopeKind, scopeId) {
        if (scopeKind === "import") {
            importConfirmDialog.openFor(scopeId)
            return
        }
        syncConfirmDialog.openFor(scopeKind, scopeId)
    }

    // Vertical SplitView: connection tree on top, sync output panel
    // on bottom. When the panel is collapsed it locks to header-only
    // height; otherwise the user can drag the divider.
    SplitView {
        anchors.fill: parent
        anchors.margins: Theme.s3
        orientation: Qt.Vertical

        Item {
            SplitView.fillHeight: true
            SplitView.minimumHeight: 200

            ColumnLayout {
                anchors.fill: parent
                spacing: Theme.s3

                // Toolbar row.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s3

                    Wordmark {
                        Layout.rightMargin: Theme.s3
                    }

                    Button {
                        text: "New connection"
                        icon.name: "list-add"
                        // Recolor: theme icons come from the session's icon
                        // theme, which may not match a forced light/dark mode.
                        icon.color: Theme.textColor
                        highlighted: true
                        onClicked: page.newConnection(-1)
                    }

                    Button {
                        text: "New import"
                        icon.name: "document-import"
                        icon.color: Theme.textColor
                        onClicked: page.newImportJob()
                    }

                    Item { Layout.fillWidth: true }

                    Label {
                        text: "Group by"
                        opacity: 0.6
                    }

                    SegmentedControl {
                        model: [
                            {value: "destination", label: "Destination"},
                            {value: "source", label: "Source"},
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

                // The tree itself.
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    ListView {
                        id: tree
                        model: connectionsModel
                        spacing: 2
                        interactive: true

                        delegate: TreeRow {
                            width: tree.width
                            onSyncRequested: (scopeKind, scopeId) =>
                                page.runSync(scopeKind, scopeId)
                            onEditRequested: (rowType, nodeId) =>
                                page.editEntity(rowType, nodeId)
                            onDeleteRequested: (rowType, nodeId, lbl) =>
                                page.deleteEntity(rowType, nodeId, lbl)
                            onAddConnectionRequested: deviceId =>
                                page.newConnection(deviceId)
                            onAddDeviceRequested: containerId =>
                                page.newDevice(containerId)
                        }
                    }
                }

                // Empty state. Shown only when the model has no rows.
                ColumnLayout {
                    visible: tree.count === 0
                    Layout.alignment: Qt.AlignHCenter
                    spacing: Theme.s2

                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Nothing here yet"
                        font.weight: Font.DemiBold
                        font.pixelSize: Theme.fsHeading
                        opacity: 0.7
                    }
                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: "A connection pairs a source folder with a destination device."
                        opacity: 0.5
                    }
                    Button {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.bottomMargin: Theme.s4
                        text: "Create your first connection"
                        icon.name: "list-add"
                        icon.color: Theme.textColor
                        onClicked: page.newConnection(-1)
                    }
                }
            }
        }

        OutputPanel {
            id: outputPanel
            // SplitView doesn't treat -1 as "no maximum" — it clamps
            // to that literal value. Use a large number instead.
            SplitView.preferredHeight: collapsed ? headerHeight : page._outputHeight
            SplitView.minimumHeight: collapsed ? headerHeight : 120
            SplitView.maximumHeight: collapsed ? headerHeight : 99999
            onHeightChanged: {
                if (page._heightCaptureReady && !collapsed) {
                    page._outputHeight = height
                }
            }
        }
    }

    // ---- Form dialogs (instantiated once, reused) --------------------

    SourceLabelForm   { id: sourceLabelForm; objectName: "sourceLabelForm" }
    ContainerForm     { id: containerForm; objectName: "containerForm" }
    DeviceForm        { id: deviceForm; objectName: "deviceForm" }
    ConnectionForm    { id: connectionForm; objectName: "connectionForm" }
    SyncConfirmDialog { id: syncConfirmDialog; objectName: "syncConfirmDialog" }
    ImportJobForm     { id: importJobForm; objectName: "importJobForm" }
    ImportConfirmDialog {
        id: importConfirmDialog
        objectName: "importConfirmDialog"
    }

    DeleteConfirmDialog {
        id: deleteConfirm
        objectName: "deleteConfirm"
        onConfirmed: function(rowType, nodeId) {
            if (rowType === "source")         connections.deleteSourceLabel(nodeId)
            else if (rowType === "container") connections.deleteDestContainer(nodeId)
            else if (rowType === "device")    connections.deleteDestDevice(nodeId)
            else if (rowType === "connection") connections.deleteBinding(nodeId)
            else if (rowType === "importJob") connections.deleteImportJob(nodeId)
        }
    }
}
