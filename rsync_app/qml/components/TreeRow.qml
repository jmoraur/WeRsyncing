import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// One row of the connections tree. `rowType` picks the variant:
//   container / source  → group header (tinted band, bold label, pill)
//   device              → liveness dot + label + mono mountpoint/target
//   connection          → label + mono "source → dest" + run button
//
// The primary sync action is always visible (it is the point of the
// app); edit / delete / add are icon buttons revealed on hover.
Item {
    id: row

    // Model roles from ConnectionsModel.
    required property string rowType
    required property int nodeId
    required property string label
    required property int depth
    required property string aggregate
    required property string liveness
    required property string deviceKind
    required property string mountpoint
    required property string sourcePath
    required property string destDisplay
    required property string destSubpath
    required property bool canSync
    required property int containerId
    required property int deviceId
    required property int sourceLabelId
    required property int bindingId

    signal syncRequested(string scopeKind, int scopeId)
    signal editRequested(string rowType, int nodeId)
    signal deleteRequested(string rowType, int nodeId, string label)
    signal addConnectionRequested(int deviceId)
    signal addDeviceRequested(int containerId)

    readonly property bool isHeader: rowType === "container"
                                     || rowType === "source"
                                     || rowType === "importHeader"
    readonly property bool isDevice: rowType === "device"
    readonly property bool isConnection: rowType === "connection"
    readonly property bool isImportHeader: rowType === "importHeader"
    readonly property bool isImportJob: rowType === "importJob"
    readonly property int editTarget: isConnection ? bindingId : nodeId
    readonly property string syncScope: isConnection ? "connection"
                                      : isImportJob ? "import"
                                      : rowType
    readonly property int syncId: isConnection ? bindingId
                                : isDevice ? deviceId
                                : rowType === "source" ? sourceLabelId
                                : isImportJob ? nodeId
                                : containerId

    implicitHeight: isHeader ? Theme.rowHeader
                  : isDevice ? Theme.rowDevice
                  : Theme.rowConnection
    height: implicitHeight

    HoverHandler { id: rowHover }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: row.isHeader ? Theme.surfaceAlt
             : rowHover.hovered ? Theme.hoverBg
             : "transparent"
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.s3 + row.depth * 22
        anchors.rightMargin: Theme.s2
        spacing: Theme.s2

        LivenessDot {
            visible: row.liveness !== ""
            liveness: row.liveness
        }

        Label {
            text: row.label
            font.weight: row.isHeader ? Font.Bold
                       : row.isDevice ? Font.DemiBold
                       : Font.Normal
            elide: Text.ElideRight
            Layout.preferredWidth: row.isHeader ? implicitWidth
                                                : Math.min(implicitWidth, 230)
        }

        // Secondary text: paths in monospace, dimmed.
        Label {
            text: {
                if (row.isDevice)
                    return row.deviceKind === "local"
                           ? (row.mountpoint || "not mounted")
                           : row.destDisplay
                if (row.isConnection || row.isImportJob)
                    return row.sourcePath + "  →  " + row.destDisplay
                if (row.rowType === "source")
                    return row.sourcePath
                return ""
            }
            visible: text !== ""
            opacity: 0.6
            font.family: Theme.mono
            font.pixelSize: Theme.fsMono
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }

        // Container rows have no secondary text — flexible spacer so the
        // pill + actions hug the right.
        Item {
            visible: row.rowType === "container"
            Layout.fillWidth: true
        }

        // Aggregate pill ("2/3 reachable", "empty", "no connections").
        Rectangle {
            visible: row.aggregate !== ""
            implicitWidth: pillLabel.implicitWidth + 14
            implicitHeight: 18
            radius: 9
            color: Theme.tint(Theme.textColor, 0.08)

            Label {
                id: pillLabel
                anchors.centerIn: parent
                text: row.aggregate
                font.pixelSize: Theme.fsSmall
                opacity: 0.75
            }
        }

        // --- Hover-revealed secondary actions -------------------------
        RowLayout {
            spacing: 0
            opacity: rowHover.hovered ? 1.0 : 0.0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 120 } }

            RowActionButton {
                visible: row.rowType === "container"
                icon.name: "list-add"
                tip: "Add device to this container"
                onClicked: row.addDeviceRequested(row.containerId)
            }
            RowActionButton {
                visible: row.isDevice
                icon.name: "list-add"
                tip: "Add connection to this device"
                onClicked: row.addConnectionRequested(row.deviceId)
            }
            RowActionButton {
                visible: !row.isImportHeader
                icon.name: "document-edit"
                tip: "Edit"
                onClicked: row.editRequested(row.rowType, row.editTarget)
            }
            RowActionButton {
                visible: !row.isImportHeader
                icon.name: "edit-delete"
                danger: true
                tip: "Delete"
                onClicked: row.deleteRequested(row.rowType, row.editTarget,
                                               row.label)
            }
        }

        // --- Primary sync action (always visible) ---------------------
        RowActionButton {
            visible: !row.isImportHeader
            icon.name: row.isImportJob ? "document-import" : "view-refresh"
            accent: true
            enabled: row.canSync
            tip: row.isImportJob
                 ? (row.canSync ? "Import new files from the dump folder"
                                : "One of this import's folders is missing"
                                  + " — edit it first")
                 : row.isConnection
                 ? (row.canSync ? "Sync this connection"
                                : "Destination unreachable")
                 : (row.canSync ? "Sync everything reachable here"
                                : "Nothing reachable to sync")
            onClicked: row.syncRequested(row.syncScope, row.syncId)
        }
    }
}
