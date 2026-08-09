import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Qt.labs.platform as Labs
import "../components"

// Import-job dialog: a label, a mode, plus three folders — the source/dump
// folder (where phone media comes from), the destination folder (where new
// files land), and the archive root (what "already have it" is checked
// against). In "Move & clean" the source is emptied (dups deleted, new
// files moved out); in "Copy only" the source is never touched — made for
// reading a mounted phone directly. The destination is meant to be
// repointed over time (new year / event folder); the rest rarely changes.
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
    property int initialCopyMode: 0

    property bool copyMode: false

    // Live preflight of the current field values (display-only).
    property var issues: []

    function _draft() {
        return {
            label: labelField.text.trim(),
            dump_path: dumpField.text.trim(),
            dest_path: destField.text.trim(),
            archive_root: archiveField.text.trim(),
            copy_mode: dlg.copyMode ? 1 : 0,
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
        dlg.copyMode = dlg.initialCopyMode === 1
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
            text: "MODE"
            Layout.topMargin: Theme.s1
        }
        SegmentedControl {
            model: [
                { value: "move", label: "Move & clean" },
                { value: "copy", label: "Copy only" },
            ]
            value: dlg.copyMode ? "copy" : "move"
            onActivated: newValue => {
                dlg.copyMode = (newValue === "copy")
                dlg._refreshIssues()
            }
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            opacity: 0.65
            font.pixelSize: Theme.fsSmall
            text: dlg.copyMode
                  ? "New files are copied out; nothing in the source folder"
                    + " is ever changed or deleted. Made for importing"
                    + " straight off a mounted phone."
                  : "Duplicates are deleted from the dump folder and new"
                    + " files are moved out of it, leaving it empty."
        }

        SectionLabel {
            text: dlg.copyMode
                  ? "SOURCE  ·  scanned, never changed"
                  : "DUMP FOLDER  ·  where you unload the phone"
            Layout.topMargin: Theme.s1
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s1
            TextField {
                id: dumpField
                Layout.fillWidth: true
                placeholderText: dlg.copyMode
                                 ? "/home/me/Phone/Internal storage/DCIM/…"
                                 : "/home/me/Pictures/Phone-Camera"
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
            text: dlg.copyMode
                  ? "DESTINATION  ·  where new files are copied to"
                  : "DESTINATION  ·  where new files are moved to"
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
