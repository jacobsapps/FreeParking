import SwiftUI

struct GarageView: View {
    @EnvironmentObject private var garage: Garage
    var exporting = false
    @State private var page = 0
    @State private var showingProblem = false
    private let pageSize = 2
    private var pageCount: Int { max(1, (garage.displayedCars.count + pageSize - 1) / pageSize) }
    private var visibleCars: [ParkedCar] { Array(garage.displayedCars.dropFirst(min(page, pageCount - 1) * pageSize).prefix(pageSize)) }

    var body: some View {
        ZStack {
            Asphalt().ignoresSafeArea()
            VStack(spacing: 10) {
                header
                HStack {
                    Text(garage.hasScanned ? "iTerm · \(garage.windows.count) \(garage.windows.count == 1 ? "window" : "windows") · \(garage.windows.reduce(0) { $0 + $1.tabs.count }) tabs" : "")
                        .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
                    Spacer()
                    if pageCount > 1 {
                        Button { page = max(0, page - 1) } label: { Image(systemName: "chevron.left") }
                            .disabled(page == 0)
                        Text("\(min(page + 1, pageCount)) / \(pageCount)").font(.system(size: 11, design: .monospaced))
                        Button { page = min(pageCount - 1, page + 1) } label: { Image(systemName: "chevron.right") }
                            .disabled(page >= pageCount - 1)
                    }
                }
                .buttonStyle(.plain)
                if garage.displayedCars.isEmpty {
                    NewParkingBay(large: true)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    HStack(alignment: .top, spacing: 14) {
                        NewParkingBay().frame(width: 138)
                        ForEach(visibleCars) { ParkedBay(car: $0).frame(maxWidth: 177) }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
                }
                status
            }
            .padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 18)
        }
        .foregroundStyle(ParkingStyle.ink)
        .task { garage.load() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            // A CLI may need more than a few seconds to publish its identity.
            // Recheck on return to the app; no daemon or continuous polling.
            if !garage.isPreview { garage.scan() }
        }
        .onChange(of: garage.displayedCars.count) { _, _ in page = min(page, pageCount - 1) }
        .sheet(isPresented: $garage.showingArchive) { ArchiveView().environmentObject(garage) }
        .sheet(item: $garage.selectedCar) { CarDetails(car: $0).environmentObject(garage) }
        .sheet(item: $garage.recoveryCar) { RecoveryDetails(car: $0).environmentObject(garage) }
        .sheet(item: $garage.selectedWindow) { WindowDetails(window: $0).environmentObject(garage) }
        .sheet(item: $garage.pendingRemoval) { RemovalConfirmation(request: $0).environmentObject(garage) }
        .sheet(isPresented: $showingProblem) {
            VStack(alignment: .leading, spacing: 16) {
                Text("Details").font(.system(size: 20, weight: .semibold))
                ScrollView { Text(garage.problem ?? garage.message).font(.system(size: 13)).frame(maxWidth: .infinity, alignment: .leading) }
                    .frame(maxHeight: 240)
                HStack {
                    if garage.automationDenied {
                        Button("Open System Settings") { garage.openAutomationSettings() }
                    }
                    Spacer()
                    Button("Done") { showingProblem = false }
                }
            }.padding(22).frame(width: 470)
        }
    }

    private var header: some View {
        HStack(spacing: 9) {
            ParkingMark().frame(width: 38, height: 38)
            Text("FREE PARKING").font(ParkingStyle.signFont(28)).tracking(1.3)
            Spacer(minLength: 8)
            if garage.isPreview { SampleBadge() }
            Button { garage.scan() } label: { Image(systemName: "arrow.clockwise").frame(width: 28, height: 28) }
                .buttonStyle(.plain).disabled(garage.busy).help("Refresh window count")
                .accessibilityLabel("Refresh window count")
            Button { garage.showingArchive = true } label: { Image(systemName: "archivebox").frame(width: 22, height: 28) }
                .buttonStyle(.plain).help("Archived cars").accessibilityLabel("Archived cars")
                .accessibilityIdentifier("show-archive")
        }
        .padding(.bottom, 2)
    }

    private var status: some View {
        HStack(spacing: 7) {
            if garage.busy {
                ProgressView().controlSize(.small)
                Text(garage.activity).lineLimit(2)
            } else if garage.problem != nil {
                Image(systemName: "exclamationmark.triangle").foregroundStyle(ParkingStyle.warning)
                Text(garage.problem ?? "").lineLimit(2).foregroundStyle(ParkingStyle.warning)
                Button("Details") { showingProblem = true }.buttonStyle(.plain).underline()
            } else if !garage.message.isEmpty {
                Text(garage.message).lineLimit(2)
                Button("Details") { showingProblem = true }.buttonStyle(.plain).underline()
            }
        }
        .font(.system(size: 11)).foregroundStyle(ParkingStyle.secondary)
        .frame(maxWidth: .infinity, minHeight: 30)
    }
}

