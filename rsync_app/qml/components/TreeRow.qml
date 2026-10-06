import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// One row of the jobs tree. `rowType` picks the variant:
//   top / importHeader / devicesHeader → section header (top = a device:
//                  the destination or the source, by the Group by choice)
//   device       → the other device of the pair, under a top row
//   bundle       → a bundle of jobs (and bundles) of one device pair
//   deviceEntry  → a device in the Devices list (edit / delete)
//   job / importJob → one line (title) when folded; From / To / Options
//                     + Edit / Delete when expanded
//
// Jobs and bundles can be selected (click, Ctrl+click, Shift+click) and
// have a right-click menu; the page owns the selection and the menu.
//
// The sync button sits in one fixed column at the far left, so all run
// actions line up; indentation starts after it. What a row syncs and
// edits comes from the controller (scopeKind/scopeId…, editKind/editId),
// so this file never works out ids itself.
Item {
    id: row

    // Model roles from ConnectionsModel.
    required property string rowType
    required property string label
    required property int depth
    required property string aggregate
    required property string liveness
    required property string where
    required property string fromDisplay
    required property string toDisplay
    required property string optsSummary
    required property bool canSync
    required property string scopeKind
    required property int scopeId
    required property int scopeId2
    required property string editKind
    required property int editId
    required property int deviceId
    required property int fromDeviceId
    required property int toDeviceId
    required property string jobKey
    required property string selectKey
    required property bool builtIn
    required property string groupKey
    required property bool hasChildren
    required property bool collapsed
    required property bool canMoveUp
    required property bool canMoveDown

    // Job details open/closed and selection — UI state owned by the page.
    property bool expanded: false
    property bool selected: false

    signal syncRequested(string kind, int id, int id2)
    signal editRequested(string kind, int id)
    signal deleteRequested(string kind, int id, string label)
    signal newJobRequested(int fromDeviceId, int toDeviceId)
    signal newDeviceRequested()
    signal newImportRequested()
    signal toggleRequested(string groupKey)
    signal expandRequested(string key)
    signal moveRequested(int deviceId, int delta)
    signal selectRequested(string key, int modifiers)
    signal menuRequested(string key)

    readonly property bool isHeader: rowType === "top"
                                     || rowType === "importHeader"
                                     || rowType === "devicesHeader"
    readonly property bool isJob: rowType === "job" || rowType === "importJob"
    readonly property bool isDevice: rowType === "device"
                                     || rowType === "deviceEntry"

    readonly property int lineHeight: isHeader ? Theme.rowHeader
                                    : isDevice ? Theme.rowDevice
                                    : Theme.rowConnection

    implicitHeight: lineHeight + (details.visible ? details.implicitHeight
                                                    + Theme.s2 : 0)
    height: implicitHeight

    HoverHandler { id: rowHover }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: row.selected ? Theme.tint(Theme.accent, Theme.dark ? 0.30 : 0.18)
             : row.isHeader ? Theme.surfaceAlt
             : row.expanded ? Theme.tint(Theme.textColor, Theme.dark ? 0.05 : 0.03)
             : rowHover.hovered ? Theme.hoverBg
             : "transparent"
    }

    // --- Main line ---------------------------------------------------
    RowLayout {
        id: line
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: row.lineHeight
        anchors.leftMargin: Theme.s1
        anchors.rightMargin: Theme.s2
        spacing: Theme.s2

        // Primary action: sync, always in the first column.
        RowActionButton {
            visible: row.scopeKind !== ""
            icon.name: row.rowType === "importJob" ? "document-import"
                                                   : "view-refresh"
            accent: true
            enabled: row.canSync
            tip: row.rowType === "importJob"
                 ? (row.canSync ? "Import new files from the dump folder"
                                : "One of this import's folders is missing"
                                  + " — edit it first")
                 : row.rowType === "job"
                 ? (row.canSync ? "Sync this job"
                                : "A device of this job is not available")
                 : (row.canSync ? "Sync every job in here"
                                : "Nothing ready to sync")
            onClicked: row.syncRequested(row.scopeKind, row.scopeId,
                                         row.scopeId2)
        }
        Item {
            visible: row.scopeKind === ""
            implicitWidth: 28
        }

        Item { implicitWidth: row.depth * 20 }

        // Fold arrow: headers fold their children, jobs fold their details.
        // Text glyph (renders offscreen too).
        Label {
            readonly property bool foldable: row.hasChildren || row.isJob
            readonly property bool open: row.isJob ? row.expanded
                                                   : !row.collapsed
            text: !foldable ? "" : open ? "▾" : "▸"
            horizontalAlignment: Text.AlignHCenter
            Layout.preferredWidth: 14
            font.pixelSize: 14
            opacity: arrowHover.hovered ? 1.0 : 0.55
            HoverHandler { id: arrowHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: row._fold() }
        }

        LivenessDot {
            visible: row.liveness !== ""
            liveness: row.liveness
        }

        Label {
            text: row.label
            font.weight: row.isHeader ? Font.DemiBold
                       : row.isDevice || row.rowType === "bundle" ? Font.Medium
                       : Font.Normal
            elide: Text.ElideRight
            Layout.fillWidth: row.where === ""
            Layout.maximumWidth: row.where !== "" ? implicitWidth : -1
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            // Jobs and bundles: click selects, double-click folds,
            // right-click opens the menu. Other rows: click folds.
            TapHandler {
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                onSingleTapped: (pt, button) => {
                    if (row.selectKey === "") {
                        if (button === Qt.LeftButton) row._fold()
                    } else if (button === Qt.RightButton) {
                        row.menuRequested(row.selectKey)
                    } else {
                        row.selectRequested(row.selectKey, point.modifiers)
                    }
                }
                onDoubleTapped: (pt, button) => {
                    if (row.selectKey !== "" && button === Qt.LeftButton)
                        row._fold()
                }
            }
        }

        // Devices: where the device lives, dim.
        Label {
            visible: row.where !== ""
            text: row.where
            opacity: 0.5
            font.pixelSize: Theme.fsSmall
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }

        // Group summary ("2/3 ready").
        Label {
            visible: row.aggregate !== "" && !row.isJob
            text: row.aggregate
            font.pixelSize: Theme.fsSmall
            opacity: 0.55
        }

        // --- Hover actions for headers and devices --------------------
        RowLayout {
            spacing: 0
            visible: !row.isJob && rowHover.hovered

            RowActionButton {
                visible: row.rowType === "top"
                enabled: row.canMoveUp
                icon.name: "go-up"
                tip: "Move up"
                onClicked: row.moveRequested(row.deviceId, -1)
            }
            RowActionButton {
                visible: row.rowType === "top"
                enabled: row.canMoveDown
                icon.name: "go-down"
                tip: "Move down"
                onClicked: row.moveRequested(row.deviceId, 1)
            }
            RowActionButton {
                visible: row.rowType === "device"
                icon.name: "list-add"
                tip: "New job here"
                onClicked: row.newJobRequested(row.fromDeviceId,
                                               row.toDeviceId)
            }
            RowActionButton {
                visible: row.rowType === "devicesHeader"
                icon.name: "list-add"
                tip: "New device"
                onClicked: row.newDeviceRequested()
            }
            RowActionButton {
                visible: row.rowType === "importHeader"
                icon.name: "list-add"
                tip: "New import"
                onClicked: row.newImportRequested()
            }
            RowActionButton {
                visible: row.editKind !== ""
                icon.name: "document-edit"
                tip: row.rowType === "bundle" ? "Rename bundle"
                     : row.isDevice || row.rowType === "top"
                     ? "Edit device" : "Edit"
                onClicked: row.editRequested(row.editKind, row.editId)
            }
            RowActionButton {
                visible: row.rowType === "deviceEntry" && !row.builtIn
                icon.name: "edit-delete"
                danger: true
                tip: "Delete"
                onClicked: row.deleteRequested(row.editKind, row.editId,
                                               row.label)
            }
        }
    }

    // --- Job details (expanded) --------------------------------------
    ColumnLayout {
        id: details
        visible: row.isJob && row.expanded
        anchors.top: line.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        // Line up under the title: sync column + indent + arrow + dot.
        anchors.leftMargin: Theme.s1 + 28 + row.depth * 20 + 14
                            + 8 + 4 * Theme.s2
        anchors.rightMargin: Theme.s3
        spacing: Theme.s1

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.s3
            rowSpacing: 2

            Label { text: "From"; opacity: 0.55; font.pixelSize: Theme.fsSmall; Layout.alignment: Qt.AlignTop }
            Label {
                text: row.fromDisplay
                font.family: Theme.mono
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.WrapAnywhere
                Layout.fillWidth: true
            }
            Label { text: "To"; opacity: 0.55; font.pixelSize: Theme.fsSmall; Layout.alignment: Qt.AlignTop }
            Label {
                text: row.toDisplay
                font.family: Theme.mono
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.WrapAnywhere
                Layout.fillWidth: true
            }
            Label { text: "Options"; opacity: 0.55; font.pixelSize: Theme.fsSmall; Layout.alignment: Qt.AlignTop }
            Label {
                text: row.optsSummary
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }

        RowLayout {
            spacing: Theme.s1
            Button {
                flat: true
                text: "Edit"
                icon.name: "document-edit"
                icon.color: Theme.textColor
                font.pixelSize: Theme.fsSmall
                onClicked: row.editRequested(row.editKind, row.editId)
            }
            Button {
                flat: true
                text: "Delete"
                icon.name: "edit-delete"
                icon.color: Theme.textColor
                font.pixelSize: Theme.fsSmall
                onClicked: row.deleteRequested(row.editKind, row.editId,
                                               row.label)
            }
        }
    }

    function _fold() {
        if (row.isJob) row.expandRequested(row.jobKey)
        else if (row.hasChildren) row.toggleRequested(row.groupKey)
    }
}
