import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Generic delete confirmation. Caller sets the target (rowType, id,
// label) then opens the dialog; cascade impact is computed at open
// time so the warning text reflects current state.
Dialog {
    id: dlg
    modal: true
    title: "Confirm delete"
    standardButtons: Dialog.Yes | Dialog.No
    width: 460

    property string rowType: ""
    property int    nodeId: -1
    property string label: ""

    // Cascade summary text, computed on open.
    property string _cascadeText: ""

    signal confirmed(string rowType, int nodeId)

    function _computeCascade() {
        if (dlg.rowType === "container") {
            const devs = connections.pickableDevices(dlg.nodeId)
            const binds = connections.bindingsForScope("container", dlg.nodeId)
            if (devs.length === 0 && binds.length === 0) {
                _cascadeText = "This container is empty."
            } else {
                _cascadeText = "Will also delete " + devs.length +
                               " device(s) and " + binds.length +
                               " connection(s) under this container."
            }
        } else if (dlg.rowType === "device") {
            const binds = connections.bindingsForScope("device", dlg.nodeId)
            _cascadeText = binds.length === 0
                ? "No connections use this device."
                : "Will also delete " + binds.length +
                  " connection(s) on this device."
        } else if (dlg.rowType === "source") {
            const binds = connections.bindingsForScope("source", dlg.nodeId)
            _cascadeText = binds.length === 0
                ? "No connections use this source label."
                : "Will also delete " + binds.length +
                  " connection(s) that use this source."
        } else if (dlg.rowType === "importJob") {
            _cascadeText = "Only this import setup is removed — no files"
                           + " or folders are touched."
        } else {
            _cascadeText = ""   // connection delete has no cascade
        }
    }

    onAboutToShow: _computeCascade()

    onAccepted: dlg.confirmed(dlg.rowType, dlg.nodeId)

    contentItem: ColumnLayout {
        spacing: Theme.s2

        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: {
                const kind = dlg.rowType === "container"  ? "container"
                           : dlg.rowType === "device"     ? "destination device"
                           : dlg.rowType === "source"     ? "source"
                           : dlg.rowType === "connection" ? "connection"
                           : dlg.rowType === "importJob"  ? "import"
                                                          : "item"
                return "Delete " + kind + " “" + dlg.label + "”?"
            }
            font.weight: Font.DemiBold
        }

        Label {
            visible: dlg._cascadeText !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: dlg._cascadeText
            opacity: 0.75
        }

        Label {
            visible: dlg.rowType === "container" || dlg.rowType === "source"
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: "This cannot be undone."
            color: Theme.error
        }
    }
}
