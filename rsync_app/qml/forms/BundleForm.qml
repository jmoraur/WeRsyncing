import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Bundle name dialog. New: bundles the selected jobs / bundles (`keys`,
// "j<id>" / "b<id>") under the name. Rename: bundleId set.
Dialog {
    id: dlg
    modal: true
    anchors.centerIn: Overlay.overlay
    title: dlg.bundleId === -1 ? "New bundle" : "Rename bundle"
    standardButtons: Dialog.Save | Dialog.Cancel
    width: 460

    property int bundleId: -1
    property var keys: []
    property string initialLabel: ""

    signal done()

    onAboutToShow: {
        labelField.text = dlg.initialLabel
        labelField.forceActiveFocus()
    }

    onAccepted: {
        const label = labelField.text.trim()
        if (!label) return
        if (dlg.bundleId === -1) connections.bundle(dlg.keys, label)
        else connections.renameBundle(dlg.bundleId, label)
        dlg.done()
    }

    contentItem: ColumnLayout {
        spacing: Theme.s2

        SectionLabel { text: "BUNDLE NAME" }
        TextField {
            id: labelField
            Layout.fillWidth: true
            placeholderText: "e.g. Documents, Videos"
            onAccepted: dlg.accept()
        }

        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            opacity: 0.55
            font.pixelSize: Theme.fsSmall
            text: "A bundle only gathers jobs in the list. Its sync button " +
                  "runs every job inside it, bundles inside it included."
        }
    }
}
