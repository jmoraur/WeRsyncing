import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Group dialog. A group is only a label that groups jobs. Reachable
// from the group row's edit button and from inline-create in JobForm.
Dialog {
    id: dlg
    modal: true
    anchors.centerIn: Overlay.overlay
    title: dlg.groupId === -1 ? "New group" : "Edit group"
    standardButtons: Dialog.Save | Dialog.Cancel
    width: 460

    property int groupId: -1
    property string initialLabel: ""

    signal acceptedWithId(int newId)

    onAboutToShow: {
        labelField.text = dlg.initialLabel
        labelField.forceActiveFocus()
    }

    onAccepted: {
        const label = labelField.text.trim()
        if (!label) return
        let newId = dlg.groupId
        if (dlg.groupId === -1) {
            newId = connections.addGroup(label)
        } else {
            connections.updateGroup(dlg.groupId, label)
        }
        dlg.acceptedWithId(newId)
    }

    contentItem: ColumnLayout {
        spacing: Theme.s2

        SectionLabel { text: "GROUP NAME" }
        TextField {
            id: labelField
            Layout.fillWidth: true
            placeholderText: "e.g. NAS, X10 Pro"
        }

        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            opacity: 0.55
            font.pixelSize: Theme.fsSmall
            text: "A group only groups jobs in the list. " +
                  "\"Sync all\" on a group runs every job in it."
        }
    }
}
