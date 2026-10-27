// Disposable installed-app verification only. Never copied into the app bundle.
// No launching, activation, keyboard events, typing, terminal calls or cleanup.
import AppKit
import ApplicationServices
import Darwin

func fail(_ message: String) -> Never { fputs(message + "\n", stderr); exit(1) }
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var result: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &result) == .success ? result : nil
}
func text(_ element: AXUIElement, _ name: String) -> String { attr(element, name) as? String ?? "" }
func children(_ element: AXUIElement, _ name: String = kAXChildrenAttribute) -> [AXUIElement] {
    attr(element, name) as? [AXUIElement] ?? []
}
func actions(_ element: AXUIElement) -> [String] {
    var result: CFArray?
    guard AXUIElementCopyActionNames(element, &result) == .success else { return [] }
    return result as? [String] ?? []
}
func ps(_ pid: pid_t, _ field: String) -> String {
    let process = Process(), output = Pipe(), errors = Pipe()
    process.executableURL = URL(fileURLWithPath: "/bin/ps")
    process.arguments = ["-ww", "-p", String(pid), "-o", field + "="]
    process.standardOutput = output; process.standardError = errors
    do { try process.run() } catch { fail("Process inspection unavailable: \(error)") }
    let data = output.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else { fail("Exact process inspection failed") }
    let result = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
    guard !result.isEmpty else { fail("Target process is absent") }
    return result
}
func exactArguments(_ pid: pid_t) -> [String] {
    // ps command is retained as a second check, but does not preserve argv
    // boundaries around spaces. KERN_PROCARGS2 makes --test-scope exact.
    var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, pid]
    var size = 0
    guard sysctl(&mib, u_int(mib.count), nil, &size, nil, 0) == 0,
          size > MemoryLayout<Int32>.size, size <= 2 * 1024 * 1024 else { fail("Cannot read exact target argv") }
    var bytes = [UInt8](repeating: 0, count: size)
    let result = bytes.withUnsafeMutableBytes { sysctl(&mib, u_int(mib.count), $0.baseAddress, &size, nil, 0) }
    guard result == 0 else { fail("Cannot verify exact target argv") }
    let argc = bytes.withUnsafeBytes { $0.loadUnaligned(as: Int32.self) }
    guard argc > 0, argc < 1000 else { fail("Invalid target argv") }
    var offset = MemoryLayout<Int32>.size
    // Executable pathname precedes argv, separated by zero padding.
    while offset < size && bytes[offset] != 0 { offset += 1 }
    while offset < size && bytes[offset] == 0 { offset += 1 }
    var argv: [String] = []
    for _ in 0..<argc {
        let start = offset
        while offset < size && bytes[offset] != 0 { offset += 1 }
        guard offset < size, let value = String(bytes: bytes[start..<offset], encoding: .utf8) else { fail("Incomplete target argv") }
        argv.append(value); offset += 1
    }
    return argv
}

struct Target {
    let pid: pid_t
    let scope: URL
    let birth: String
    let argv: [String]
    let launchDate: Date
    let baseline: Data
    static let bundle = URL(fileURLWithPath: "/Applications/Free Parking.app").resolvingSymlinksInPath()
    static var executable: String { bundle.appendingPathComponent("Contents/MacOS/FreeParking").path }

    static func baseline(_ scope: URL) -> Data {
        guard let data = try? Data(contentsOf: scope),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              json["schema_version"] as? Int == 2,
              let windows = json["protected_windows"] as? [[String: Any]],
              let processes = json["protected_processes"] as? [[String: Any]], !processes.isEmpty,
              json["owned"] is [String: Any], json["launch_commands"] is [String] else { fail("Missing version-2 protective scope") }
        // Ownership receipts legitimately change during app operations; the
        // protective baseline must not. Production backend performs its own audit.
        return try! JSONSerialization.data(withJSONObject: ["windows": windows, "processes": processes, "allowProtectedAdditions": json["allow_protected_additions"] as? Bool ?? false], options: [.sortedKeys])
    }
    static func app(_ pid: pid_t) -> NSRunningApplication {
        guard let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated,
              app.bundleIdentifier == "com.jacobsapps.FreeParking",
              app.bundleURL?.resolvingSymlinksInPath() == bundle,
              app.executableURL?.resolvingSymlinksInPath().path == executable else { fail("Not the exact installed Free Parking process") }
        return app
    }
    static func capture(_ pid: pid_t, _ scope: URL) -> Target {
        let app = app(pid)
        guard let launch = app.launchDate else { fail("Cannot establish app process birth") }
        let argv = exactArguments(pid)
        guard argv.first == executable,
              argv.filter({ $0.hasPrefix("--test-scope=") }) == ["--test-scope=" + scope.path],
              !argv.contains("--live"), !argv.contains("--demo") else { fail("App is not running with exactly the requested disposable scope") }
        let command = ps(pid, "command")
        guard command.hasPrefix(executable), command.contains("--test-scope=" + scope.path) else { fail("ps and exact process arguments disagree") }
        let target = Target(pid: pid, scope: scope, birth: ps(pid, "lstart"), argv: argv,
                            launchDate: launch, baseline: baseline(scope))
        target.verify()
        return target
    }
    func verify() {
        let app = Self.app(pid)
        guard app.launchDate == launchDate, ps(pid, "lstart") == birth,
              exactArguments(pid) == argv, Self.baseline(scope) == baseline else { fail("Target process or protective baseline changed") }
    }
}

