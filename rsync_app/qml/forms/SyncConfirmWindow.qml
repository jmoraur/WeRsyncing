import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Sync confirmation as a separate modal window, one popup at a time.
//
// For every job under the scope: one job popup ("You are about to sync
// X"), then one popup per warning of that job. Buttons on every popup:
//   Cancel     stop asking (runs already started keep going)
//   No         skip this job
//   Dry run    run this job with --dry-run, skip its remaining warnings
//   Yes        this popup is OK; the job runs after its last popup
//   Yes to all Yes, and every later popup of the same kind is auto-OK'd
//              (kinds: "job", or the warning code such as W_DELETE_ENABLED)
// Jobs with errors (unreachable device, missing folder) never run; the
// first popup lists them. A job starts as soon as it is confirmed, so the
// runner's per-device queueing applies as before.
ThemedWindow {
    id: win
    title: "Confirm sync"
    modality: Qt.ApplicationModal
    flags: Qt.Dialog
    visible: false
    width: 900
    minimumWidth: 640
    height: Math.min(root.implicitHeight + 2 * Theme.s5,
                     Screen.desktopAvailableHeight - 120)

    // --- Inputs --------------------------------------------------------
    property string scopeKind: ""
    property int    scopeId: -1
    property int    scopeId2: 0

    // --- Sequence state -----------------------------------------------
    property var _jobs: []        // runnable jobsForScope rows
    property var _skipped: []     // [{title, reason}]
    property int _jobIdx: 0
    property var draft: ({})      // current job, per-run edits included
    property var _warnings: []    // current job's warnings (after job Yes)
    property int _warnIdx: -1     // -1 = the job popup itself
    property var _yesAll: ({})
    property var _catalog: []
    property bool _optionsOpen: false

    readonly property var _job: _jobIdx < _jobs.length ? _jobs[_jobIdx] : null
    readonly property bool _onWarning: _warnIdx >= 0
                                       && _warnIdx < _warnings.length
    readonly property var _warning: _onWarning ? _warnings[_warnIdx] : null
    readonly property var _draftIssues: _job ? connections.preflightDraft(draft)
                                             : []
    readonly property bool _draftHasErrors:
        _draftIssues.some(i => i.severity === "error")
    readonly property int _draftWarnCount:
        _draftIssues.filter(i => i.severity === "warning").length

    function set(key, value) {
        const d = Object.assign({}, draft)
        d[key] = value
        draft = d
    }

    function openFor(kind, id, id2) {
        scopeKind = kind
        scopeId = id
        scopeId2 = id2 || 0
        open()
    }

    function open() {
        _catalog = connections.optionCatalog()
        _yesAll = ({})
        const rows = connections.jobsForScope(scopeKind, scopeId, scopeId2)
        const ok = [], skipped = []
        for (const b of rows) {
            const err = (b.issues || []).find(i => i.severity === "error")
            if (err)
                skipped.push({title: b.title, reason: err.message})
            else if (!b.ready)
                skipped.push({title: b.title, reason: "a device is not available"})
            else
                ok.push(b)
        }
        _jobs = ok
        _skipped = skipped
        _jobIdx = 0
        if (ok.length === 0) {   // nothing to ask: show the skipped list
            _warnIdx = -1
            _show()
            return
        }
        _startJob()
    }

    function _show() {
        if (!visible) {
            show()
            raise()
            requestActivate()
        }
    }

    function _startJob() {
        if (_jobIdx >= _jobs.length) {
            close()
            return
        }
        // The whole saved job, so the run is exactly what the job says.
        const b = connections.getJob(_jobs[_jobIdx].id)
        const d = {
            name: "",
            from_device_id: 0, from_path: "",
            to_device_id: 0, to_path: "",
            path_mode: "contents", chown_mode: "source",
            chown_value: "", chmod_value: "", excludes: "",
        }
        for (const o of _catalog) d[o.key] = o.default ? 1 : 0
        for (const k in d) {
            if (b[k] !== undefined && b[k] !== null) d[k] = b[k]
        }
        draft = d
        excludesArea.text = d.excludes || ""
        ownershipEditor.load()
        _optionsOpen = false
        _warnIdx = -1
        _warnings = []
        if (_yesAll["job"] && !_draftHasErrors) _afterJobYes()
        else _show()
    }

    function _afterJobYes() {
        _warnings = _draftIssues.filter(i => i.severity === "warning")
        _warnIdx = 0
        _nextWarning()
    }

    function _nextWarning() {
        while (_warnIdx < _warnings.length && _yesAll[_warnings[_warnIdx].code])
            _warnIdx++
        if (_warnIdx >= _warnings.length) {
            _enqueue(false)
            _nextJob()
        } else {
            _show()
        }
    }

    function _nextJob() {
        _jobIdx++
        _startJob()
    }

    function _enqueue(dry) {
        const d = Object.assign({}, draft)
        if (dry) d.opt_dry_run = 1
        const argv = connections.previewCommand(d)
        if (argv.length === 0) return
        connections.runSync([{
            argv: argv,
            label: _job.title + (dry && !draft.opt_dry_run ? "  (dry run)" : ""),
            destDeviceId: d.to_device_id,
            jobId: _job.id,
        }])
    }

    // --- Buttons -------------------------------------------------------
    function yes() {
        if (_warnIdx < 0) _afterJobYes()
        else { _warnIdx++; _nextWarning() }
    }
    function yesToAll() {
        const y = Object.assign({}, _yesAll)
        y[_onWarning ? _warning.code : "job"] = true
        _yesAll = y
        yes()
    }
    function no() { _nextJob() }
    function dryRun() { _enqueue(true); _nextJob() }

    // --- Content -------------------------------------------------------
    ColumnLayout {
        id: root
        anchors.fill: parent
        anchors.margins: Theme.s5
        spacing: Theme.s3

        // Where we are in the sequence.
        Label {
            visible: win._jobs.length > 1 || win._onWarning
            text: {
                const parts = []
                if (win._jobs.length > 1)
                    parts.push("Job " + (win._jobIdx + 1) + " of "
                               + win._jobs.length)
                if (win._onWarning)
                    parts.push("warning " + (win._warnIdx + 1) + " of "
                               + win._warnings.length)
                return parts.join("  ·  ")
            }
            opacity: 0.6
            font.pixelSize: Theme.fsSmall
        }

        // --- Nothing can run ---------------------------------------------
        Label {
            visible: win._jobs.length === 0
            text: "Nothing can run here right now."
            font.pixelSize: Theme.fsHeading
            font.weight: Font.DemiBold
        }

        // --- Job popup / warning popup headline ---------------------------
        Label {
            visible: win._job !== null
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            textFormat: Text.StyledText
            font.pixelSize: Theme.fsHeading
            color: win._onWarning ? Theme.warn : Theme.textColor
            text: !win._job ? ""
                  : win._onWarning
                  ? "⚠  " + win._warning.message
                  : "You are about to sync <b>" + win._job.title + "</b>."
        }
        Label {
            visible: win._onWarning
            Layout.fillWidth: true
            text: "Job: " + (win._job ? win._job.title : "")
                  + ".  Are you sure?"
        }

        GridLayout {
            visible: win._job !== null
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Theme.s3
            rowSpacing: 2

            Label { text: "From"; opacity: 0.6 }
            Label {
                text: win._job ? win._job.fromDisplay : ""
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }
            Label { text: "To"; opacity: 0.6 }
            Label {
                text: win._job ? win._job.toDisplay : ""
                font.family: Theme.mono
                font.pixelSize: Theme.fsMono
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }
        }

        // Jobs that won't run — told once, on the first popup.
        ColumnLayout {
            visible: win._skipped.length > 0 && win._jobIdx === 0
                     && !win._onWarning
            Layout.fillWidth: true
            spacing: 2
            Label {
                text: "Won't run:"
                color: Theme.error
                font.weight: Font.DemiBold
            }
            Repeater {
                model: win._skipped
                Label {
                    required property var modelData
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: "✕  " + modelData.title + " — " + modelData.reason
                    color: Theme.error
                    font.pixelSize: Theme.fsSmall
                }
            }
        }

        // Job popup: errors from per-run edits, warnings still to come.
        IssueList {
            visible: win._job !== null && !win._onWarning
            Layout.fillWidth: true
            ackable: false
            issues: win._draftIssues.filter(i => i.severity === "error")
        }
        Label {
            visible: win._job !== null && !win._onWarning
                     && win._draftWarnCount > 0
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: (win._draftWarnCount === 1
                   ? "1 warning to confirm next:"
                   : win._draftWarnCount + " warnings to confirm next:")
                  + win._draftIssues.filter(i => i.severity === "warning")
                        .map(i => "\n⚠  " + i.message).join("")
            color: Theme.warn
            font.pixelSize: Theme.fsSmall
        }

        CommandPreview {
            visible: win._job !== null
            Layout.fillWidth: true
            argv: win._job ? connections.previewCommand(win.draft) : []
            placeholder: "(command unavailable — source or destination missing)"
        }

        // --- Per-run options (job popup only) ----------------------------
        Label {
            visible: win._job !== null && !win._onWarning
            text: (win._optionsOpen ? "▾" : "▸")
                  + "  Change options for this run"
            opacity: optHover.hovered ? 1.0 : 0.75
            HoverHandler { id: optHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: win._optionsOpen = !win._optionsOpen }
        }

        ScrollView {
            id: optionsScroll
            visible: win._job !== null && !win._onWarning && win._optionsOpen
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredHeight: optionsCol.implicitHeight
            clip: true

            ColumnLayout {
                id: optionsCol
                width: optionsScroll.availableWidth - 16
                spacing: Theme.s3

                Label {
                    text: "Changes here apply to this run only."
                    opacity: 0.55
                    font.pixelSize: Theme.fsSmall
                }

                RowLayout {
                    spacing: Theme.s2
                    Label { text: "Path mode"; opacity: 0.75 }
                    SegmentedControl {
                        model: [
                            {value: "contents", label: "contents/"},
                            {value: "folder", label: "folder"},
                        ]
                        value: win.draft.path_mode || "contents"
                        onActivated: v => win.set("path_mode", v)
                    }
                }

                OwnershipEditor {
                    id: ownershipEditor
                    Layout.fillWidth: true
                    values: win.draft
                    onFieldChanged: (key, v) => win.set(key, v)
                }

                OptionsEditor {
                    Layout.fillWidth: true
                    catalog: win._catalog
                    values: win.draft
                    onOptionToggled: (key, v) => win.set(key, v)
                }

                SectionLabel { text: "SKIP LIST  ·  one per line" }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 60

                    TextArea {
                        id: excludesArea
                        placeholderText: ".cache/\n*.tmp"
                        onTextChanged: win.set("excludes", text)
                        font.family: Theme.mono
                        font.pixelSize: Theme.fsMono
                        wrapMode: TextEdit.NoWrap
                    }
                }
            }
        }

        Item {
            visible: !optionsScroll.visible
            Layout.fillHeight: true
        }

        // --- Buttons -----------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            Button {
                text: win._jobs.length === 0 ? "Close" : "Cancel"
                onClicked: win.close()
                ToolTip.visible: hovered && win._jobs.length > 0
                ToolTip.delay: 500
                ToolTip.text: "Stop asking. Runs already started keep going."
            }
            Item { Layout.fillWidth: true }
            Button {
                visible: win._job !== null
                text: "No"
                onClicked: win.no()
                ToolTip.visible: hovered
                ToolTip.delay: 500
                ToolTip.text: "Skip this job"
            }
            Button {
                visible: win._job !== null && !win.draft.opt_dry_run
                enabled: !win._draftHasErrors
                text: "Dry run"
                onClicked: win.dryRun()
                ToolTip.visible: hovered
                ToolTip.delay: 500
                ToolTip.text: "Run this job as a trial (--dry-run): it shows"
                              + " what would change and changes nothing"
            }
            Button {
                visible: win._job !== null
                enabled: !win._draftHasErrors
                text: "Yes"
                highlighted: true
                onClicked: win.yes()
            }
            Button {
                visible: win._job !== null
                enabled: !win._draftHasErrors
                text: "Yes to all"
                onClicked: win.yesToAll()
                ToolTip.visible: hovered
                ToolTip.delay: 500
                ToolTip.text: win._onWarning
                              ? "Yes, and also to this same warning on"
                                + " every later job"
                              : "Yes, and skip this question for every"
                                + " later job (warnings are still asked)"
            }
        }
    }
}
