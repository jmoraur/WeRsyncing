import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Qt.labs.platform as Labs
import "../components"

// Import-job dialog: a label plus three folders — the dump folder (where
// phone media gets unloaded), the destination folder (where new files are
// moved to), and the archive root (what "already have it" is checked
// against). The destination is meant to be repointed over time (new year /
// event folder); the rest rarely changes.
//
// Modes: importJobId === -1 → add, else → edit.
Dialog {
    id: dlg
    modal: true
    title: dlg.importJobId === -1 ? "New import" : "Edit import"
    standardButtons: Dialog.Save | Dialog.Cancel
    width: 560

    property int importJobId: -1
    property string initialLabel: ""
    property string initialDump: ""
    property string initialDest: ""
    property string initialArchive: ""

    // Live preflight of the current field values (display-only).
    property var issues: []

    function _draft() {
        return {
            label: labelField.text.trim(),
            dump_path: dumpField.text.trim(),
            dest_path: destField.text.trim(),
            archive_root: archiveField.text.trim(),
        }
    }

    function _refreshIssues() {
        dlg.issues = connections.preflightImportDraft(dlg._draft())
    }

    onAboutToShow: {
        labelField.text = dlg.initialLabel
        dumpField.text = dlg.initialDump
        destField.text = dlg.initialDest
        archiveField.text = dlg.initialArchive
        dlg._refreshIssues()
        labelField.forceActiveFocus()
    }

    onAccepted: {
        const draft = dlg._draft()
        if (!draft.label) return
        if (dlg.importJobId === -1) {
            connections.addImportJob(draft)
        } else {
            connections.updateImportJob(dlg.importJobId, draft)
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.s2

        SectionLabel { text: "NAME" }
        TextField {
            id: labelField
            Layout.fillWidth: true
            placeholderText: "e.g. Phone photos"
        }

        SectionLabel {
            text: "DUMP FOLDER  ·  where you unload the phone"
            Layout.topMargin: Theme.s1
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s1
            TextField {
                id: dumpField
                Layout.fillWidth: true
                placeholderText: "/home/me/Pictures/Phone-Camera"
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                onTextChanged: dlg._refreshIssues()
            }
            RowActionButton {
                icon.name: "folder-open"
                tip: "Browse…"
                onClicked: { folderDialog.target = dumpField; folderDialog.open() }
            }
        }

        SectionLabel {
            text: "DESTINATION  ·  where new files are moved to"
            Layout.topMargin: Theme.s1
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s1
            TextField {
                id: destField
                Layout.fillWidth: true
                placeholderText: "/home/me/Videos/Phone-Media/2026/…"
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                onTextChanged: dlg._refreshIssues()
            }
            RowActionButton {
                icon.name: "folder-open"
                tip: "Browse…"
                onClicked: { folderDialog.target = destField; folderDialog.open() }
            }
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            opacity: 0.65
            font.pixelSize: Theme.fsSmall
            text: "Change this when a new event or year folder starts —"
                  + " everything else stays the same."
        }

        SectionLabel {
            text: "ARCHIVE  ·  what counts as “already have it”"
            Layout.topMargin: Theme.s1
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s1
            TextField {
                id: archiveField
                Layout.fillWidth: true
                placeholderText: "/home/me/Videos/Phone-Media"
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                onTextChanged: dlg._refreshIssues()
            }
            RowActionButton {
                icon.name: "folder-open"
                tip: "Browse…"
                onClicked: { folderDialog.target = archiveField; folderDialog.open() }
            }
        }

        IssueList {
            Layout.fillWidth: true
            Layout.topMargin: Theme.s1
            visible: dlg.issues.length > 0
            issues: dlg.issues
            ackable: false
        }
    }

    Labs.FolderDialog {
        id: folderDialog
        property var target: null
        title: "Choose folder"
        currentFolder: (target && target.text)
                       ? "file://" + target.text
                       : Labs.StandardPaths.writableLocation(Labs.StandardPaths.HomeLocation)
        onAccepted: {
            if (!target) return
            const url = folder.toString()
            target.text = url.startsWith("file://") ? url.slice(7) : url
        }
    }
}
