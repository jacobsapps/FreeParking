import SwiftUI

struct GarageView: View {
    @EnvironmentObject private var garage: Garage
    var exporting = false
    private let columns = [GridItem(.adaptive(minimum: 148, maximum: 170), spacing: 14, alignment: .top)]

    var body: some View {
        ZStack {
            Asphalt().ignoresSafeArea()
            VStack(spacing: 0) {
                header.padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 18)
                if exporting { content }
                else { ScrollView { content } }
            }
        }
        .foregroundStyle(ParkingStyle.ink)
        .task { garage.load() }
        .sheet(item: $garage.selectedCar) { CarDetails(car: $0).environmentObject(garage) }
        .sheet(item: $garage.recoveryCar) { RecoveryDetails(car: $0).environmentObject(garage) }
        .sheet(item: $garage.selectedWindow) { WindowDetails(window: $0).environmentObject(garage) }
        .sheet(item: $garage.pendingRemoval) { RemovalConfirmation(request: $0).environmentObject(garage) }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 18) {
            if (garage.busy && garage.activeCarID == nil) || garage.problem != nil || !garage.message.isEmpty { notice }
            if garage.hasScanned, !garage.windows.isEmpty {
                HStack {
                    Text("iTerm · \(garage.windows.count) \(garage.windows.count == 1 ? "window" : "windows") · \(garage.windows.reduce(0) { $0 + $1.tabs.count }) tabs")
                        .font(.system(size: 12, weight: .medium))
                    Spacer()
                    Text("Read-only scan").font(.system(size: 10)).foregroundStyle(ParkingStyle.secondary)
                }
            }
            ForEach(Array(garage.windows.enumerated()), id: \.element.id) { index, window in
                WindowRow(window: window, number: index + 1)
            }
            if garage.cars.isEmpty { emptyLot }
            else {
                if garage.cars.count == 1, let car = garage.cars.first {
                    HStack(alignment: .top, spacing: 22) {
                        Spacer(minLength: 0)
                        NewParkingBay().frame(width: 164)
                        ParkedBay(car: car).frame(width: 164)
                        Spacer(minLength: 0)
                    }
                } else {
                    LazyVGrid(columns: columns, alignment: .center, spacing: 22) {
                        NewParkingBay()
                        ForEach(garage.cars) { ParkedBay(car: $0) }
                    }
                }
                if !garage.busy {
                    Text("Double-click a car to open in iTerm")
                        .font(.system(size: 11)).foregroundStyle(ParkingStyle.secondary)
                        .frame(maxWidth: .infinity).padding(.top, 3)
                }
            }
        }
        .padding(.horizontal, 20).padding(.bottom, 20)
        .frame(maxWidth: .infinity, maxHeight: exporting ? .infinity : nil, alignment: .topLeading)
    }

    private var header: some View {
        HStack(spacing: 9) {
            Button { garage.scan() } label: { ParkingMark().frame(width: 38, height: 38) }
                .buttonStyle(.plain).disabled(garage.busy || garage.pendingRemoval != nil)
                .help("Park my windows").accessibilityLabel("Park my windows")
            Text("FREE PARKING").font(ParkingStyle.signFont(28)).tracking(1.3)
            Spacer(minLength: 8)
            if garage.isPreview { SampleBadge() }
            Button { garage.scan() } label: {
                Image(systemName: "arrow.clockwise").font(.system(size: 14, weight: .medium))
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain).disabled(garage.busy)
            .help("Find iTerm windows").accessibilityLabel("Find iTerm windows")
            Button { garage.revealGarage() } label: {
                Image(systemName: "archivebox").frame(width: 22, height: 28)
            }
            .buttonStyle(.plain).help("Recovery files")
            .accessibilityLabel("Recovery files")
        }
    }

    private var notice: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: garage.problem != nil ? "exclamationmark.triangle" : garage.busy ? "arrow.triangle.2.circlepath" : "info.circle")
                .foregroundStyle(garage.problem != nil ? ParkingStyle.warning : ParkingStyle.yellow)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 9) {
                Text(garage.problem ?? (garage.busy ? garage.activity : garage.message))
                    .font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                if garage.automationDenied {
                    HStack {
                        Button("Open System Settings") { garage.openAutomationSettings() }
                            .buttonStyle(ParkingButtonStyle())
                        Button("Try again") { garage.scan() }
                            .buttonStyle(ParkingButtonStyle(prominent: true)).disabled(garage.busy)
                    }
                }
            }
            Spacer(minLength: 0)
            if !garage.busy {
                Button {
                    garage.problem = nil
                    garage.message = ""
                } label: { Image(systemName: "xmark").font(.system(size: 10, weight: .semibold)) }
                .buttonStyle(.plain).help("Dismiss message").accessibilityLabel("Dismiss message")
            }
        }
        .padding(11)
        .background(ParkingStyle.surface, in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(ParkingStyle.secondary.opacity(0.2)))
    }

    private var compactEmptyLot: Bool {
        !garage.windows.isEmpty || garage.automationDenied || garage.problem != nil || garage.busy || !garage.message.isEmpty
    }

    private var emptyLot: some View {
        VStack(spacing: 16) {
            PaintedParkingSpace(compact: compactEmptyLot)
                .frame(width: compactEmptyLot ? 96 : 164,
                       height: compactEmptyLot ? 90 : 176)
            Text(garage.hasScanned && garage.windows.isEmpty ? "No iTerm windows found" : "Nothing parked")
                .font(.system(size: 14, weight: .medium))
            if garage.windows.isEmpty && !garage.automationDenied {
                Button(garage.hasScanned ? "Try again" : "Park my windows") { garage.scan() }
                    .buttonStyle(ParkingButtonStyle(prominent: true)).disabled(garage.busy)
            }
        }
        .frame(maxWidth: .infinity).frame(height: garage.windows.isEmpty ? (compactEmptyLot ? 200 : 322) : 132)
    }
}

