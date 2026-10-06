import QtQuick
import QtQuick.Controls

// ApplicationWindow that follows the app's light/dark/system mode.
// Used by the main window and by every pop-up window (confirmations).
ApplicationWindow {
    // Qt Quick Controls resolve their colors from the platform theme, which
    // (on KDE) ignores the application palette ThemeBridge sets — so the
    // selected mode must cascade from the root window explicitly. SystemPalette
    // does track the application palette, so every control follows it via
    // these bindings, live on mode changes.
    palette.window: Theme.pal.window
    palette.windowText: Theme.pal.windowText
    palette.base: Theme.pal.base
    palette.alternateBase: Theme.pal.alternateBase
    palette.text: Theme.pal.text
    palette.button: Theme.pal.button
    palette.buttonText: Theme.pal.buttonText
    palette.highlight: Theme.pal.highlight
    palette.highlightedText: Theme.pal.highlightedText
    palette.light: Theme.pal.light
    palette.midlight: Theme.pal.midlight
    palette.mid: Theme.pal.mid
    palette.dark: Theme.pal.dark
    palette.shadow: Theme.pal.shadow
    palette.placeholderText: Theme.pal.placeholderText
    palette.disabled.windowText: Theme.palDisabled.windowText
    palette.disabled.text: Theme.palDisabled.text
    palette.disabled.buttonText: Theme.palDisabled.buttonText
    palette.disabled.base: Theme.palDisabled.base
    palette.disabled.highlight: Theme.palDisabled.highlight
    palette.disabled.highlightedText: Theme.palDisabled.highlightedText

    color: Theme.windowBg
}
