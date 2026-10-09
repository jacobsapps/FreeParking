import AppKit
import Carbon
import Foundation

struct SavedTab: Decodable {
    let terminal_id: String
    let title: String
    let cwd: String
    var label: String { title.isEmpty ? (cwd as NSString).lastPathComponent : title }
}
struct SavedWindow: Decodable {
    let window_id: String
    let tabs: [SavedTab]
    let fingerprint: String?
}
struct RecoveryRecord: Decodable {
    let id: String
    let phase: String
    let source: SavedWindow
    let issue: String?
}
struct ReportedIssue: Decodable { let message: String }
struct ParkingSettings: Codable {
    let auto_continue_interrupted: Bool
    let continue_message: String
}
struct Response: Decodable {
    let settings: ParkingSettings?
    let ok: Bool
    let windows: [SavedWindow]
    let records: [RecoveryRecord]
    let errors: [ReportedIssue]
    let can_mutate: Bool?
    let test_mode: Bool
    let error: String?
}
struct Row {
    let title: String
    let subtitle: String
    let status: String
    let names: String
    let command: [String]?
    static func live(_ window: SavedWindow, allowed: Bool) -> Row {
        Row(title: window.tabs.first?.label ?? "Folder window",
            subtitle: "\(window.tabs.count) tabs · \(window.tabs.first?.cwd ?? "")", status: "Open",
            names: window.tabs.map(\.label).joined(separator: "\n"),
            command: allowed ? window.fingerprint.map { ["park", window.window_id, $0] } : nil)
    }
    static func saved(_ record: RecoveryRecord, allowed: Bool) -> Row {
        let labels = ["interrupting": "Stopping task", "saved": "Still open", "closing": "Awaiting close confirmation", "parked": "Parked",
                      "opening": "Opening", "checking": "Checking return", "returned": "Backup"]
        return Row(title: record.source.tabs.first?.label ?? "Saved window",
                   subtitle: record.issue ?? "\(record.source.tabs.count) tabs · recovery retained",
                   status: labels[record.phase] ?? "Needs inspection",
                   names: record.source.tabs.map(\.label).joined(separator: "\n"),
                   command: allowed && ["parked", "checking"].contains(record.phase) ? ["reopen", record.id] : nil)
    }
}

