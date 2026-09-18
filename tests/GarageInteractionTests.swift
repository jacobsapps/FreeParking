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
        print("UI-model checks passed: quit during refresh, mutation quit guard, immediate reversible archive, feedback, stale/busy guards, multiple cars, one-click parking, preview isolation.")
    }
}
