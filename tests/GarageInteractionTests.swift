// UI-model checks only. Compile with FREE_PARKING_DEMO. No terminal adapters,
// session logs, recovery files, permissions prompts or window automation.
import Foundation

@main
struct GarageInteractionTests {
    @MainActor
    static func main() throws {
        precondition(PreviewMode.enabled)
        for operation in ["scan", "list"] {
            precondition(!QuitPolicy.mustWait(operation: operation, busy: true))
        }
        for operation in ["park", "park-all", "restore", "restore-all", "remove", "unarchive", "unknown"] {
            precondition(QuitPolicy.mustWait(operation: operation, busy: true))
            precondition(!QuitPolicy.mustWait(operation: operation, busy: false))
        }
        precondition(!QuitPolicy.mustWait(operation: nil, busy: false))
        precondition(QuitPolicy.mustWait(operation: nil, busy: true))
        let a = PreviewFixtures.cars[0].withStatus("restored", note: "Fictional match")
        let b = PreviewFixtures.cars[1]
        let garage = Garage()
        garage.load()
        garage.cars = [a, b]

        // Legacy recovery journals can record every launch and still say
        // "restoring" when only title readback failed. Do not report missing tabs.
        var pendingTitles = a.withStatus("restoring", note: "Restoring tab 3.")
        pendingTitles.restoredTargets = Dictionary(uniqueKeysWithValues: a.tabs.indices.map {
            (String($0), ParkedCar.RestoredTarget(windowId: "fixture-window", terminalId: "fixture-\($0)", titlePending: true))
        })
        precondition(pendingTitles.allLaunchesRecorded && pendingTitles.awaitingTabNames)
        precondition(pendingTitles.hasReturned)
        precondition(garage.issue(for: pendingTitles) == nil)
        precondition(pendingTitles.statusLabel == "Opened · awaiting check")
        precondition(pendingTitles.tabNamesHelp == "1. Update the website\n2. Refactor the command parser\n3. Sketch a small prototype")
        var partial = pendingTitles
        partial.restoredTargets?.removeValue(forKey: "2")
        precondition(!partial.allLaunchesRecorded && !partial.awaitingTabNames)
        precondition(garage.issue(for: partial)?.summary == "Opening incomplete")
        var ambiguous = pendingTitles
        ambiguous.pendingTab = 2
        precondition(!ambiguous.allLaunchesRecorded && !ambiguous.awaitingTabNames)
        var pendingBounds = pendingTitles
        pendingBounds.boundsPending = true
        precondition(pendingBounds.allLaunchesRecorded && !pendingBounds.awaitingTabNames)

        let failedParking = b.withStatus("attention", note: "Parking did not finish")
        precondition(garage.issue(for: failedParking)?.summary == "Parking incomplete")
        let acknowledgedFailure = pendingTitles.withStatus("attention", note: "Old failed acknowledgement")
        precondition(acknowledgedFailure.allLaunchesRecorded)
        precondition(acknowledgedFailure.statusLabel == "Opened · awaiting check")
        precondition(garage.issue(for: acknowledgedFailure) == nil)

        garage.removeCar(a)
        precondition(garage.cars.map(\.id) == [b.id])
        precondition(garage.archives.map(\.id) == [a.id])
        precondition(garage.archivePulse == 1 && garage.archiveAcknowledged)
        garage.removeCar(a) // Stale action cannot affect another car.
        precondition(garage.archivePulse == 1)
        garage.busy = true
        garage.removeCar(b)
        precondition(garage.cars.map(\.id) == [b.id])
        garage.busy = false
        garage.removeCar(b) // Unopened cars archive without any confirmation.
        precondition(garage.cars.isEmpty && garage.archives.count == 2)
        precondition(garage.archivePulse == 2)
        garage.bringBack(b)
        precondition(garage.cars.map(\.id) == [b.id])
        precondition(garage.cars[0].tabs.count == b.tabs.count)

        garage.cars = [a, b]
        garage.open(b)
        precondition(garage.cars.map(\.id) == [a.id])
        precondition(garage.archives.contains { $0.id == b.id })
        precondition(garage.departingAt[b.id] != nil)
        garage.bringBack(b)
        precondition(Set(garage.cars.map(\.id)) == Set([a.id, b.id]))
        precondition(garage.selectedCar == nil && garage.recoveryCar == nil)

        garage.scan()
        precondition(garage.windows.count == 1)
        garage.park(garage.windows[0])
        precondition(garage.cars.count == 3)
        precondition(Set(garage.cars.map(\.id)).count == 3)
        precondition(garage.cars.contains { $0.id == a.id } && garage.cars.contains { $0.id == b.id })
        precondition(garage.windows.isEmpty)

        // The primary actions work without first visiting a scan/review screen.
        let direct = Garage()
        direct.load()
        direct.parkAll()
        precondition(direct.cars.count == 1 && direct.selectedWindow == nil)
        direct.parkAll()
        precondition(direct.cars.count == 2)
        for car in direct.cars { direct.open(car) }
        precondition(direct.cars.isEmpty && direct.archives.count == 2)
        direct.windows = [PreviewFixtures.window, PreviewFixtures.unsupportedWindow]
        let before = direct.cars.count
        direct.parkAll()
        precondition(direct.cars.count == before && direct.problem != nil)
        direct.problem = nil
        direct.windows = [PreviewFixtures.window, PreviewFixtures.window]
        direct.parkAll()
        precondition(direct.cars.count == before + 2 && direct.windows.isEmpty)

        do {
            _ = try LocalBackend.call(["scan"])
            preconditionFailure("Preview reached a terminal adapter")
        } catch is BackendError { /* Expected: denied before helper lookup. */ }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let response = try decoder.decode(BackendResponse.self, from: Data(#"{"ok":true,"windows":[],"cars":[],"warnings":[],"verified_archived_ids":["sample-car"]}"#.utf8))
        precondition(response.verifiedArchivedIds == ["sample-car"])
        let denial = try decoder.decode(BackendResponse.self, from: Data(#"{"ok":false,"windows":[],"cars":[],"warnings":[],"error_code":"automation_denied","error":"Permission denied"}"#.utf8))
        precondition(denial.errorCode == "automation_denied")
        let journal = try decoder.decode(ParkedCar.self, from: Data(#"{"id":"fixture","created_at":"2026-10-08T09:00:00Z","status":"restoring","note":"Restoring tab 1.","file_path":"/fixture","tabs":[{"index":0,"title":"Actual tab name","terminal_id":"original","provider":"shell","session_id":"","cwd":"/fixture","issue":""}],"restored_targets":{"0":{"window_id":"returned","terminal_id":"returned-tab","title_pending":true}},"pending_tab":null,"bounds_pending":false}"#.utf8))
        precondition(journal.awaitingTabNames && journal.allLaunchesRecorded)
        precondition(journal.tabNamesHelp == "1. Actual tab name")
        var unverifiedV2 = pendingTitles
        unverifiedV2.returnVerified = false
        precondition(unverifiedV2.allLaunchesRecorded && !unverifiedV2.hasReturned)
        precondition(unverifiedV2.needsAttention && garage.issue(for: unverifiedV2) != nil)
        let preferences = try decoder.decode(BackendResponse.self, from: Data(#"{"ok":true,"windows":[],"cars":[],"warnings":[],"settings":{"auto_continue_interrupted":false,"continue_message":"continue now"}}"#.utf8))
        precondition(preferences.settings?.autoContinueInterrupted == false)
        precondition(preferences.settings?.continueMessage == "continue now")
        let fractional = try decoder.decode(ParkedCar.self, from: Data(#"{"id":"fixture","created_at":"2026-10-13T17:00:00.123456+00:00","status":"parked","note":"","file_path":"/fixture","tabs":[],"return_verified":false}"#.utf8))
        precondition(abs(fractional.date.timeIntervalSince1970 - 1791910800.123456) < 0.001)
        if let marker = CommandLine.arguments.firstIndex(of: "--backend-contract") {
            let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[marker + 1]))
            let replies = try decoder.decode([BackendResponse].self, from: data)
            precondition(replies.count == 3 && replies.allSatisfy(\.ok))
            precondition(replies[0].windows.count == 2 && replies[0].windows.allSatisfy(\.canPark))
            precondition(replies[1].cars.count == 1 && replies[1].cars[0].status == "parked")
            precondition(replies[1].cars[0].returnVerified == false)
            precondition(replies[1].cars[0].date > .distantPast)
            precondition(replies[2].cars.isEmpty && replies[2].archives?.count == 1)
            precondition(replies[2].archives?[0].returnVerified == true)
            precondition(replies[2].verifiedArchivedIds == [replies[1].cars[0].id])
        }
        print("UI-model checks passed: quit during refresh, mutation quit guard, immediate reversible archive, feedback, stale/busy guards, multiple cars, one-click parking, preview isolation.")
    }
}
