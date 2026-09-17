import Foundation

enum PreviewMode {
    static var enabled: Bool {
        #if FREE_PARKING_DEMO
        true
        #else
        ProcessInfo.processInfo.arguments.contains("--demo")
        #endif
    }

    static var scene: String {
        let prefix = "--demo-scene="
        return ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix(prefix) })
            .map { String($0.dropFirst(prefix.count)) } ?? "empty"
    }
}

/// Invented data only. Never read a ledger, terminal or real project for previews.
enum PreviewFixtures {
    static let tabs: [AgentTab] = [
        tab(0, "Update the website", "codex", "Website"),
        tab(1, "Refactor the command parser", "claude", "Tools"),
        tab(2, "Sketch a small prototype", "codex", "Playground"),
    ]

    private static func tab(_ index: Int, _ title: String, _ provider: String, _ folder: String) -> AgentTab {
        AgentTab(index: index, title: title, terminalId: "sample-terminal-\(index)",
                 provider: provider, sessionId: String(format: "00000000-0000-4000-8000-%012d", index + 1),
                 cwd: "~/Projects/" + folder, issue: "", resumeCommand: nil)
    }

    static let window = LiveWindow(id: "sample-window", title: "Website & tools",
                                   tabs: tabs, canPark: true, fingerprint: "preview-only")
    static let unsupportedWindow: LiveWindow = {
        let unknown = AgentTab(index: 2, title: "New conversation", terminalId: "sample-new-terminal",
                               provider: "codex", sessionId: "", cwd: "", issue: "No saved conversation yet. Let a turn finish, then refresh.", resumeCommand: nil)
        return LiveWindow(id: "sample-attention", title: "Website & tools",
                          tabs: Array(tabs.prefix(2)) + [unknown], canPark: false, fingerprint: "preview-only")
    }()

    static func car(id: String, date: String, count: Int = 3, status: String = "parked", title: String = "Website & tools") -> ParkedCar {
        ParkedCar(id: id, createdAt: date, status: status, note: "Sample conversations only.",
                  tabs: Array(tabs.prefix(count)), filePath: "", windowTitle: title)
    }

    private static func date(daysAgo: Int, hour: Int = 17, minute: Int = 42) -> String {
        let calendar = Calendar.current
        let day = calendar.date(byAdding: .day, value: -daysAgo, to: .now)!
        let time = calendar.date(bySettingHour: hour, minute: minute, second: 0, of: day)!
        return ISO8601DateFormatter().string(from: time)
    }

    static let cars = [
        car(id: "11111111-1111-4111-8111-111111111113", date: date(daysAgo: 0)),
        car(id: "22222222-2222-4222-8222-222222222225", date: date(daysAgo: 1, hour: 18, minute: 15), count: 2, title: "Command line"),
        car(id: "33333333-3333-4333-8333-333333333332", date: date(daysAgo: 21, minute: 30), title: "Prototype"),
    ]
}
