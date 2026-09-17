import AppKit
import Carbon
import Foundation
import SwiftUI

struct AgentTab: Decodable, Identifiable, Sendable {
    let index: Int
    let title: String
    let terminalId: String
    let provider: String
    let sessionId: String
    let cwd: String
    let issue: String
    let resumeCommand: String?

    var id: Int { index }
    var folder: String { cwd.isEmpty ? "Unidentified tab" : (cwd as NSString).lastPathComponent }
    var displayTitle: String { title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? folder : title }
    var shortPath: String { cwd.replacingOccurrences(of: NSHomeDirectory(), with: "~") }
    var label: String { provider == "codex" ? "Codex" : provider == "claude" ? "Claude" : provider == "shell" ? "Folder" : "Unknown" }
}

struct LiveWindow: Decodable, Identifiable, Sendable {
    let id: String
    let title: String
    let tabs: [AgentTab]
    let canPark: Bool
    let fingerprint: String
}

struct ParkedCar: Decodable, Identifiable, Sendable {
    let id: String
    let createdAt: String
    let status: String
    let note: String
    let tabs: [AgentTab]
    let filePath: String
    var windowTitle: String? = nil

    var displayTitle: String {
        if let windowTitle, !windowTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return windowTitle }
        return tabs.first?.folder ?? "Saved window"
    }

    var date: Date { ISO8601DateFormatter().date(from: createdAt) ?? .distantPast }
    var statusLabel: String {
        switch status {
        case "parked": return "Parked"
        case "restored": return "Opened"
        case "saved_open": return "Saved · still open"
        case "restoring": return "Restore interrupted"
        default: return "Needs attention"
        }
    }
    var needsAttention: Bool { !["parked", "restored"].contains(status) }
    var hasReturned: Bool { status == "restored" }
    var commands: String { tabs.compactMap(\.resumeCommand).joined(separator: "\n\n") }

    func withStatus(_ status: String, note: String) -> ParkedCar {
        ParkedCar(id: id, createdAt: createdAt, status: status, note: note, tabs: tabs, filePath: filePath, windowTitle: windowTitle)
    }
}

struct RemovalRequest: Decodable, Identifiable, Sendable {
    let carId: String
    let token: String
    let reason: String
    var id: String { carId }
}

struct BackendResponse: Decodable, Sendable {
    let ok: Bool
    let windows: [LiveWindow]
    let cars: [ParkedCar]
    let warnings: [String]
    let message: String?
    let error: String?
    let errorCode: String?
    let confirmationRequired: RemovalRequest?
}

