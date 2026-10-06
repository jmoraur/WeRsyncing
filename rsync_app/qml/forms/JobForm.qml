import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Qt.labs.platform as Labs
import "../components"

// Job form. A job is rsync: From (device + path) → To (device + path)
// + options. Bundles are made in the list (select → right-click). The live command preview and the
// checks stay pinned at the bottom.
//
// State lives in one `draft` map mirroring the jobs columns; every change
// goes through set(), which reassigns the map so all bindings re-evaluate.
// Option toggles render from connections.optionCatalog().
//
// Paths: laptop = absolute; drive = inside the drive (relative to where
// it's mounted); server = absolute path on the server.
//
// Modes: jobId === -1 → add, else → edit. Prefills for "new job here":
// initialFromDeviceId / initialToDeviceId.
Dialog {
    id: dlg
    modal: true
    anchors.centerIn: Overlay.overlay
    title: dlg.jobId === -1 ? "New job" : "Edit job"
    standardButtons: Dialog.Save | Dialog.Cancel
    // Width set directly — implicitWidth on the contentItem collapses or
    // loops on Fusion (see lessons).
    width: 860
    height: Math.min(parent ? parent.height - 40 : 760, 760)

    property int jobId: -1
    property int initialFromDeviceId: -1
    property int initialToDeviceId: -1

    property var draft: ({})
    property var _catalog: []
    property var _devices: []
    property string _skipWarning: ""

    readonly property var _issues: connections.preflightDraft(dlg.draft)
    readonly property var _fromResolved: connections.resolvedPath(
        dlg.draft.from_device_id || 0, dlg.draft.from_path || "")
    // Local folder to pick skip-list entries from ("" for a server side).
    readonly property string _fromLocal:
        connections.browseRoot(dlg.draft.from_device_id || 0) !== ""
        ? (_fromResolved.path || "") : ""

    function set(key, value) {
        const d = Object.assign({}, draft)
        d[key] = value
        draft = d
    }

    function _defaults() {
        const d = {
            name: "",
            from_device_id: 0, from_path: "",
            to_device_id: 0, to_path: "",
            path_mode: "contents", chown_mode: "source",
            chown_value: "", chmod_value: "", excludes: "",
        }
        for (const o of _catalog) d[o.key] = o.default ? 1 : 0
        return d
    }

    function _refreshDevices() {
        _devices = connections.pickableDevices()
        fromSide.sync()
        toSide.sync()
    }

    // Append one pattern to the skip list unless it's already there.
    function _appendSkip(pattern) {
        const lines = (excludesArea.text || "").split("\n")
            .map(s => s.trim()).filter(s => s !== "")
        if (lines.indexOf(pattern) !== -1) return
        lines.push(pattern)
        excludesArea.text = lines.join("\n")
    }

    function _openDevice(prefix, id) {
        childDeviceForm.forSide = prefix
        childDeviceForm.deviceId = id
        childDeviceForm.open()
    }

    function _browse(side) {
        folderDialog.side = side
        folderDialog.open()
    }

    function _swap() {
        const d = Object.assign({}, draft)
        ;[d.from_device_id, d.to_device_id] = [d.to_device_id, d.from_device_id]
        ;[d.from_path, d.to_path] = [d.to_path, d.from_path]
        draft = d
        fromSide.sync()
        toSide.sync()
    }

    onAboutToShow: {
        _catalog = connections.optionCatalog()
        _skipWarning = ""
        let d = _defaults()
        if (dlg.jobId !== -1) {
            const j = connections.getJob(dlg.jobId)
            for (const k in d)
                if (j[k] !== undefined && j[k] !== null) d[k] = j[k]
        } else {
            d.to_device_id = dlg.initialToDeviceId > 0 ? dlg.initialToDeviceId : 0
            d.from_device_id = dlg.initialFromDeviceId > 0
                               ? dlg.initialFromDeviceId : 0
            // Otherwise new jobs start from this computer — the usual case.
            const laptop = connections.pickableDevices()
                .find(x => x.kind === "laptop")
            if (!d.from_device_id && laptop && d.to_device_id !== laptop.id)
                d.from_device_id = laptop.id
        }
        draft = d
        nameField.text = d.name || ""
        excludesArea.text = d.excludes || ""
        ownershipEditor.load()
        _refreshDevices()
    }

    onAccepted: {
        if (!(draft.from_device_id > 0) || !(draft.to_device_id > 0))
            return
        let newId = dlg.jobId
        if (dlg.jobId === -1) newId = connections.addJob(draft)
        else connections.updateJob(dlg.jobId, draft)
    }

    // One side of the job: device picker + path + browse + resolved path.
    component Side: GroupBox {
        id: side
        property string prefix   // "from" | "to"
        property var form: null  // the JobForm (ids aren't visible in here)
        readonly property string devKey: prefix + "_device_id"
        readonly property string pathKey: prefix + "_path"
        readonly property int deviceId: form ? (form.draft[devKey] || 0) : 0
        readonly property var device: form ? (form._devices.find(
                                          x => x.id === deviceId) || ({}))
                                           : ({})
        readonly property string root: connections.browseRoot(deviceId)
        readonly property var resolved: connections.resolvedPath(
            deviceId, form ? (form.draft[pathKey] || "") : "")
        property string browseWarning: ""

        function sync() {
            combo.currentIndex = side.form._devices.findIndex(x => x.id === deviceId)
            pathField.text = side.form.draft[pathKey] || ""
        }

        Layout.fillWidth: true
        Layout.preferredWidth: 1   // equal split
        Layout.alignment: Qt.AlignTop

        ColumnLayout {
            anchors.fill: parent
            spacing: Theme.s2

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s1
                ScrollComboBox {
                    id: combo
                    Layout.fillWidth: true
                    model: side.form._devices
                    textRole: "display"
                    valueRole: "id"
                    displayText: currentIndex === -1 ? "— pick a device —"
                                                     : currentText
                    onActivated: {
                        side.form.set(side.devKey, currentValue || 0)
                        side.browseWarning = ""
                    }
                }
                RowActionButton {
                    icon.name: "list-add"
                    tip: "New device"
                    onClicked: side.form._openDevice(side.prefix, -1)
                }
                RowActionButton {
                    icon.name: "document-edit"
                    tip: "Edit selected device"
                    enabled: side.deviceId > 0
                    onClicked: side.form._openDevice(side.prefix, side.deviceId)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s1
                TextField {
                    id: pathField
                    Layout.fillWidth: true
                    font.family: Theme.mono
                    font.pixelSize: Theme.fsMono
                    placeholderText: side.device.kind === "drive"
                                     ? "folder on the drive (empty = whole drive)"
                                     : side.device.kind === "server"
                                     ? "/mnt/user/…" : "/home/…"
                    onTextChanged: side.form.set(side.pathKey, text)
                }
                RowActionButton {
                    icon.name: "folder-open"
                    enabled: side.root !== ""
                    tip: side.deviceId <= 0 ? "Pick a device first"
                         : side.root === ""
                         ? (side.device.kind === "server"
                            ? "Server folders can't be browsed — type the path"
                            : "Browsing needs the drive to be connected")
                         : "Browse"
                    onClicked: side.form._browse(side)
                }
            }
            Label {
                Layout.fillWidth: true
                visible: side.browseWarning !== ""
                text: side.browseWarning
                color: Theme.error
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }
            Label {
                // Where this side really points, resolved live.
                Layout.fillWidth: true
                visible: text !== ""
                text: {
                    const r = side.resolved
                    if (!r || (!r.path && !r.note)) return ""
                    if (!r.path) return "(" + r.note + ")"
                    return r.path + (r.note ? "   (" + r.note + ")" : "")
                }
                opacity: 0.6
                font.family: Theme.mono
                font.pixelSize: Theme.fsSmall
                elide: Text.ElideMiddle
            }
        }
    }

    contentItem: ColumnLayout {
        spacing: Theme.s2

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: parent.width
                spacing: Theme.s3

                // ---- Name ------------------------------------------------
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: Theme.s2
                    rowSpacing: Theme.s2

                    Label { text: "Name"; opacity: 0.75 }
                    TextField {
                        id: nameField
                        Layout.fillWidth: true
                        placeholderText: "optional — the folder name is used"
                                         + " if empty"
                        onTextChanged: dlg.set("name", text)
                    }
                }

                // ---- From | To ---------------------------------------------
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    Side {
                        id: fromSide
                        prefix: "from"
                        form: dlg
                        title: "From"
                    }
                    RowActionButton {
                        Layout.alignment: Qt.AlignVCenter
                        icon.name: "exchange-positions"
                        tip: "Swap From and To"
                        onClicked: dlg._swap()
                    }
                    Side {
                        id: toSide
                        prefix: "to"
                        form: dlg
                        title: "To"
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2
                    Label { text: "Copy"; opacity: 0.75 }
                    SegmentedControl {
                        model: [
                            {value: "contents", label: "what's inside"},
                            {value: "folder", label: "the folder itself"},
                        ]
                        value: dlg.draft.path_mode || "contents"
                        onActivated: v => dlg.set("path_mode", v)
                    }
                    Label {
                        text: dlg.draft.path_mode === "folder"
                              ? "no trailing slash on From"
                              : "trailing slash on From"
                        opacity: 0.5
                        font.pixelSize: Theme.fsSmall
                    }
                }

                // ---- Ownership & permissions -------------------------------
                OwnershipEditor {
                    id: ownershipEditor
                    Layout.fillWidth: true
                    values: dlg.draft
                    onFieldChanged: (key, v) => dlg.set(key, v)
                }

                // ---- Options (catalog-driven) ------------------------------
                OptionsEditor {
                    Layout.fillWidth: true
                    catalog: dlg._catalog
                    values: dlg.draft
                    onOptionToggled: (key, v) => dlg.set(key, v)
                }

                // ---- Skip list ---------------------------------------------
                SectionLabel {
                    text: "SKIP LIST  ·  folders and files not to copy, one per line"
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s1
                    Button {
                        text: "Add folder…"
                        enabled: dlg._fromLocal !== ""
                        onClicked: skipFolderDialog.open()
                    }
                    Repeater {
                        model: [".cache/", "*.tmp", ".Trash-*/", "lost+found/"]
                        Button {
                            required property string modelData
                            text: modelData
                            flat: true
                            font.family: Theme.mono
                            font.pixelSize: Theme.fsMono
                            onClicked: dlg._appendSkip(modelData)
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 72
                    TextArea {
                        id: excludesArea
                        placeholderText: ".cache/\n*.tmp"
                        onTextChanged: dlg.set("excludes", text)
                        font.family: Theme.mono
                        font.pixelSize: Theme.fsMono
                        wrapMode: TextEdit.NoWrap
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: dlg._skipWarning !== ""
                    text: dlg._skipWarning
                    color: Theme.error
                    font.pixelSize: Theme.fsSmall
                    wrapMode: Text.Wrap
                }
            }
        }

        // ---- Pinned: live command + checks ----------------------------
        SectionLabel { text: "COMMAND" }
        CommandPreview {
            Layout.fillWidth: true
            argv: connections.previewCommand(dlg.draft)
            placeholder: "(pick both devices to see the command)"
        }
        ScrollView {
            // Capped so a pile of warnings can't squeeze the form away.
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(issueList.implicitHeight, 120)
            visible: dlg._issues.length > 0
            clip: true
            contentWidth: availableWidth
            IssueList {
                id: issueList
                width: parent.width
                ackable: false
                issues: dlg._issues
            }
        }
    }

    // ---- Folder browser for either side ---------------------------------
    // Laptop paths are stored absolute; drive paths relative to where the
    // drive is mounted (picking outside the drive is rejected).
    Labs.FolderDialog {
        id: folderDialog
        property var side: null
        title: "Choose a folder"
        currentFolder: {
            if (!side) return ""
            const r = side.resolved
            return "file://" + (r.path && r.note === "" ? r.path : side.root)
        }
        onAccepted: {
            const url = folder.toString()
            const picked = decodeURIComponent(
                url.startsWith("file://") ? url.slice(7) : url)
            if (side.device.kind === "laptop") {
                dlg.set(side.pathKey, picked)
            } else {
                const base = side.root.replace(/\/+$/, "")
                if (picked === base) dlg.set(side.pathKey, "")
                else if (picked.startsWith(base + "/"))
                    dlg.set(side.pathKey, picked.slice(base.length + 1))
                else {
                    side.browseWarning = "That folder is not on the drive"
                                         + " — pick one under " + base
                    return
                }
            }
            side.browseWarning = ""
            side.sync()
        }
    }

    // ---- Skip-list folder picker ----------------------------------------
    // Rooted at the From folder; stores the pick relative to what rsync
    // transfers (in "folder itself" mode that starts one level up).
    Labs.FolderDialog {
        id: skipFolderDialog
        title: "Choose a folder to skip"
        currentFolder: "file://" + dlg._fromLocal
        onAccepted: {
            const url = folder.toString()
            const picked = decodeURIComponent(
                url.startsWith("file://") ? url.slice(7) : url)
            const base = dlg._fromLocal.replace(/\/+$/, "")
            if (!picked.startsWith(base + "/")) {
                dlg._skipWarning = picked === base
                    ? "That's the whole From folder — pick a folder inside it."
                    : "That folder is not inside the From folder — pick one"
                      + " under " + base
                return
            }
            dlg._skipWarning = ""
            let rel = picked.slice(base.length + 1) + "/"
            if ((dlg.draft.path_mode || "contents") === "folder")
                rel = base.slice(base.lastIndexOf("/") + 1) + "/" + rel
            dlg._appendSkip(rel)
        }
    }

    // ---- Inline-create child dialogs --------------------------------------
    DeviceForm {
        id: childDeviceForm
        property string forSide: "from"
        onAcceptedWithId: newId => {
            dlg.set(forSide + "_device_id", newId)
            dlg._refreshDevices()
        }
    }
}
