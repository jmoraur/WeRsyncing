import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Monospace command box with a copy button. Shows the literal rsync
// argv — surfacing the exact command is a core principle of this app.
Rectangle {
    id: box

    property var argv: []
    // Already-quoted command; wins over argv when set.
    property string text: ""
    property string placeholder: "(pick a source and a device to preview the command)"
    readonly property bool hasCommand: text !== "" || (argv && argv.length > 0)

    implicitHeight: cmdEdit.implicitHeight + 2 * Theme.s2
    radius: Theme.radius
    color: Theme.dark ? Qt.rgba(0, 0, 0, 0.25) : Qt.rgba(0, 0, 0, 0.05)
    border.width: 1
    border.color: Theme.border

    TextEdit {
        id: cmdEdit
        anchors.fill: parent
        anchors.margins: Theme.s2
        anchors.rightMargin: 34
        readOnly: true
        selectByMouse: true
        wrapMode: TextEdit.Wrap
        font.family: Theme.mono
        font.pixelSize: Theme.fsMono
        color: box.hasCommand ? Theme.textColor
                              : Theme.tint(Theme.textColor, 0.5)
        text: box.text !== "" ? box.text
              : box.hasCommand ? Sh.quote(box.argv) : box.placeholder
    }

    RowActionButton {
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: 2
        visible: box.hasCommand
        icon.name: "edit-copy"
        tip: "Copy command"
        onClicked: {
            cmdEdit.selectAll()
            cmdEdit.copy()
            cmdEdit.deselect()
        }
    }
}
