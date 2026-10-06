import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Plain-language ownership & permissions editor, shared by JobForm
// and SyncConfirmWindow. Three choices map to chown_mode:
//   "Like in source"      → 'source'  (preserve everything; --archive as-is)
//   "Like in destination" → 'dest'    (fixed recipe; stored values ignored)
//   "Custom…"             → 'custom'  (exact --chown/--chmod values)
// Custom is edited through four small fields (owner / group / folders /
// files) that compose chown_value ("owner:group") and chmod_value
// ("D755,F644") — no rsync syntax surfaces in the UI.
//
// The caller owns state: pass the draft as `values`, apply edits in
// onFieldChanged, and call load() after (re)filling the draft so the
// Custom fields pick up stored values.
ColumnLayout {
    id: root
    spacing: Theme.s2

    property var values: ({})
    signal fieldChanged(string key, var value)

    // load() sets field text, which fires onTextChanged; the guard keeps
    // that from recomposing (and possibly rewriting) the stored values.
    property bool _loading: false

    function load() {
        _loading = true
        const chown = String(values.chown_value || "")
        const colon = chown.indexOf(":")
        ownerField.text = colon === -1 ? chown : chown.slice(0, colon)
        groupField.text = colon === -1 ? "" : chown.slice(colon + 1)
        const perms = _parseChmod(String(values.chmod_value || ""))
        foldersField.text = perms.folders
        filesField.text = perms.files
        _loading = false
    }

    // Accepts what _recompose emits ("D755,F644", either order, one part
    // alone) plus a bare octal (applies to both). Anything else — none is
    // known to exist in real data — leaves the fields empty.
    function _parseChmod(v) {
        const out = { folders: "", files: "" }
        for (const part of v.split(",")) {
            const p = part.trim()
            if (/^D[0-7]{3,4}$/.test(p)) out.folders = p.slice(1)
            else if (/^F[0-7]{3,4}$/.test(p)) out.files = p.slice(1)
            else if (/^[0-7]{3,4}$/.test(p)) { out.folders = p; out.files = p }
        }
        return out
    }

    function _recompose() {
        if (_loading) return
        const owner = ownerField.text.trim()
        const group = groupField.text.trim()
        fieldChanged("chown_value",
                     owner || group ? owner + (group ? ":" + group : "") : "")
        const parts = []
        if (foldersField.text.trim()) parts.push("D" + foldersField.text.trim())
        if (filesField.text.trim()) parts.push("F" + filesField.text.trim())
        fieldChanged("chmod_value", parts.join(","))
    }

    readonly property bool _ownerOk: !/[:\s]/.test(ownerField.text)
    readonly property bool _groupOk: !/[:\s]/.test(groupField.text)
    readonly property bool _foldersOk: /^([0-7]{3,4})?$/.test(foldersField.text.trim())
    readonly property bool _filesOk: /^([0-7]{3,4})?$/.test(filesField.text.trim())

    SectionLabel { text: "OWNERSHIP & PERMISSIONS" }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s2

        SegmentedControl {
            model: [
                {value: "source", label: "Like in source"},
                {value: "dest", label: "Like in destination"},
                {value: "custom", label: "Custom…"},
            ]
            value: root.values.chown_mode || "source"
            onActivated: v => root.fieldChanged("chown_mode", v)
        }
        Item { Layout.fillWidth: true }
    }

    Label {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        opacity: 0.6
        font.pixelSize: Theme.fsSmall
        text: {
            const mode = root.values.chown_mode || "source"
            if (mode === "source")
                return "Copies keep their owner and permissions exactly as"
                     + " they are on the source."
            if (mode === "dest")
                return "Copies end up as if created fresh on the destination"
                     + " — owned by the receiving account, with its normal"
                     + " permissions. Programs stay runnable."
            return "Everything that arrives gets exactly the owner, group"
                 + " and permission numbers below."
        }
    }

    GridLayout {
        visible: (root.values.chown_mode || "source") === "custom"
        Layout.fillWidth: true
        columns: 4
        columnSpacing: Theme.s3
        rowSpacing: Theme.s1

        Label { text: "Owner"; opacity: 0.75 }
        TextField {
            id: ownerField
            Layout.fillWidth: true
            placeholderText: "e.g. nobody"
            onTextChanged: root._recompose()
        }
        Label { text: "Group"; opacity: 0.75 }
        TextField {
            id: groupField
            Layout.fillWidth: true
            placeholderText: "e.g. users"
            onTextChanged: root._recompose()
        }

        Label { text: "Folders"; opacity: 0.75 }
        TextField {
            id: foldersField
            Layout.fillWidth: true
            placeholderText: "e.g. 755"
            onTextChanged: root._recompose()
        }
        Label { text: "Files"; opacity: 0.75 }
        TextField {
            id: filesField
            Layout.fillWidth: true
            placeholderText: "e.g. 755"
            onTextChanged: root._recompose()
        }
    }

    Label {
        visible: (root.values.chown_mode || "source") === "custom"
                 && !(root._ownerOk && root._groupOk
                      && root._foldersOk && root._filesOk)
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: Theme.error
        font.pixelSize: Theme.fsSmall
        text: {
            if (!root._ownerOk || !root._groupOk)
                return "Owner and Group can't contain spaces or colons."
            return "Folders and Files take a 3-digit permission number"
                 + " like 755."
        }
    }

    Label {
        visible: (root.values.chown_mode || "source") === "custom"
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        opacity: 0.6
        font.pixelSize: Theme.fsSmall
        text: "Tip: 644 for Files keeps them from being marked as runnable"
            + " programs. Assigning a different owner only works when the"
            + " connection signs in as the administrator (root)."
    }
}