private struct NewParkingBay: View {
    @EnvironmentObject private var garage: Garage
    var large = false
    var body: some View {
        VStack(spacing: 13) {
            if !large { Color.clear.frame(height: 22) }
            Button { garage.parkAll() } label: {
                PaintedParkingSpace().frame(width: large ? 144 : 112, height: large ? 152 : 106)
            }
            .buttonStyle(.plain).disabled(garage.busy || garage.pendingRemoval != nil)
            .help("Save and close my iTerm windows").accessibilityLabel("Park my windows")
            .accessibilityIdentifier("park-bay")
            Button("Park my windows") { garage.parkAll() }
                .buttonStyle(ParkingButtonStyle(prominent: true))
                .disabled(garage.busy || garage.pendingRemoval != nil)
                .accessibilityIdentifier("park-all-windows")
            if !large { Color.clear.frame(height: 40) }
        }
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
        VStack(spacing: 6) {
            HStack {
                Text(working ? (garage.removingCar ? "Checking…" : "Opening…") : car.hasReturned ? "Not yet verified" : "")
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
            // One click opens. The options menu remains a separate target.
            VStack(spacing: 6) {
                ZStack {
                    BayMarkings().stroke(ParkingStyle.secondary.opacity(hovered ? 0.8 : 0.55),
                                         style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .padding(.horizontal, 6)
                    AnimatedParkedCar(color: color, arriving: garage.arrivingAt[car.id],
                                      departing: garage.departingAt[car.id], hovered: hovered)
                }
                .frame(height: 106)
                TimelineView(.periodic(from: .now, by: 60)) { context in
                    RegistrationPlate(text: CarDateLabel.title(for: car.date, now: context.date))
                }
                Text("\(car.tabs.count) tabs · \(car.date.formatted(.dateTime.hour().minute()))")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(ParkingStyle.secondary)
            }
            .contentShape(Rectangle())
            .onTapGesture(count: 1) { garage.open(car) }
            .onHover { value in withAnimation(reduceMotion ? nil : .easeOut(duration: 0.18)) { hovered = value } }
            .help("Click to reopen this window in iTerm. If already open, bring it forward.")
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
                Button("Check again") { garage.open(car) }
                    .accessibilityIdentifier("check-car-" + car.id)
                    .buttonStyle(ParkingButtonStyle())
                    .disabled(garage.busy)
                    .help("Keep this car until all saved tabs can be identified. Click to check again.")
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

/// Only the illustration moves: data is already safely saved or archived before
/// these purely decorative manoeuvres begin. Reduced Motion skips the journey.
private struct AnimatedParkedCar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let color: Color
    let arriving: Date?
    let departing: Date?
    let hovered: Bool
    @State private var animating = false

    private var start: Date? { departing ?? arriving }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60, paused: !animating || reduceMotion)) { context in
            let elapsed = start.map { context.date.timeIntervalSince($0) } ?? 10
            let pose = CarManoeuvre.pose(at: elapsed, departing: departing != nil)
            CarIllustration(color: color).frame(width: 54, height: 98)
                .compositingGroup().shadow(color: .black.opacity(0.4), radius: 5, x: 4, y: 6)
                .rotationEffect(.degrees(reduceMotion ? 0 : pose.angle))
                .offset(x: reduceMotion ? 0 : pose.x,
                        y: reduceMotion ? 0 : pose.y + (hovered && !animating ? -4 : 0))
                .opacity(reduceMotion ? (departing == nil ? 1 : 0) : pose.opacity)
        }
        .task(id: start) {
            guard let start, Date().timeIntervalSince(start) < 1.8 else { return }
            animating = true
            try? await Task.sleep(for: .milliseconds(1800))
            animating = false
        }
        .allowsHitTesting(false)
    }
}

struct ArchiveView: View {
    @EnvironmentObject private var garage: Garage
    var exporting = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Archived cars").font(.system(size: 21, weight: .semibold))
                Spacer()
                Button("Done") { garage.showingArchive = false }.keyboardShortcut(.cancelAction)
            }
            Text("Your recovery backups stay here, just in case.")
                .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
            if garage.archives.isEmpty {
                Label("No archived cars yet", systemImage: "archivebox")
                    .foregroundStyle(ParkingStyle.secondary).frame(maxWidth: .infinity, minHeight: 120)
            } else {
                if exporting { rows }
                else { ScrollView { rows }.frame(maxHeight: 250) }
            }
            Button("Show recovery files in Finder") { garage.revealGarage() }
                .font(.system(size: 11)).buttonStyle(.plain).foregroundStyle(ParkingStyle.secondary)
        }
        .padding(22).frame(width: 480).background(ParkingStyle.background)
        .foregroundStyle(ParkingStyle.ink)
    }

    private var rows: some View {
        VStack(spacing: 10) {
            ForEach(garage.archives) { car in
                HStack(spacing: 14) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(car.displayTitle).font(.system(size: 13, weight: .medium)).lineLimit(1)
                        Text("\(car.date.formatted(date: .abbreviated, time: .shortened)) · \(car.tabs.count) tabs")
                            .font(.system(size: 11)).foregroundStyle(ParkingStyle.secondary)
                    }
                    Spacer(minLength: 8)
                    Button("Bring back car") { garage.bringBack(car) }
                        .buttonStyle(ParkingButtonStyle()).disabled(garage.busy)
                        .accessibilityIdentifier("unarchive-car-" + car.id)
                }
                .padding(12).background(ParkingStyle.surface, in: RoundedRectangle(cornerRadius: 9))
            }
        }
    }
}
