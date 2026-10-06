import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Activity pane (right side of the page) for the SyncRunner.
//
// Top: one line per run, filtered by the All / Running / Failed / Done
// tabs. Bottom: the selected run's command and its log, which fills all
// remaining height. A run that starts while nothing running is selected
// takes the selection, so the log follows the work.
//
// The ListModel keeps per-run fields stable across `jobsChanged`
// reconciliation; only state/exit are mutated in place. Model field names
// avoid `state` (collides with Item.state — lessons).
Item {
    id: panel

    // Persisted via QSettings outputFilter alias in ConnectionsPage.
    // Values: "all" | "running" | "failed" | "done".
    property string _filter: "all"
    property int selectedJobId: -1
    // Bumped on every model change so bindings reading jobModel.get()
    // re-evaluate (get() alone isn't tracked).
    property int _rev: 0

    property int nRunning: 0
    property int nPending: 0
    property int nDone: 0
    property int nFailed: 0
    property int nCancelled: 0
    readonly property int nActive: nRunning + nPending

    ListModel { id: jobModel }

    function _indexOf(jobId) {
        for (let i = 0; i < jobModel.count; i++)
            if (jobModel.get(i).jobId === jobId) return i
        return -1
    }

    function _refresh() {
        const snap = syncRunner.jobs()

        const liveIds = {}
        for (let i = 0; i < snap.length; i++) liveIds[snap[i].id] = true
        for (let i = jobModel.count - 1; i >= 0; i--) {
            if (!liveIds[jobModel.get(i).jobId]) jobModel.remove(i)
        }

        const counts = {running: 0, pending: 0, done: 0,
                        failed: 0, cancelled: 0}
        for (let i = 0; i < snap.length; i++) {
            const j = snap[i]
            counts[j.state] = (counts[j.state] || 0) + 1
            const idx = _indexOf(j.id)
            if (idx >= 0) {
                jobModel.setProperty(idx, "jobState", j.state)
                jobModel.setProperty(idx, "exitCode", j.exitCode)
            } else {
                jobModel.append({
                    jobId: j.id,
                    label: j.label,
                    argvText: Sh.quote(j.argv),
                    jobState: j.state,
                    exitCode: j.exitCode,
                })
            }
        }
        nRunning = counts.running
        nPending = counts.pending
        nDone = counts.done
        nFailed = counts.failed
        nCancelled = counts.cancelled
        _rev++

        if (_indexOf(selectedJobId) < 0)
            _select(jobModel.count > 0 ? jobModel.get(jobModel.count - 1).jobId
                                       : -1)
    }

    function _select(jobId) {
        if (jobId === selectedJobId) return
        selectedJobId = jobId
        logArea.text = jobId >= 0 ? syncRunner.jobLog(jobId) : ""
        logArea.cursorPosition = logArea.length
    }

    readonly property var _selected: {
        _rev   // dependency: re-evaluate on every refresh
        const i = _indexOf(selectedJobId)
        return i >= 0 ? jobModel.get(i) : null
    }

    function _shown(jobState) {
        if (_filter === "all") return true
        if (_filter === "running")
            return jobState === "running" || jobState === "pending"
        return jobState === _filter
    }

    function _stateColor(s) {
        return s === "done" ? Theme.ok
             : s === "failed" ? Theme.error
             : s === "running" ? Theme.running
             : Theme.idle   // pending / cancelled
    }

    function _stateGlyph(s) {
        return s === "done" ? "✓"
             : s === "failed" ? "✕"
             : s === "running" ? "●"
             : s === "cancelled" ? "–"
             : "○"   // pending
    }

    Connections {
        target: syncRunner
        function onJobsChanged() { panel._refresh() }
        function onJobStateChanged(jobId, state) {
            panel._refresh()
            // Follow the work: a starting run takes the selection unless
            // the selected one is still running.
            const sel = panel._selected
            if (state === "running" && (!sel || sel.jobState !== "running"))
                panel._select(jobId)
        }
        function onJobOutputAppended(id, t) {
            if (id !== panel.selectedJobId) return
            // Sample BEFORE insert: follow the tail only if the user was
            // already at the bottom. insert (not append) avoids a stray
            // newline.
            const sb = logScroll.ScrollBar.vertical
            const wasAtBottom = (sb.size + sb.position) >= 0.999
            logArea.insert(logArea.length, t)
            if (wasAtBottom) logArea.cursorPosition = logArea.length
        }
    }

    Component.onCompleted: _refresh()

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.s2

        // --- Header ---------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: Theme.rowHeader
            spacing: Theme.s2

            Label {
                text: "Activity"
                font.weight: Font.DemiBold
                Layout.leftMargin: Theme.s1
            }
            Item { Layout.fillWidth: true }
            ToolButton {
                text: "Cancel all"
                enabled: panel.nActive > 0
                onClicked: syncRunner.cancelAll()
            }
            ToolButton {
                text: "Clear finished"
                enabled: jobModel.count > panel.nActive
                onClicked: syncRunner.clearFinished()
            }
        }

        TabBar {
            id: tabs
            objectName: "activityTabs"
            Layout.fillWidth: true
            readonly property var values: ["all", "running", "failed", "done"]
            currentIndex: Math.max(0, values.indexOf(panel._filter))
            onCurrentIndexChanged: panel._filter = values[currentIndex]

            TabButton { text: "All  " + jobModel.count }
            TabButton { text: "Running  " + panel.nActive }
            TabButton { text: "Failed  " + panel.nFailed }
            TabButton { text: "Done  " + panel.nDone }
        }

        // --- Run list -------------------------------------------------
        ListView {
            id: runList
            Layout.fillWidth: true
            // Up to ~6 lines, then it scrolls.
            Layout.preferredHeight: Math.min(contentHeight,
                                             6 * Theme.rowConnection)
            clip: true
            model: jobModel
            ScrollBar.vertical: ScrollBar {}

            delegate: Rectangle {
                id: runRow
                required property int jobId
                required property string label
                required property string jobState
                required property int exitCode

                width: runList.width
                height: visible ? Theme.rowConnection : 0
                visible: panel._shown(jobState)
                radius: Theme.radius
                color: jobId === panel.selectedJobId
                       ? Theme.tint(Theme.accent, 0.18)
                       : runHover.hovered ? Theme.hoverBg : "transparent"

                HoverHandler { id: runHover }
                TapHandler { onTapped: panel._select(runRow.jobId) }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s1
                    spacing: Theme.s2

                    Label {
                        text: panel._stateGlyph(runRow.jobState)
                        color: panel._stateColor(runRow.jobState)
                        font.bold: true
                        Layout.preferredWidth: 14
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Label {
                        text: runRow.label
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Label {
                        text: runRow.jobState === "done"
                              || runRow.jobState === "failed"
                              ? "exit " + runRow.exitCode : runRow.jobState
                        opacity: 0.55
                        font.pixelSize: Theme.fsSmall
                    }
                    RowActionButton {
                        visible: runRow.jobState === "running"
                                 || runRow.jobState === "pending"
                        icon.name: "process-stop"
                        tip: "Cancel this run"
                        onClicked: syncRunner.cancel(runRow.jobId)
                    }
                }
            }
        }

        Label {
            visible: jobModel.count === 0
            Layout.fillWidth: true
            Layout.topMargin: Theme.s4
            horizontalAlignment: Text.AlignHCenter
            text: "Run a sync to see its output here."
            opacity: 0.5
        }

        // --- Selected run ---------------------------------------------
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: Theme.border
            visible: panel._selected !== null
        }

        RowLayout {
            visible: panel._selected !== null
            Layout.fillWidth: true
            Label {
                text: panel._selected ? panel._selected.label : ""
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Label {
                text: "↓ following"
                opacity: 0.5
                font.pixelSize: Theme.fsSmall
                visible: (logScroll.ScrollBar.vertical.size
                          + logScroll.ScrollBar.vertical.position) >= 0.999
            }
        }

        // Import jobs run in-process and have no command line.
        CommandPreview {
            visible: panel._selected !== null && panel._selected.argvText !== ""
            Layout.fillWidth: true
            text: panel._selected ? panel._selected.argvText : ""
        }

        // The log grows with the pane.
        Rectangle {
            visible: panel._selected !== null
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.dark ? Qt.rgba(0, 0, 0, 0.25) : Qt.rgba(0, 0, 0, 0.04)
            border.color: Theme.border
            border.width: 1
            radius: Theme.radius

            ScrollView {
                id: logScroll
                anchors.fill: parent
                anchors.margins: 3
                clip: true

                TextArea {
                    id: logArea
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.NoWrap
                    font.family: Theme.mono
                    font.pixelSize: Theme.fsSmall
                    background: null
                }
            }
        }

        // Keeps the list at the top when nothing is selected.
        Item {
            visible: panel._selected === null
            Layout.fillHeight: true
        }
    }
}
