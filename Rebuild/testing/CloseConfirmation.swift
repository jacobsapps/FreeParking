// Disposable-test helper only. Not shipped in the application.
// Maps AX windows to immutable CG window IDs; never selects the front window.
import AppKit
import ApplicationServices

@_silgen_name("_AXUIElementGetWindow")
func windowNumber(_ element: AXUIElement, _ number: UnsafeMutablePointer<CGWindowID>) -> AXError

func value(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var result: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &result) == .success ? result : nil
}
func elements(_ element: AXUIElement, _ name: String) -> [AXUIElement] {
    value(element, name) as? [AXUIElement] ?? []
}
func text(_ element: AXUIElement, _ name: String) -> String { value(element, name) as? String ?? "" }
func fail(_ message: String) -> Never { fputs(message + "\n", stderr); exit(1) }
let args = CommandLine.arguments
if args.count != 4 { fail("Use PID WINDOW_ID inspect|describe|OK|Cancel") }
guard let pid = Int32(args[1]), let wid = UInt32(args[2]), ["inspect", "describe", "OK", "Cancel"].contains(args[3]),
      NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "com.googlecode.iterm2" else { fail("Invalid target") }
guard AXIsProcessTrusted() else { fail("Accessibility is unavailable") }
let app = AXUIElementCreateApplication(pid)
let windows = elements(app, kAXWindowsAttribute)
let matches = windows.filter { var n: CGWindowID = 0; return windowNumber($0, &n) == .success && n == wid }
guard matches.count == 1 else { print("absent"); exit(0) }
let window = matches[0]
let sheets = elements(window, kAXChildrenAttribute).filter { text($0, kAXRoleAttribute) == kAXSheetRole }
guard sheets.count == 1 else { print("no-close-sheet"); exit(0) }
let children = elements(sheets[0], kAXChildrenAttribute)
let texts = children.filter { text($0,kAXRoleAttribute) == kAXStaticTextRole }.map { text($0,kAXValueAttribute) }
let buttons = children.filter { text($0,kAXRoleAttribute) == kAXButtonRole }
if args[3] == "describe" {
    let data = try JSONSerialization.data(withJSONObject: ["texts": texts, "buttons": buttons.map { text($0,kAXTitleAttribute) }])
    print(String(decoding: data, as: UTF8.self)); exit(0)
}
// The live iTerm window-close sheet. Never accept an unrelated confirmation.
guard texts.contains(where: { $0.hasPrefix("Close Window #") && $0.hasSuffix("?") }),
      texts.contains(where: { $0 == "This window will be closed." || $0.hasPrefix("This window is running ") }),
      Set(buttons.map { text($0,kAXTitleAttribute) }) == Set(["Cancel", "OK"]) else {
    print("unrecognized-sheet"); exit(0)
}
if args[3] == "inspect" { print("close-confirmation"); exit(0) }
let selected = buttons.filter { text($0,kAXTitleAttribute) == args[3] }
guard selected.count == 1 else { fail("Ambiguous confirmation button") }
var current: CGWindowID = 0
guard windowNumber(window, &current) == .success && current == wid else { fail("Target changed") }
guard AXUIElementPerformAction(selected[0], kAXPressAction as CFString) == .success else { fail("Confirmation action failed") }
print("responded-" + args[3])