private struct NewParkingBay: View {
    @EnvironmentObject private var garage: Garage

    var body: some View {
        VStack(spacing: 8) {
            Color.clear.frame(height: 22)
            Button { garage.scan() } label: {
                VStack(spacing: 10) {
                    PaintedParkingSpace().frame(width: 120, height: 142)
                    Text("Park my windows").font(.system(size: 12, weight: .medium))
                        .foregroundStyle(ParkingStyle.ink)
                }
                .frame(maxWidth: .infinity).contentShape(Rectangle())
            }
            .buttonStyle(.plain).disabled(garage.busy || garage.pendingRemoval != nil)
            .help("Read iTerm windows before parking anything")
            .accessibilityLabel("Park my windows")
        }
        .frame(maxWidth: .infinity, alignment: .top)
    }
}

private struct WindowRow: View {
    @EnvironmentObject private var garage: Garage
    let window: LiveWindow
    let number: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(spacing: 12) {
                Image(systemName: "terminal").font(.system(size: 21))
                    .foregroundStyle(ParkingStyle.secondary).frame(width: 28)
                VStack(alignment: .leading, spacing: 3) {
                    Text(window.title.isEmpty ? "iTerm window \(number)" : window.title)
                        .font(.system(size: 13, weight: .medium)).lineLimit(1)
                    if !window.canPark {
                        Text("\(window.tabs.filter { !$0.issue.isEmpty }.count) tab needs attention")
                            .font(.system(size: 11)).foregroundStyle(ParkingStyle.warning)
                    }
                }
                Spacer(minLength: 8)
                Button("\(window.tabs.count) tabs ›") { garage.selectedWindow = window }
                    .buttonStyle(.plain).font(.system(size: 12))
                    .help("View every tab title and folder before parking")
                Button(window.canPark ? "Park" : "Review") { garage.park(window) }
                    .accessibilityIdentifier("park-window-" + window.id)
                    .buttonStyle(ParkingButtonStyle(prominent: true)).disabled(garage.busy)
            }
            // A recognisable glance at the live window, not a list of IDs.
            ForEach(window.tabs.prefix(2)) { tab in
                HStack(spacing: 10) {
                    Text(tab.displayTitle).font(.system(size: 11)).lineLimit(1)
                    Spacer(minLength: 10)
                    Text(tab.cwd.isEmpty ? "Folder unknown" : tab.folder)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(ParkingStyle.secondary).lineLimit(1)
                }
            }
        }
        .padding(12)
        .background(ParkingStyle.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(ParkingStyle.secondary.opacity(0.25)))
    }

}

