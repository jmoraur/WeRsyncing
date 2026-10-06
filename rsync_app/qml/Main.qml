import QtCore
import QtQuick
import QtQuick.Controls

import "./pages"
import "./components"

ThemedWindow {
    id: window
    width: 1100
    height: 720
    visible: true
    title: "WeRsyncing"

    Settings {
        category: "window"
        property alias x: window.x
        property alias y: window.y
        property alias width: window.width
        property alias height: window.height
        property alias visibility: window.visibility
    }

    Component.onCompleted: _ensureOnScreen()

    function _ensureOnScreen() {
        const screens = Qt.application.screens
        for (let i = 0; i < screens.length; i++) {
            const s = screens[i]
            if (window.x >= s.virtualX && window.x < s.virtualX + s.width
                && window.y >= s.virtualY && window.y < s.virtualY + s.height) {
                return
            }
        }
        window.x = Screen.virtualX + (Screen.width - window.width) / 2
        window.y = Screen.virtualY + (Screen.height - window.height) / 2
    }

    ConnectionsPage {
        anchors.fill: parent
    }
}
