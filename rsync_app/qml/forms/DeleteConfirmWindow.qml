import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Delete confirmation as a modal pop-up window: "You are about to delete
// X. Are you sure?" Says what goes with it (a device's jobs).
ThemedWindow {
    id: win
    title: "Confirm delete"
    modality: Qt.ApplicationModal
    flags: Qt.Dialog
    visible: false
    width: 520
    height: col.implicitHeight + 2 * Theme.s5

    property string kind: ""     // device | job | importJob
    property int    itemId: -1
    property string label: ""
    property int    _jobs: 0

    signal confirmed(string kind, int itemId)

    function open() {
        _jobs = connections.jobCountFor(kind, itemId)
        show()
        raise()
        requestActivate()
    }

    readonly property string _what: kind === "device" ? "the device"
                                  : kind === "importJob" ? "the import"
                                  : "the job"

    ColumnLayout {
        id: col
        anchors.fill: parent
        anchors.margins: Theme.s5
        spacing: Theme.s3

        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            textFormat: Text.StyledText
            font.pixelSize: Theme.fsHeading
            text: "You are about to delete " + win._what + " <b>"
                  + win.label + "</b>. Are you sure?"
        }
        Label {
            visible: win._jobs > 0
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.error
            text: (win._jobs === 1 ? "1 job goes" : win._jobs + " jobs go")
                  + " with it."
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            opacity: 0.6
            text: win.kind === "importJob"
                  ? "Only the import setup is removed; no files are touched."
                  : "Only the setup is removed; no files are touched."
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button {
                text: "No"
                onClicked: win.close()
            }
            Button {
                text: "Yes, delete"
                highlighted: true
                onClicked: {
                    win.confirmed(win.kind, win.itemId)
                    win.close()
                }
            }
        }
    }
}