enum LocalBackend {
    static func call(_ arguments: [String]) throws -> BackendResponse {
        guard !PreviewMode.enabled else {
            throw BackendError("Terminal access is disabled in the UI preview.")
        }
        guard let script = Bundle.main.url(forResource: "freeparking", withExtension: "py") else {
            throw BackendError("The local helper is missing. Build the .app with scripts/build-app.sh; do not run the Swift executable directly.")
        }
        // Ask in the app's own identity before a timed helper call. The first
        // macOS consent dialog may stay open longer than a discovery timeout.
        if arguments.first != "list" && arguments.first != "remove-confirmed",
           !NSRunningApplication.runningApplications(withBundleIdentifier: "com.googlecode.iterm2").isEmpty {
            let target = NSAppleEventDescriptor(bundleIdentifier: "com.googlecode.iterm2")
            let status = AEDeterminePermissionToAutomateTarget(target.aeDesc, typeWildCard, typeWildCard, true)
            if status == errAEEventNotPermitted {
                throw BackendError("Allow Free Parking to access iTerm in System Settings → Privacy & Security → Automation, then try again. Accessibility permission is not needed.", code: "automation_denied")
            }
            if status != noErr { throw BackendError("Could not request iTerm Automation access (\(status)). No terminal action was taken.") }
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [script.path] + arguments
        let output = Pipe()
        process.standardOutput = output
        // A separate file avoids pipe deadlocks if macOS emits long permission errors.
        let errorURL = FileManager.default.temporaryDirectory.appendingPathComponent("car-park-\(UUID().uuidString).log")
        FileManager.default.createFile(atPath: errorURL.path, contents: nil,
                                       attributes: [.posixPermissions: 0o600])
        let errorFile = try FileHandle(forWritingTo: errorURL)
        defer {
            try? errorFile.close()
            try? FileManager.default.removeItem(at: errorURL)
        }
        process.standardError = errorFile
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            let detail = (try? String(contentsOf: errorURL, encoding: .utf8)) ?? ""
            throw BackendError("The helper stopped unexpectedly. Any saved recovery files have been kept. \(detail)")
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do { return try decoder.decode(BackendResponse.self, from: data) }
        catch { throw BackendError("Could not read the helper's response. Recovery files have not been removed. \(error.localizedDescription)") }
    }
}

struct BackendError: LocalizedError {
    let message: String
    var code: String?
    init(_ message: String, code: String? = nil) { self.message = message; self.code = code }
    var errorDescription: String? { message }
}

struct CarIssue {
    let summary: String
    let detail: String
}

@MainActor
final class Garage: ObservableObject {
    let isPreview = PreviewMode.enabled
    private var previewLoaded = false
    @Published var cars: [ParkedCar] = []
    @Published var windows: [LiveWindow] = []
    @Published var busy = false
    @Published var activity = ""
    @Published var message = ""
    @Published var problem: String?
    @Published var hasScanned = false
    @Published var selectedCar: ParkedCar?
    @Published var selectedWindow: LiveWindow?
    @Published var recoveryCar: ParkedCar?
    @Published var carIssues: [String: CarIssue] = [:]
    @Published var activeCarID: String?
    @Published var removingCar = false
    @Published var pendingRemoval: RemovalRequest?
    @Published var automationDenied = false
    private var previewUnverifiedIDs: Set<String> = []

    func issue(for car: ParkedCar) -> CarIssue? {
        if let issue = carIssues[car.id] { return issue }
        guard car.needsAttention, activeCarID != car.id else { return nil }
        return CarIssue(summary: car.status == "saved_open" ? "Window still open" : "Opening incomplete",
                        detail: car.note)
    }

    func load() {
        if isPreview {
            guard !previewLoaded else { return }
            previewLoaded = true
            loadPreview()
            return
        }
        perform(["scan"], activity: "Reading iTerm windows…")
    }
    func scan() {
        guard !busy, pendingRemoval == nil else { return }
        if isPreview {
            windows = [PreviewFixtures.window]
            hasScanned = true
            return
        }
        perform(["scan"], activity: "Reading iTerm windows…")
    }
    func parkAll() {
        guard !busy, pendingRemoval == nil else { return }
        selectedWindow = nil
        if isPreview {
            let targets = windows.isEmpty ? [PreviewFixtures.window] : windows
            guard targets.allSatisfy(\.canPark) else {
                problem = "Nothing closed. Finish the unidentified session, then park again."
                return
            }
            for window in targets {
                cars.insert(PreviewFixtures.car(id: UUID().uuidString, date: ISO8601DateFormatter().string(from: .now),
                                                count: window.tabs.count, title: window.title), at: 0)
            }
            windows = []
            hasScanned = false
            return
        }
        perform(["park-all"], activity: "Saving all windows, then closing. Confirm in iTerm if asked…")
    }
    func reopenAll() {
        guard !busy, pendingRemoval == nil, !cars.isEmpty else { return }
        if isPreview {
            cars = cars.map { $0.withStatus("restored", note: "Fictional return. No terminals were opened.") }
            return
        }
        perform(["restore-all"], activity: "Reopening your windows…")
    }
    func park(_ window: LiveWindow) {
        guard !busy, pendingRemoval == nil else { return }
        guard window.canPark else { selectedWindow = window; return }
        selectedWindow = nil
        if isPreview {
            withAnimation {
                cars.insert(PreviewFixtures.car(id: UUID().uuidString, date: ISO8601DateFormatter().string(from: .now)), at: 0)
                windows.removeAll { $0.id == window.id }
                hasScanned = false
                message = ""
            }
            return
        }
        perform(["park", window.id, window.fingerprint], activity: "Saving first, then parking. Check iTerm for its close confirmation…")
    }
    /// Opening a car resumes its saved conversations or focuses them.
    /// It never navigates to an inspection or confirmation screen.
    func open(_ car: ParkedCar) {
        guard !busy, pendingRemoval == nil else { return }
        selectedCar = nil
        recoveryCar = nil
        carIssues[car.id] = nil
        message = ""
        if isPreview {
            if let index = cars.firstIndex(where: { $0.id == car.id }) {
                cars[index] = car.withStatus("restored", note: "Fictional return. No terminals were opened.")
            }
            return
        }
        perform(["restore", car.id], activity: "Opening in iTerm…", carID: car.id)
    }