struct Control {
    let element: AXUIElement
    let path: String
    let role: String
    let title: String
    let description: String
    let identifier: String
    let enabled: Bool
    let actions: [String]
    var json: [String: Any] {
        ["path": path, "role": role, "title": title, "description": description,
         "identifier": identifier, "enabled": enabled, "actions": actions,
         "value": text(element, kAXValueAttribute)]
    }
}
func controls(_ pid: pid_t) -> [Control] {
    let app = AXUIElementCreateApplication(pid)
    let windows = children(app, kAXWindowsAttribute)
    guard !windows.isEmpty else { fail("The exact app exposes no AX windows") }
    var seen: [AXUIElement] = [], result: [Control] = []
    func walk(_ element: AXUIElement, _ path: String, _ depth: Int) {
        guard depth <= 40, seen.count < 5000 else { fail("AX tree exceeds bounded inspection") }
        if seen.contains(where: { CFEqual($0, element) }) { return }
        seen.append(element)
        var owner: pid_t = 0
        guard AXUIElementGetPid(element, &owner) == .success, owner == pid else { fail("AX tree escaped the exact app process") }
        result.append(Control(element: element, path: path, role: text(element, kAXRoleAttribute),
                              title: text(element, kAXTitleAttribute), description: text(element, kAXDescriptionAttribute),
                              identifier: text(element, kAXIdentifierAttribute),
                              enabled: (attr(element, kAXEnabledAttribute) as? Bool) == true, actions: actions(element)))
        for (index, child) in children(element).enumerated() { walk(child, path + "/" + String(index), depth + 1) }
    }
    for (index, window) in windows.enumerated() { walk(window, "window/" + String(index), 0) }
    return result
}

let args = CommandLine.arguments
if args.count < 4 || args.count > 5 { fail("Use PID ABSOLUTE_SCOPE inspect | press-label EXACT_LABEL | press-id EXACT_IDENTIFIER") }
guard let pid = pid_t(args[1]), pid > 0, args[2].hasPrefix("/"),
      ["inspect", "press-label", "press-id"].contains(args[3]),
      args.count == (args[3] == "inspect" ? 4 : 5) else { fail("Invalid scoped action") }
let scope = URL(fileURLWithPath: args[2]).standardizedFileURL.resolvingSymlinksInPath()
guard scope.path == args[2] else { fail("Provide the exact canonical scope path used in app argv") }
let target = Target.capture(pid, scope)
guard AXIsProcessTrusted() else { fail("Accessibility is unavailable; no permission prompt or fallback action attempted") }
let tree = controls(pid)
target.verify()
if args[3] == "inspect" {
    let data = try JSONSerialization.data(withJSONObject: ["pid": pid, "scope": scope.path, "controls": tree.map(\.json)], options: [.prettyPrinted, .sortedKeys])
    print(String(decoding: data, as: UTF8.self))
} else {
    let label = args[4]
    guard !label.isEmpty else { fail("An exact nonempty label/identifier is required") }
    let matches = tree.filter { control in
        guard control.enabled, control.actions.contains(kAXPressAction) else { return false }
        if args[3] == "press-id" { return control.identifier == label }
        return control.role == kAXButtonRole && (control.title == label || control.description == label)
    }
    guard matches.count == 1 else { fail("Expected exactly one enabled pressable control; found \(matches.count)") }
    target.verify()
    // Re-read selection and ownership before a single AXPress. Never retry an
    // ambiguous result: the app's durable operation record is the next evidence.
    let selected = matches[0]
    var owner: pid_t = 0
    guard AXUIElementGetPid(selected.element, &owner) == .success, owner == pid,
          (attr(selected.element, kAXEnabledAttribute) as? Bool) == true,
          actions(selected.element).contains(kAXPressAction),
          (args[3] == "press-id" ? text(selected.element, kAXIdentifierAttribute) == label :
            text(selected.element, kAXTitleAttribute) == label || text(selected.element, kAXDescriptionAttribute) == label) else { fail("Control changed before action") }
    let result = AXUIElementPerformAction(selected.element, kAXPressAction as CFString)
    guard result == .success else { fail("AXPress is unconfirmed (\(result.rawValue)); do not repeat automatically") }
    print("pressed-once " + label)
}
