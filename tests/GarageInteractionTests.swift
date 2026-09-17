// UI-model checks only. Compile with FREE_PARKING_DEMO. No terminal adapters,
// session logs, recovery files, permissions prompts or window automation.
import Foundation

@main
struct GarageInteractionTests {
    @MainActor
    static func main() throws {
        precondition(PreviewMode.enabled)
        let a = PreviewFixtures.cars[0].withStatus("restored", note: "Fictional match")
        let b = PreviewFixtures.cars[1]
        let garage = Garage()
        garage.load()
        garage.cars = [a, b]

        garage.removeCar(a)
        precondition(garage.cars.map(\.id) == [b.id])
        precondition(garage.pendingRemoval == nil)

        garage.removeCar(b)
        let request = garage.pendingRemoval!
        precondition(request.carId == b.id && garage.cars.count == 1)
        garage.pendingRemoval = nil // Cancel does not remove anything.
        precondition(garage.cars.map(\.id) == [b.id])

        garage.removeCar(b)
        garage.confirmRemoval(RemovalRequest(carId: a.id, token: request.token, reason: "Wrong car"))
        precondition(garage.pendingRemoval?.carId == b.id)
        precondition(garage.cars.map(\.id) == [b.id])
        garage.confirmRemoval(request)
        precondition(garage.cars.isEmpty && garage.pendingRemoval == nil)

        garage.cars = [a, b]
        garage.open(b)
        precondition(garage.cars.count == 2)
        precondition(garage.cars.first { $0.id == b.id }!.hasReturned)
        precondition(garage.selectedCar == nil && garage.recoveryCar == nil)

        garage.scan()
        precondition(garage.windows.count == 1)
        garage.park(garage.windows[0])
        precondition(garage.cars.count == 3)
        precondition(Set(garage.cars.map(\.id)).count == 3)
        precondition(garage.cars.contains { $0.id == a.id } && garage.cars.contains { $0.id == b.id })
        precondition(garage.windows.isEmpty)

        do {
            _ = try LocalBackend.call(["scan"])
            preconditionFailure("Preview reached a terminal adapter")
        } catch is BackendError { /* Expected: denied before helper lookup. */ }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let response = try decoder.decode(BackendResponse.self, from: Data(#"{"ok":true,"windows":[],"cars":[],"warnings":[],"confirmation_required":{"car_id":"sample-car","token":"revision","reason":"Missing tabs"}}"#.utf8))
        precondition(response.confirmationRequired?.carId == "sample-car")
        precondition(response.confirmationRequired?.token == "revision")
        let denial = try decoder.decode(BackendResponse.self, from: Data(#"{"ok":false,"windows":[],"cars":[],"warnings":[],"error_code":"automation_denied","error":"Permission denied"}"#.utf8))
        precondition(denial.errorCode == "automation_denied" && denial.confirmationRequired == nil)
        print("UI-model checks passed: exact-car removal, cancellation, mismatched confirmation, multiple cars, another parking action, preview isolation.")
    }
}
