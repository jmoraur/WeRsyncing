import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Bottom-of-page output panel for the SyncRunner.
//
// The Repeater uses a ListModel (not a JS array) so existing delegates
// stay alive across `jobsChanged` reconciliation — that keeps each
// delegate's log buffer + per-job collapse state intact while only
// state/exit fields are mutated in place.
//
// Per-delegate `Connections` filtered by `jobId` stream incremental
// output into the right log area. The runner is the source of truth;
// the panel is a thin viewer.
//
// Model field names avoid `state` (collides with Item.state — lessons).
// State counts are plain int properties recomputed in _refresh() so the
// filter labels stay reactive on state transitions, which ListModel.get()
// inside a binding would not guarantee.
Item {
    id: panel

    // Bound by ConnectionsPage to drive SplitView sizing.
    property bool collapsed: false
    // Persisted via QSettings outputFilter alias in ConnectionsPage.
    // Values: "all" | "running" | "failed" | "done".
    property string _filter: "all"
    readonly property int headerHeight: 38
    readonly property int jobCount: jobModel.count

    property int nRunning: 0
    property int nPending: 0
    property int nDone: 0
    property int nFailed: 0
    property int nCancelled: 0
    readonly property int nActive: nRunning + nPending

    ListModel { id: jobModel }

    function _refresh() {
        const snap = syncRunner.jobs()

        const liveIds = {}
        for (let i = 0; i < snap.length; i++) liveIds[snap[i].id] = true
        for (let i = jobModel.count - 1; i >= 0; i--) {
            if (!liveIds[jobModel.get(i).jobId]) jobModel.remove(i)
        }

        const existing = {}
        for (let i = 0; i < jobModel.count; i++) {
            existing[jobModel.get(i).jobId] = i
        }
        const counts = {running: 0, pending: 0, done: 0,
                        failed: 0, cancelled: 0}
        for (let i = 0; i < snap.length; i++) {
            const j = snap[i]
            counts[j.state] = (counts[j.state] || 0) + 1
            if (j.id in existing) {
                const idx = existing[j.id]
                jobModel.setProperty(idx, "jobState", j.state)
                jobModel.setProperty(idx, "exitCode", j.exitCode)
            } else {
                jobModel.append({
                    jobId: j.id,
                    label: j.label,
                    argvText: Sh.quote(j.argv),
                    destDeviceId: j.destDeviceId,
                    jobState: j.state,
                    exitCode: j.exitCode,
                    jobCollapsed: false,
                })
            }
        }
        nRunning = counts.running
        nPending = counts.pending
        nDone = counts.done
        nFailed = counts.failed
        nCancelled = counts.cancelled
    }

    function _matchesFilter(jobState) {
        if (panel._filter === "all") return true
        return jobState === panel._filter
    }

    function _visibleCount() {
        let n = 0
        for (let i = 0; i < jobModel.count; i++) {
            if (_matchesFilter(jobModel.get(i).jobState)) n++
        }
        return n
    }

    function _stateColor(s) {
        return s === "done" ? Theme.ok
             : s === "failed" ? Theme.error
             : s === "running" ? Theme.running
             : Theme.idle   // pending / cancelled
    }

    Connections {
        target: syncRunner
        function onJobsChanged() { panel._refresh() }
        function onJobStateChanged(jobId, state) { panel._refresh() }
    }

    Component.onCompleted: _refresh()

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // --- Header bar (always visible) -----------------------------
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: panel.headerHeight
            color: Theme.surfaceAlt
            radius: Theme.radius

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s1
                anchors.rightMargin: Theme.s2
                spacing: Theme.s2

                RowActionButton {
                    icon.name: panel.collapsed ? "arrow-right" : "arrow-down"
                    tip: panel.collapsed ? "Expand output" : "Collapse output"
                    onClicked: panel.collapsed = !panel.collapsed
                }

                Label {
                    text: "Sync output"
                    font.weight: Font.DemiBold
                }

                Label {
                    opacity: 0.6
                    font.pixelSize: Theme.fsSmall
                    text: {
                        if (jobModel.count === 0) return "no jobs yet"
                        const parts = []
                        if (panel.nRunning) parts.push(panel.nRunning + " running")
                        if (panel.nPending) parts.push(panel.nPending + " queued")
                        if (panel.nDone) parts.push(panel.nDone + " done")
                        if (panel.nFailed) parts.push(panel.nFailed + " failed")
                        if (panel.nCancelled) parts.push(panel.nCancelled + " cancelled")
                        return parts.join("  ·  ")
                    }
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
        }

        // --- Filter row (above body) ----------------------------------
        RowLayout {
            visible: !panel.collapsed
            Layout.fillWidth: true
            Layout.topMargin: Theme.s2
            Layout.bottomMargin: Theme.s1
            spacing: Theme.s2

            SegmentedControl {
                model: [
                    {value: "all", label: "All " + jobModel.count},
                    {value: "running", label: "Running " + panel.nRunning},
                    {value: "failed", label: "Failed " + panel.nFailed},
                    {value: "done", label: "Done " + panel.nDone},
                ]
                value: panel._filter
                onActivated: v => panel._filter = v
            }
            Item { Layout.fillWidth: true }
        }

        // --- Body (jobs list) ----------------------------------------
        ScrollView {
            visible: !panel.collapsed
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: panel.width
                spacing: Theme.s2

                Label {
                    visible: jobModel.count === 0
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: Theme.s4
                    text: "Run a sync to see output here."
                    opacity: 0.5
                }

                Label {
                    visible: jobModel.count > 0 && panel._visibleCount() === 0
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: Theme.s4
                    text: "No jobs match the current filter."
                    opacity: 0.5
                }

                Repeater {
                    model: jobModel

                    delegate: Rectangle {
                        id: jobCard
                        // ListModel auto-injects these as delegate-scope
                        // required properties.
                        required property int index
                        required property int jobId
                        required property string label
                        required property string argvText
                        required property int destDeviceId
                        required property string jobState
                        required property int exitCode
                        required property bool jobCollapsed

                        Layout.fillWidth: true
                        Layout.rightMargin: Theme.s2
                        visible: panel._matchesFilter(jobState)
                        // Invisible cards must not reserve layout cells.
                        Layout.preferredHeight: visible ? implicitHeight : 0
                        Layout.maximumHeight: visible ? -1 : 0
                        implicitHeight: cardColumn.implicitHeight + 2 * Theme.s2
                        radius: Theme.radius
                        color: Theme.surface
                        border.width: 1
                        border.color: Theme.border

                        ColumnLayout {
                            id: cardColumn
                            anchors.fill: parent
                            anchors.margins: Theme.s2
                            spacing: Theme.s1

                            // Header: collapse + state badge + label
                            // + exit + cancel.
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                RowActionButton {
                                    implicitWidth: 24
                                    implicitHeight: 24
                                    icon.name: jobCard.jobCollapsed
                                               ? "arrow-right" : "arrow-down"
                                    tip: jobCard.jobCollapsed
                                         ? "Expand job" : "Collapse job"
                                    onClicked: jobModel.setProperty(
                                        jobCard.index,
                                        "jobCollapsed",
                                        !jobCard.jobCollapsed)
                                }

                                Rectangle {
                                    implicitWidth: badgeLabel.implicitWidth + 14
                                    implicitHeight: 18
                                    radius: 9
                                    color: Theme.tint(
                                        panel._stateColor(jobCard.jobState), 0.18)

                                    Label {
                                        id: badgeLabel
                                        anchors.centerIn: parent
                                        text: jobCard.jobState
                                        color: panel._stateColor(jobCard.jobState)
                                        font.weight: Font.DemiBold
                                        font.pixelSize: Theme.fsSmall
                                    }
                                }

                                Label {
                                    text: jobCard.label
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Label {
                                    visible: jobCard.jobState === "done"
                                             || jobCard.jobState === "failed"
                                    text: "exit " + jobCard.exitCode
                                    opacity: 0.6
                                    font.family: Theme.mono
                                    font.pixelSize: Theme.fsSmall
                                }

                                ToolButton {
                                    text: "Cancel"
                                    visible: jobCard.jobState === "running"
                                             || jobCard.jobState === "pending"
                                    onClicked: syncRunner.cancel(jobCard.jobId)
                                }
                            }

                            // Argv row with copy button. Import jobs run
                            // in-process and have no command line to show.
                            RowLayout {
                                visible: !jobCard.jobCollapsed
                                         && jobCard.argvText !== ""
                                Layout.fillWidth: true
                                spacing: Theme.s1

                                TextEdit {
                                    id: argvEdit
                                    Layout.fillWidth: true
                                    text: jobCard.argvText
                                    readOnly: true
                                    selectByMouse: true
                                    wrapMode: TextEdit.Wrap
                                    font.family: Theme.mono
                                    font.pixelSize: Theme.fsSmall
                                    color: Theme.textColor
                                    opacity: 0.7
                                }
                                RowActionButton {
                                    implicitWidth: 24
                                    implicitHeight: 24
                                    icon.name: "edit-copy"
                                    tip: "Copy command"
                                    Layout.alignment: Qt.AlignTop
                                    onClicked: {
                                        argvEdit.selectAll()
                                        argvEdit.copy()
                                        argvEdit.deselect()
                                    }
                                }
                            }

                            // Log area with follow-tail hint.
                            RowLayout {
                                visible: !jobCard.jobCollapsed
                                Layout.fillWidth: true

                                Item { Layout.fillWidth: true }
                                Label {
                                    text: "↓ following tail"
                                    opacity: 0.55
                                    font.pixelSize: Theme.fsSmall
                                    visible: (logScroll.ScrollBar.vertical.size
                                              + logScroll.ScrollBar.vertical.position)
                                             >= 0.999
                                }
                            }

                            Rectangle {
                                visible: !jobCard.jobCollapsed
                                Layout.fillWidth: true
                                Layout.preferredHeight: 120
                                color: Theme.dark ? Qt.rgba(0, 0, 0, 0.25)
                                                  : Qt.rgba(0, 0, 0, 0.04)
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

                                        // Initial backfill — covers opening
                                        // mid-run or delegate rebuilds.
                                        Component.onCompleted:
                                            text = syncRunner.jobLog(jobCard.jobId)

                                        Connections {
                                            target: syncRunner
                                            function onJobOutputAppended(id, t) {
                                                if (id !== jobCard.jobId) return
                                                // Sample scroll position BEFORE
                                                // insert; only follow tail if
                                                // the user was already at the
                                                // bottom. insert (not append)
                                                // avoids a stray newline.
                                                const sb = logScroll.ScrollBar.vertical
                                                const wasAtBottom =
                                                    (sb.size + sb.position) >= 0.999
                                                logArea.insert(logArea.length, t)
                                                if (wasAtBottom) {
                                                    logArea.cursorPosition = logArea.length
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
