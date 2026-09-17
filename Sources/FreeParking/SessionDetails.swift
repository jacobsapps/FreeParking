import SwiftUI

/// Inspection is optional and never interrupts the normal open/remove journey.
struct CarDetails: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.dismiss) private var dismiss
    let car: ParkedCar
    var exporting = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Saved tabs").font(.system(size: 20, weight: .semibold))
                    Text("\(car.tabs.count) tabs · \(car.date.formatted(date: .abbreviated, time: .shortened))")
                        .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
                }
                Spacer()
                if garage.isPreview { SampleBadge() }
            }
            SessionList(tabs: car.tabs, exporting: exporting)
            HStack {
                Spacer()
                Button("Done") { dismiss() }.buttonStyle(ParkingButtonStyle(prominent: true))
            }
        }
        .padding(22).frame(width: 520)
        .foregroundStyle(ParkingStyle.ink).background(ParkingStyle.background)
    }
}

struct RecoveryDetails: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.dismiss) private var dismiss
    let car: ParkedCar

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("Recovery").font(.system(size: 20, weight: .semibold))
                Spacer()
                if garage.isPreview { SampleBadge() }
            }
            Text(garage.issue(for: car)?.detail ?? "Your saved folders and session IDs are kept in a local recovery file. Click the car to reopen its window in iTerm.")
                .font(.system(size: 13)).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Copy resume commands") { garage.copyCommands(car) }
                    .buttonStyle(ParkingButtonStyle())
                Button("Recovery file") { garage.reveal(car) }.buttonStyle(ParkingButtonStyle())
                Spacer()
                Button("Done") { dismiss() }.buttonStyle(ParkingButtonStyle(prominent: true))
            }
        }
        .padding(22).frame(width: 520)
        .foregroundStyle(ParkingStyle.ink).background(ParkingStyle.background)
    }
}

struct WindowDetails: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.dismiss) private var dismiss
    let window: LiveWindow
    var exporting = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text(window.canPark ? "\(window.tabs.count) tabs to park" : "One tab needs attention")
                    .font(.system(size: 20, weight: .semibold))
                Spacer()
                if garage.isPreview { SampleBadge() }
            }
            if !window.title.isEmpty {
                Text(window.title).font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
            }
            SessionList(tabs: window.tabs, exporting: exporting)
            Text(window.canPark ? "Save recovery first. Then interrupt agents and ask iTerm to close."
                               : "This window stays open until every tab can be saved.")
                .font(.system(size: 12)).foregroundStyle(window.canPark ? ParkingStyle.secondary : ParkingStyle.warning)
            HStack {
                Spacer()
                Button("Done") { dismiss() }.buttonStyle(ParkingButtonStyle())
                Button("Park") { garage.park(window) }
                    .buttonStyle(ParkingButtonStyle(prominent: true))
                    .disabled(!window.canPark || garage.busy)
            }
        }
        .padding(22).frame(width: 520)
        .foregroundStyle(ParkingStyle.ink).background(ParkingStyle.background)
    }
}

private struct SessionList: View {
    let tabs: [AgentTab]
    var exporting = false

    var body: some View {
        if exporting { rows }
        else { ScrollView { rows }.frame(maxHeight: 250) }
    }

    private var rows: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(tabs) { tab in
                HStack(alignment: .top, spacing: 12) {
                    Text(String(format: "%02d", tab.index + 1))
                        .font(.system(size: 11, design: .monospaced)).foregroundStyle(ParkingStyle.secondary)
                        .padding(.top, 2)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(tab.displayTitle).font(.system(size: 13, weight: .medium))
                        if tab.issue.isEmpty {
                            Text("\(tab.label) · \(tab.shortPath)")
                                .font(.system(size: 11)).foregroundStyle(ParkingStyle.secondary)
                        } else {
                            if !tab.cwd.isEmpty {
                                Text(tab.shortPath).font(.system(size: 11)).foregroundStyle(ParkingStyle.secondary)
                            }
                            Text(tab.issue).font(.system(size: 12)).foregroundStyle(ParkingStyle.warning)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 12)
                Divider().overlay(ParkingStyle.secondary.opacity(0.2))
            }
        }
        .textSelection(.enabled)
    }
}


struct RemovalConfirmation: View {
    @EnvironmentObject private var garage: Garage
    let request: RemovalRequest

    private var car: ParkedCar? { garage.cars.first { $0.id == request.carId } }

    var body: some View {
        VStack(alignment: .leading, spacing: 17) {
            HStack {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 24)).foregroundStyle(ParkingStyle.warning)
                Text("Are you sure?").font(.system(size: 20, weight: .semibold))
                Spacer()
                if garage.isPreview { SampleBadge() }
            }
            if let car {
                VStack(alignment: .leading, spacing: 4) {
                    Text(car.displayTitle).font(.system(size: 14, weight: .semibold))
                    Text("\(car.date.formatted(date: .abbreviated, time: .shortened)) · \(car.tabs.count) tabs")
                        .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
                }
            }
            Text("This car’s saved tabs aren’t all verified open in iTerm. Remove it anyway?")
                .font(.system(size: 13)).fixedSize(horizontal: false, vertical: true)
            Text("The recovery file will be kept. No terminal windows will be closed.")
                .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Keep car") { garage.pendingRemoval = nil }
                    .accessibilityIdentifier("keep-car-" + request.carId)
                    .buttonStyle(ParkingButtonStyle(prominent: true))
                    .keyboardShortcut(.cancelAction)
                Button("Remove anyway", role: .destructive) { garage.confirmRemoval(request) }
                    .accessibilityIdentifier("confirm-remove-car-" + request.carId)
                    .buttonStyle(ParkingButtonStyle()).disabled(garage.busy || car == nil)
            }
        }
        .padding(22).frame(width: 470)
        .foregroundStyle(ParkingStyle.ink).background(ParkingStyle.background)
        .interactiveDismissDisabled(garage.busy)
    }
}