    /// A fresh exact match needs no alert. A readable mismatch requires a
    /// separate, car-specific confirmation; permission failures never bypass it.
    func removeCar(_ car: ParkedCar) {
        guard !busy, pendingRemoval == nil, cars.contains(where: { $0.id == car.id }) else { return }
        if isPreview {
            if !car.hasReturned || previewUnverifiedIDs.contains(car.id) {
                pendingRemoval = RemovalRequest(carId: car.id, token: "preview-only-" + car.id,
                    reason: "The saved tabs are not all confirmed open in iTerm.")
            } else { removePreviewCar(car.id) }
            return
        }
        perform(["remove", car.id], activity: "Checking open sessions…", carID: car.id)
    }

    func confirmRemoval(_ request: RemovalRequest) {
        guard !busy, pendingRemoval?.carId == request.carId,
              pendingRemoval?.token == request.token else { return }
        pendingRemoval = nil
        if isPreview { removePreviewCar(request.carId); return }
        perform(["remove-confirmed", request.carId, request.token], activity: "Keeping recovery and removing car…", carID: request.carId)
    }

    private func removePreviewCar(_ id: String) {
        withAnimation(.easeInOut(duration: 0.3)) { cars.removeAll { $0.id == id } }
        carIssues[id] = nil
        previewUnverifiedIDs.remove(id)
        message = ""
    }

    func openAutomationSettings() {
        guard !isPreview else { return }
        // Open Settings only on an explicit click. Never grant access or change
        // TCC preferences ourselves. The message names the exact pane to visit.
        NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/System Settings.app"))
    }

    private func perform(_ arguments: [String], activity: String, carID: String? = nil) {
        guard !busy, pendingRemoval == nil else { return }
        busy = true
        activeCarID = carID
        removingCar = ["remove", "remove-confirmed"].contains(arguments.first ?? "")
        self.activity = activity
        problem = nil
        message = ""
        if let carID { carIssues[carID] = nil }
        Task {
            var failure: String?
            do {
                let response = try await Task.detached(priority: .userInitiated) {
                    try LocalBackend.call(arguments)
                }.value
                withAnimation(.easeInOut(duration: 0.35)) {
                    cars = response.cars
                    if arguments.first == "scan" {
                        windows = response.windows
                        hasScanned = response.ok
                    } else if arguments.first != "list" {
                        // Do not show stale live-window buttons after a mutation.
                        windows = []
                        hasScanned = false
                    }
                }
                if carID == nil { message = response.message ?? "" }
                if !response.ok { failure = response.error ?? "The operation did not finish." }
                if response.errorCode == "automation_denied" {
                    automationDenied = true
                    problem = response.error
                } else if response.ok && arguments.first == "scan" { automationDenied = false }
                if let request = response.confirmationRequired {
                    if request.carId == carID && cars.contains(where: { $0.id == request.carId }) {
                        pendingRemoval = request
                    } else { failure = "The removal check returned a different car. Nothing else was removed." }
                }
                if !response.warnings.isEmpty {
                    // Storage-wide warnings must not disappear with one car.
                    problem = response.warnings.joined(separator: "\n")
                }
            } catch {
                failure = error.localizedDescription
                if (error as? BackendError)?.code == "automation_denied" {
                    automationDenied = true
                    problem = failure
                }
            }
            if let failure {
                if let carID {
                    carIssues[carID] = CarIssue(summary: removingCar ? "Car kept · tab not verified" : "Couldn’t open all tabs",
                                               detail: failure)
                } else if problem == nil { problem = failure }
                else if problem != failure { problem = [problem!, failure].joined(separator: "\n") }
            }
            busy = false
            activeCarID = nil
            removingCar = false
            self.activity = ""
        }
    }

