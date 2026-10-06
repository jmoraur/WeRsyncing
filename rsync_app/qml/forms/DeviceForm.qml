import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Device dialog. A device is a place folders live, identity only — no
// paths or rsync options here (those belong to the job):
//   drive  → picked from the mounted drives (filesystem UUID)
//   server → user@host + optional ssh command, with a reachability test
// The laptop is built in; only its name can be edited.
//
// Modes: deviceId === -1 → add, else → edit (kind is then fixed).
Dialog {
    id: dlg
    modal: true
    anchors.centerIn: Overlay.overlay
    title: dlg.deviceId === -1 ? "New device" : "Edit device"
    standardButtons: Dialog.Save | Dialog.Cancel
    width: 580

    property int deviceId: -1

    signal acceptedWithId(int newId)

    property string _kind: "drive"
    property string _selectedUuid: ""
    property string _probeResult: ""

    function _refreshUuids(preselectUuid) {
        const list = connections.unassignedUuids()
        // In edit mode keep the device's own UUID in the list.
        if (dlg.deviceId !== -1 && preselectUuid
                && !list.some(x => x.uuid === preselectUuid)) {
            list.unshift({ uuid: preselectUuid, mountpoint: "(this drive)" })
        }
        uuidCombo.model = list
        const idx = list.findIndex(x => x.uuid === preselectUuid)
        uuidCombo.currentIndex = idx
        _selectedUuid = idx === -1 ? "" : preselectUuid
    }

    onAboutToShow: {
        const d = dlg.deviceId === -1 ? {} : connections.getDevice(dlg.deviceId)
        _kind = d.kind || "drive"
        labelField.text = d.label || ""
        hostField.text = d.host || ""
        rshField.text = d.rsh || ""
        _probeResult = ""
        _refreshUuids(d.uuid || "")
        labelField.forceActiveFocus()
    }

    onAccepted: {
        const draft = {
            label: labelField.text.trim(),
            kind: _kind,
            uuid: _selectedUuid,
            host: hostField.text.trim(),
            rsh: rshField.text.trim(),
        }
        if (!draft.label) return
        if (_kind === "drive" && !draft.uuid) return
        if (_kind === "server" && !draft.host) return
        let newId = dlg.deviceId
        if (dlg.deviceId === -1) newId = connections.addDevice(draft)
        else connections.updateDevice(dlg.deviceId, draft)
        dlg.acceptedWithId(newId)
    }

    contentItem: ColumnLayout {
        spacing: Theme.s2

        SectionLabel { text: "KIND" }
        SegmentedControl {
            visible: dlg._kind !== "laptop"
            // Kind is identity; don't flip after creation.
            enabled: dlg.deviceId === -1
            opacity: enabled ? 1.0 : 0.6
            model: [
                {value: "drive", label: "Drive (USB / disk)"},
                {value: "server", label: "Server (over SSH)"},
            ]
            value: dlg._kind
            onActivated: v => dlg._kind = v
        }
        Label {
            visible: dlg._kind === "laptop"
            text: "This computer (built in)"
            opacity: 0.7
        }

        SectionLabel {
            text: "NAME"
            Layout.topMargin: Theme.s1
        }
        TextField {
            id: labelField
            Layout.fillWidth: true
            placeholderText: dlg._kind === "server" ? "e.g. NAS" : "e.g. X10 Pro"
        }

        // Drive -------------------------------------------------------------
        SectionLabel {
            text: "WHICH DRIVE  ·  mounted drives"
            visible: dlg._kind === "drive"
            Layout.topMargin: Theme.s1
        }
        ScrollComboBox {
            id: uuidCombo
            visible: dlg._kind === "drive"
            Layout.fillWidth: true
            textRole: "uuid"
            valueRole: "uuid"
            displayText: currentIndex === -1
                         ? "— pick a mounted drive —"
                         : (model[currentIndex].mountpoint
                            + "   " + model[currentIndex].uuid)
            delegate: ItemDelegate {
                width: uuidCombo.width
                contentItem: Column {
                    Label { text: modelData.mountpoint }
                    Label {
                        text: modelData.uuid
                        opacity: 0.6
                        font.family: Theme.mono
                        font.pixelSize: Theme.fsSmall
                    }
                }
            }
            onActivated: dlg._selectedUuid = currentValue || ""
        }
        Label {
            visible: dlg._kind === "drive" && uuidCombo.count === 0
            opacity: 0.6
            font.italic: true
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            text: "No new drives are mounted. Plug one in, wait a couple of"
                  + " seconds, and open this dialog again."
        }
        Label {
            visible: dlg._kind === "drive"
            opacity: 0.55
            font.pixelSize: Theme.fsSmall
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            text: "The drive is recognised by its filesystem ID, so it works"
                  + " whatever folder it gets mounted at."
        }

        // Server ------------------------------------------------------------
        SectionLabel {
            text: "LOGIN  ·  user@host"
            visible: dlg._kind === "server"
            Layout.topMargin: Theme.s1
        }
        TextField {
            id: hostField
            visible: dlg._kind === "server"
            Layout.fillWidth: true
            placeholderText: "root@192.168.1.7"
            font.family: Theme.mono
            font.pixelSize: Theme.fsMono
        }
        SectionLabel {
            text: "HOW TO REACH IT (SSH)  ·  optional"
            visible: dlg._kind === "server"
            Layout.topMargin: Theme.s1
        }
        TextField {
            id: rshField
            visible: dlg._kind === "server"
            Layout.fillWidth: true
            placeholderText: "ssh -p 2222 -i ~/.ssh/key   (empty = plain ssh)"
            font.family: Theme.mono
            font.pixelSize: Theme.fsMono
        }
        RowLayout {
            visible: dlg._kind === "server"
            spacing: Theme.s2

            Button {
                text: "Test connection"
                enabled: hostField.text.trim().length > 0
                onClicked: {
                    dlg._probeResult = "probing…"
                    dlg._probeResult = remoteProbes.probeTarget(
                        hostField.text.trim(), rshField.text.trim())
                }
            }
            Label {
                text: dlg._probeResult === "live"        ? "✓ reachable"
                    : dlg._probeResult === "unreachable" ? "✕ did not respond"
                    : dlg._probeResult
                color: dlg._probeResult === "live" ? Theme.ok
                     : dlg._probeResult === "unreachable" ? Theme.error
                     : Theme.textColor
            }
        }
    }
}
