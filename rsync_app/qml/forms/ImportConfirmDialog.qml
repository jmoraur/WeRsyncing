import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Confirmation for running an import job. Shows the three folders and
// says plainly what a real run does — duplicates of archived files are
// DELETED from the dump, with no trash bin. Warnings must each be
// acknowledged before a real run; a dry run changes nothing and needs no
// acknowledgements (it's the recommended first step). "Find duplicates in
// archive" starts the report-only scan of the archive itself.
Dialog {
    id: dlg
    modal: true
    title: "Import from dump folder"
    standardButtons: Dialog.NoButton
    width: 620

    property int jobId: -1
    property var job: ({})
    property var issues: []
    property var _acks: ({})

    readonly property bool hasErrors:
        issues.some(i => i.severity === "error")
    readonly property bool allAcked:
        issues.every((i, idx) => i.severity !== "warning" || !!_acks[idx])

    function openFor(id) {
        dlg.jobId = id
        dlg.open()
    }

    onAboutToShow: {
        dlg.job = connections.getImportJob(dlg.jobId)
        dlg.issues = connections.preflightImportDraft(dlg.job)
        dlg._acks = ({})
    }

    contentItem: ColumnLayout {
        spacing: Theme.s2

        Label {
            text: dlg.job.label || ""
            font.weight: Font.DemiBold
            font.pixelSize: Theme.fsHeading
        }

        GridLayout {
            columns: 2
            columnSpacing: Theme.s3
            rowSpacing: 2
            Layout.fillWidth: true

            Label { text: "Dump"; opacity: 0.6 }
            Label {
                text: dlg.job.dump_path || ""
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }
            Label { text: "Destination"; opacity: 0.6 }
            Label {
                text: dlg.job.dest_path || ""
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }
            Label { text: "Archive"; opacity: 0.6 }
            Label {
                text: dlg.job.archive_root || ""
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.topMargin: Theme.s1
            wrapMode: Text.Wrap
            text: "New files are moved to the destination. Files that are"
                  + " already in the archive (or already at the destination)"
                  + " are deleted from the dump folder — there is no trash"
                  + " bin. Files that aren't photos or videos stay where"
                  + " they are."
        }

        IssueList {
            Layout.fillWidth: true
            visible: dlg.issues.length > 0
            issues: dlg.issues
            ackable: true
            acks: dlg._acks
            onAckToggled: (index, checked) => {
                const copy = Object.assign({}, dlg._acks)
                copy[index] = checked
                dlg._acks = copy
            }
        }

        // --- Footer --------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: Theme.s1
            spacing: Theme.s1

            Button {
                text: "Find duplicates in archive"
                enabled: !dlg.hasErrors
                onClicked: {
                    connections.runArchiveDupReport(dlg.jobId)
                    dlg.accept()
                }
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Cancel"
                onClicked: dlg.reject()
            }
            Button {
                text: "Dry run"
                enabled: !dlg.hasErrors
                onClicked: {
                    connections.runImport(dlg.jobId, true)
                    dlg.accept()
                }
            }
            Button {
                text: "Run import"
                highlighted: true
                enabled: !dlg.hasErrors && dlg.allAcked
                onClicked: {
                    connections.runImport(dlg.jobId, false)
                    dlg.accept()
                }
            }
        }
    }
}