    func reveal(_ car: ParkedCar) {
        guard !isPreview else { message = "Preview cars exist only in memory."; return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: car.filePath)])
    }
    func copyCommands(_ car: ParkedCar) {
        guard !isPreview else { message = "Preview session IDs are fictional; no commands were copied."; return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(car.commands, forType: .string)
        message = "Resume commands copied. Each command is for one tab."
    }
    func revealGarage() {
        guard !isPreview else { message = "Preview mode does not read or write your recovery files."; return }
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Car Park")
        if FileManager.default.fileExists(atPath: url.path) { NSWorkspace.shared.open(url) }
    }

    private func loadPreview() {
        let sample = PreviewFixtures.cars[0]
        switch PreviewMode.scene {
        case "ready", "window-review":
            windows = [PreviewFixtures.window]
            hasScanned = true
        case "parked", "details", "options":
            cars = [sample]
        case "multiple":
            cars = [sample.withStatus("restored", note: "Fictional live match."), PreviewFixtures.cars[1]]
        case "remove-confirm":
            cars = [sample.withStatus("restored", note: "Fictional previously opened window."), PreviewFixtures.cars[1]]
            previewUnverifiedIDs.insert(sample.id)
            pendingRemoval = RemovalRequest(carId: sample.id, token: "preview-only-" + sample.id,
                reason: "The saved tabs are not all confirmed open in iTerm.")
        case "permission":
            automationDenied = true
            problem = "Allow Free Parking to access iTerm in System Settings → Privacy & Security → Automation, then try again. Accessibility permission is not needed."
        case "blocked", "attention":
            windows = [PreviewFixtures.unsupportedWindow]
        case "saved-open":
            cars = [sample.withStatus("saved_open", note: "The close was cancelled. Recovery is saved; agents may already have been interrupted.")]

        case "restoring":
            cars = [sample.withStatus("restoring", note: "Opening conversations. The car is kept.")]
            busy = true
            activeCarID = sample.id
            activity = "Opening in iTerm…"
        case "returned":
            cars = [sample.withStatus("restored", note: "Sample conversations opened.")]
        case "remove-blocked":
            cars = [sample.withStatus("restored", note: "Sample conversations opened.")]
            carIssues[sample.id] = CarIssue(summary: "Car kept · tab not verified",
                                          detail: "One saved conversation is no longer identifiable in its returned tab. Open the car again, check your conversations, then try Remove car. The recovery file is kept.")
        case "partial", "recovery":
            cars = [sample.withStatus("attention", note: "One conversation could not be opened. The car and recovery file are kept.")]
            carIssues[sample.id] = CarIssue(summary: "1 tab didn’t open", detail: "The Playground folder could not be found. Your other two tabs may already be open. Restore the folder to its saved location, then reopen the car again. The car and recovery file are kept.")
        case "save-error":
            windows = [PreviewFixtures.window]
            problem = "Couldn’t save recovery. Your window was left open."
        case "no-windows":
            hasScanned = true
        default:
            break // An ordinary preview launch starts empty, never with random cars.
        }
        if ["details", "recovery", "attention", "window-review"].contains(PreviewMode.scene) {
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(400))
                if PreviewMode.scene == "details" {
                    selectedCar = cars.first
                } else if PreviewMode.scene == "recovery" {
                    recoveryCar = cars.first
                } else {
                    selectedWindow = windows.first
                }
            }
        }
    }
}