private struct ParkedBay: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let car: ParkedCar
    @State private var hovered = false
    @State private var showingActions = false

    private var color: Color {
        let colors: [Color] = [Color(red: 0.38, green: 0.70, blue: 0.72), ParkingStyle.yellow,
                              Color(red: 0.83, green: 0.46, blue: 0.35)]
        return colors[car.id.utf8.reduce(0) { $0 + Int($1) } % colors.count]
    }
    private var working: Bool { garage.busy && garage.activeCarID == car.id }

    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Text(working ? (garage.removingCar ? "Checking…" : "Opening…") : car.hasReturned ? "Opened" : "")
                    .font(.system(size: 11, weight: .medium)).foregroundStyle(ParkingStyle.secondary)
                Spacer(minLength: 0)
                Button { showingActions.toggle() } label: {
                    Image(systemName: "ellipsis").frame(width: 26, height: 22)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).foregroundStyle(ParkingStyle.ink)
                .help("Show tabs or recovery").accessibilityLabel("Car options")
                .accessibilityIdentifier("options-car-" + car.id)
                .popover(isPresented: $showingActions, arrowEdge: .trailing) {
                    CarActions(car: car) { showingActions = false }.environmentObject(garage)
                }
            }
            // Keep the double-click target separate from all menu/remove buttons.
            VStack(spacing: 10) {
                ZStack {
                    BayMarkings().stroke(ParkingStyle.secondary.opacity(hovered ? 0.8 : 0.55),
                                         style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .padding(.horizontal, 6)
                    CarIllustration(color: color).frame(width: 68, height: 126)
                        .compositingGroup().shadow(color: .black.opacity(0.4), radius: 5, x: 4, y: 6)
                        .offset(y: hovered && !reduceMotion ? -4 : 0)
                }
                .frame(height: 142)
                TimelineView(.periodic(from: .now, by: 60)) { context in
                    RegistrationPlate(text: CarDateLabel.title(for: car.date, now: context.date))
                }
                Text("\(car.tabs.count) tabs · \(car.date.formatted(.dateTime.hour().minute()))")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(ParkingStyle.secondary)
            }
            .contentShape(Rectangle())
            .onTapGesture(count: 2) { garage.open(car) }
            .onHover { value in withAnimation(reduceMotion ? nil : .easeOut(duration: 0.18)) { hovered = value } }
            .help("Double-click to open in iTerm. If already open, bring it forward.")
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(car.date.formatted()), \(car.tabs.count) tabs, \(car.statusLabel)")
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("open-car-" + car.id)
            .accessibilityAction { garage.open(car) }
            .focusable()
            .onKeyPress(.return) { garage.open(car); return .handled }
            .onKeyPress(.space) { garage.open(car); return .handled }

            Text(car.displayTitle).font(.system(size: 11, weight: .medium))
                .lineLimit(1).help(car.displayTitle)
            if car.hasReturned {
                Button("Remove car") { garage.removeCar(car) }
                    .accessibilityIdentifier("remove-car-" + car.id)
                    .buttonStyle(ParkingButtonStyle())
                    .disabled(garage.busy)
                    .help("Remove immediately if the saved tabs match. Otherwise ask first. Recovery is always kept.")
                    .padding(.top, 3)
            }
            if let issue = garage.issue(for: car), !working {
                Button { garage.recoveryCar = car } label: {
                    VStack(spacing: 4) {
                        Label(issue.summary, systemImage: "exclamationmark.circle")
                            .font(.system(size: 11, weight: .medium))
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Details…").font(.system(size: 11)).underline()
                    }
                    .frame(maxWidth: .infinity)
                    .foregroundStyle(ParkingStyle.warning)
                }
                .buttonStyle(.plain).padding(.top, 4)
            }
        }
        .contextMenu {
            Button("Open in iTerm") { garage.open(car) }.disabled(garage.busy)
            Button("Show tabs") { garage.selectedCar = car }
            Button("Recovery…") { garage.recoveryCar = car }
            Divider()
            Button("Remove car…") { garage.removeCar(car) }.disabled(garage.busy)
        }
    }
}

/// The same small menu is used in the popover and the native design export.
struct CarActions: View {
    @EnvironmentObject private var garage: Garage
    let car: ParkedCar
    var close: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            action("Open in iTerm", icon: "arrow.up.forward") { garage.open(car) }
                .disabled(garage.busy)
            Divider().overlay(ParkingStyle.secondary.opacity(0.2)).padding(.vertical, 4)
            action("Show tabs", icon: "list.bullet") { garage.selectedCar = car }
            action("Recovery…", icon: "lifepreserver") { garage.recoveryCar = car }
            Divider().overlay(ParkingStyle.secondary.opacity(0.2)).padding(.vertical, 4)
            action("Remove car…", icon: "minus.circle") { garage.removeCar(car) }.disabled(garage.busy)
                .accessibilityIdentifier("menu-remove-car-" + car.id)
        }
        .padding(8).frame(width: 200)
        .foregroundStyle(ParkingStyle.ink).background(ParkingStyle.surface)
    }

    private func action(_ title: String, icon: String, perform: @escaping () -> Void) -> some View {
        Button {
            close()
            // Let the options popover dismiss before presenting a sheet.
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(150))
                perform()
            }
        } label: {
            Label(title, systemImage: icon).font(.system(size: 12))
                .frame(maxWidth: .infinity, alignment: .leading).padding(8)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