final class Backend {
    static func call(_ arguments: [String], scope: String?) throws -> Response {
        guard let resources = Bundle.main.resourceURL else { throw problem("Bundle resources unavailable.") }
        let script = resources.appendingPathComponent("rebuild/backend.py")
        guard FileManager.default.fileExists(atPath: script.path) else { throw problem("The rebuild helper is missing.") }
        // Request Automation only for an explicit inspection/operation, not launch.
        if !["list", "settings"].contains(arguments.first ?? "list"), !NSRunningApplication.runningApplications(withBundleIdentifier: "com.googlecode.iterm2").isEmpty {
            let descriptor = NSAppleEventDescriptor(bundleIdentifier: "com.googlecode.iterm2")
            let result = AEDeterminePermissionToAutomateTarget(descriptor.aeDesc, typeWildCard, typeWildCard, true)
            guard result == noErr else { throw problem("iTerm Automation access is unavailable (\(result)). No window action was taken.") }
        }
        let task = Process(), stdout = Pipe()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        task.arguments = [script.path] + (scope.map { ["--scope", $0] } ?? ["--live"]) + arguments
        let errorURL = FileManager.default.temporaryDirectory.appendingPathComponent("free-parking-rebuild-\(UUID().uuidString).txt")
        FileManager.default.createFile(atPath: errorURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
        let stderr = try FileHandle(forWritingTo: errorURL)
        defer { try? stderr.close(); try? FileManager.default.removeItem(at: errorURL) }
        task.standardOutput = stdout; task.standardError = stderr
        try task.run()
        let data = stdout.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        guard task.terminationStatus == 0 else {
            throw problem("Helper failed. Recovery is retained. " + ((try? String(contentsOf: errorURL, encoding: .utf8)) ?? ""))
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }
    static func problem(_ text: String) -> NSError { NSError(domain: "FreeParkingRebuild", code: 1, userInfo: [NSLocalizedDescriptionKey: text]) }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate {
    var window: NSWindow!
    let table = NSTableView(), status = NSTextField(wrappingLabelWithString: ""), subtitle = NSTextField(wrappingLabelWithString: "")
    let refresh = NSButton(title: "Refresh", target: nil, action: nil)
    let action = NSButton(title: "Select a window", target: nil, action: nil)
    let previousRecovery = NSButton(title: "Previous recovery…", target: nil, action: nil)
    let settingsButton = NSButton(title: "Settings…", target: nil, action: nil)
    let archive = NSButton(checkboxWithTitle: "Show retained backups", target: nil, action: nil)
    var rows: [Row] = [], latest: Response?
    var busy = false, waitingToQuit = false
    let reviewPath = CommandLine.arguments.first(where: { $0.hasPrefix("--render-own-view=") }).map { String($0.dropFirst(18)) }
    func renderOwnView() {
        guard let path = reviewPath, let view = window.contentView else { return }
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        if let data = bitmap.representation(using: .png, properties: [:]) { try? data.write(to: URL(fileURLWithPath: path)) }
    }
    let scope = CommandLine.arguments.first(where: { $0.hasPrefix("--scope=") }).map { String($0.dropFirst(8)) }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), item = NSMenuItem(), appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Free Parking", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu; menu.addItem(item); NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 650), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Free Parking"
        window.appearance = NSAppearance(named: .aqua)
        window.minSize = NSSize(width: 640, height: 500)
        window.backgroundColor = NSColor(calibratedRed: 0.96, green: 0.97, blue: 0.94, alpha: 1)
        let content = NSView(); window.contentView = content
        let title = NSTextField(labelWithString: "Free Parking")
        title.font = .systemFont(ofSize: 29, weight: .bold); title.textColor = .systemTeal
        subtitle.stringValue = scope == nil ? "Put a window away. Pick up where you left off." : "Disposable test scope · your other windows are protected"
        subtitle.font = .systemFont(ofSize: 12); subtitle.textColor = .secondaryLabelColor
        let badge = NSTextField(wrappingLabelWithString: "Claude · Codex · folder tabs. Only the window you select will be parked.")
        badge.font = .systemFont(ofSize: 12); badge.textColor = .secondaryLabelColor
        refresh.target = self; refresh.action = #selector(refreshClicked)
        action.target = self; action.action = #selector(actionClicked); action.isEnabled = false
        archive.target = self; archive.action = #selector(showBackups)
        settingsButton.target = self; settingsButton.action = #selector(settingsClicked)
        previousRecovery.target = self; previousRecovery.action = #selector(previousRecoveryClicked)
        for b in [refresh, action, settingsButton, previousRecovery] { b.bezelStyle = .rounded }
        let controls = NSStackView(views: [refresh, action, archive, settingsButton]); controls.orientation = .horizontal; controls.spacing = 14
        let col = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("window")); col.title = "Windows & recovery"; table.addTableColumn(col)
        table.headerView = nil; table.rowHeight = 68; table.dataSource = self; table.delegate = self
        table.backgroundColor = .clear; table.style = .plain; table.target = self; table.doubleAction = #selector(actionClicked)
        let scroll = NSScrollView(); scroll.documentView = table; scroll.hasVerticalScroller = true; scroll.drawsBackground = false
        status.font = .systemFont(ofSize: 12); status.textColor = .secondaryLabelColor
        let stack = NSStackView(views: [title, subtitle, badge, controls, scroll, previousRecovery, status]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 16
        content.addSubview(stack); stack.translatesAutoresizingMaskIntoConstraints = false
        for v in [badge, subtitle, scroll, status] { v.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true }
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 28), stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -28), stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 25), stack.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -22), scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 220)])
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        perform(["list"])
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if busy { waitingToQuit = true; return .terminateLater }; return .terminateNow
    }
    @objc func previousRecoveryClicked() {
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Car Park")
        if FileManager.default.fileExists(atPath: url.path) { NSWorkspace.shared.open(url) }
    }
    @objc func settingsClicked() {
        guard !busy else { return }
        let current = latest?.settings ?? ParkingSettings(auto_continue_interrupted: true, continue_message: "please continue")
        let alert = NSAlert()
        alert.messageText = "Resume interrupted tasks"
        alert.informativeText = "After reopening a Claude or Codex session, send this message only if Free Parking confirmed it interrupted a running task. Idle sessions are left alone. Agent permissions remain unchanged."
        alert.addButton(withTitle: "Save"); alert.addButton(withTitle: "Cancel")
        let checkbox = NSButton(checkboxWithTitle: "Automatically continue tasks Free Parking stopped", target: nil, action: nil)
        checkbox.state = current.auto_continue_interrupted ? .on : .off
        let field = NSTextField(string: current.continue_message)
        field.placeholderString = "please continue"
        let stack = NSStackView(views: [checkbox, field]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
        stack.frame = NSRect(x: 0, y: 0, width: 440, height: 70)
        field.widthAnchor.constraint(equalToConstant: 440).isActive = true
        alert.accessoryView = stack
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let value = ParkingSettings(auto_continue_interrupted: checkbox.state == .on, continue_message: field.stringValue)
        guard let data = try? JSONEncoder().encode(value), let json = String(data: data, encoding: .utf8) else { return }
        perform(["settings", json])
    }
    @objc func refreshClicked() { perform(["scan"]) }
    @objc func showBackups() { render() }
    @objc func actionClicked() {
        guard !busy, rows.indices.contains(table.selectedRow), let command = rows[table.selectedRow].command else { return }
        perform(command)
    }
    func perform(_ args: [String]) {
        guard !busy else { return }
        busy = true; refresh.isEnabled = false; action.isEnabled = false; settingsButton.isEnabled = false
        status.stringValue = args.first == "park" ? "Saving first, then closing only the selected window. Confirm in iTerm if asked." : "Reading recovery and observed state…"
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            let result = Result { try Backend.call(args, scope: scope) }
            DispatchQueue.main.async { [self] in
                busy = false; refresh.isEnabled = true; settingsButton.isEnabled = true
                switch result {
                case .success(let response):
                    latest = response; render()
                    status.stringValue = response.error ?? response.errors.map(\.message).joined(separator: "\n")
                    if status.stringValue.isEmpty { status.stringValue = "Recovery stays local. Tab names are display-only." }
                case .failure(let error): status.stringValue = error.localizedDescription
                }
                if args.first == "scan", reviewPath != nil {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.renderOwnView() }
                }
                if waitingToQuit { NSApp.reply(toApplicationShouldTerminate: true); return }
                if args.first == "list" || args.first == "settings" || args.first == "park" || args.first == "reopen" { perform(["scan"]) }
            }
        }
    }
    func render() {
        guard let response = latest else { return }
        let closing = Set(response.records.filter { ["closing", "interrupting"].contains($0.phase) }.map { $0.source.window_id })
        rows = response.windows.map { Row.live($0, allowed: (response.can_mutate ?? response.test_mode) && !closing.contains($0.window_id)) }
        rows += response.records.filter { archive.state == .on || $0.phase != "returned" }.map { Row.saved($0, allowed: response.can_mutate ?? response.test_mode) }
        table.reloadData(); tableViewSelectionDidChange(Notification(name: NSTableView.selectionDidChangeNotification))
    }
    func numberOfRows(in tableView: NSTableView) -> Int { rows.count }
    func tableViewSelectionDidChange(_ notification: Notification) {
        let command = rows.indices.contains(table.selectedRow) ? rows[table.selectedRow].command : nil
        action.title = command?.first == "park" ? "Park selected window" : command == nil ? "Select a window" : "Reopen saved window"
        action.isEnabled = !busy && command != nil
    }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let value = rows[row], cell = NSTableCellView()
        let heading = NSTextField(labelWithString: value.title), detail = NSTextField(labelWithString: value.subtitle), state = NSTextField(labelWithString: value.status)
        heading.font = .systemFont(ofSize: 14, weight: .semibold); heading.lineBreakMode = .byTruncatingTail
        detail.font = .systemFont(ofSize: 11); detail.textColor = .secondaryLabelColor; detail.lineBreakMode = .byTruncatingMiddle
        state.font = .systemFont(ofSize: 11, weight: .semibold); state.textColor = value.status == "Parked" ? .systemTeal : .systemIndigo
        let words = NSStackView(views: [heading, detail]); words.orientation = .vertical; words.alignment = .leading; words.spacing = 5
        let line = NSStackView(views: [words, state]); line.orientation = .horizontal; line.spacing = 12; line.distribution = .fill
        cell.addSubview(line); line.translatesAutoresizingMaskIntoConstraints = false
        state.setContentCompressionResistancePriority(.required, for: .horizontal)
        NSLayoutConstraint.activate([line.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 8), line.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -8), line.centerYAnchor.constraint(equalTo: cell.centerYAnchor)])
        cell.toolTip = value.names; return cell
    }
}

@main enum FreeParkingRebuild {
    static func main() throws {
        if CommandLine.arguments.contains("--self-test") {
            let data = #"{"ok":true,"windows":[{"window_id":"fixture","fingerprint":"exact","tabs":[{"terminal_id":"t","title":"A saved title","cwd":"/tmp"}]}],"records":[],"errors":[],"test_mode":false}"#.data(using: .utf8)!
            let response = try JSONDecoder().decode(Response.self, from: data)
            precondition(Row.live(response.windows[0], allowed: false).command == nil)
            precondition(Row.live(response.windows[0], allowed: true).command == ["park", "fixture", "exact"])
            precondition(Row.live(response.windows[0], allowed: true).names == "A saved title")
            let pending = RecoveryRecord(id: "pending", phase: "closing", source: response.windows[0], issue: nil)
            precondition(Row.saved(pending, allowed: true).command == nil)
            precondition(Row.saved(pending, allowed: true).status == "Awaiting close confirmation")
            print("Native transport/view-model self-test passed. No app or terminal was launched.")
            return
        }
        let app = NSApplication.shared, delegate = AppDelegate()
        app.setActivationPolicy(.regular); app.delegate = delegate; app.run()
        withExtendedLifetime(delegate) {}
    }
}
