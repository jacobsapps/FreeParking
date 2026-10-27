import SwiftUI

struct GarageView: View {
    @EnvironmentObject private var garage: Garage
    var exporting = false
    @State private var page = 0
    @State private var showingProblem = false
    @State private var showingWindows = false
    @State private var showingSettings = false
    private let pageSize = 2
    private var hasUnresolvedTabs: Bool { garage.windows.contains { !$0.canPark && !$0.canConfirm } }
    private var pageCount: Int { max(1, (garage.displayedCars.count + pageSize - 1) / pageSize) }
    private var visibleCars: [ParkedCar] { Array(garage.displayedCars.dropFirst(min(page, pageCount - 1) * pageSize).prefix(pageSize)) }

    var body: some View {
        ZStack {
            Asphalt().ignoresSafeArea()
            VStack(spacing: 10) {
                header
                HStack {
                    Text(garage.hasScanned ? "iTerm, \(garage.windows.count) \(garage.windows.count == 1 ? "window" : "windows"), \(garage.windows.reduce(0) { $0 + $1.tabs.count }) tabs" : "")
                        .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
                    Spacer()
                    if !garage.windows.isEmpty {
                        Button(hasUnresolvedTabs ? "Review unidentified tabs" : "Review windows") { showingWindows = true }
                            .foregroundStyle(hasUnresolvedTabs ? ParkingStyle.warning : ParkingStyle.secondary)
                    }
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
                        ForEach(visibleCars) { ParkedBay(car: $0).frame(maxWidth: 220) }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
                }
                status
            }
            .padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 18)
        }
        .foregroundStyle(ParkingStyle.ink)
        .task { garage.load() }
        .onChange(of: garage.displayedCars.count) { _, _ in page = min(page, pageCount - 1) }
        .sheet(isPresented: $garage.showingArchive) { ArchiveView().environmentObject(garage) }
        .sheet(item: $garage.selectedCar) { CarDetails(car: $0).environmentObject(garage) }
        .sheet(item: $garage.recoveryCar) { RecoveryDetails(car: $0).environmentObject(garage) }
        .sheet(item: $garage.selectedWindow) { WindowDetails(window: $0).environmentObject(garage) }
        .sheet(isPresented: $showingWindows) { WindowReview().environmentObject(garage) }
        .sheet(item: $garage.pendingConfirmation) { confirmation in
            ResumeConfirmationView(confirmation: confirmation).environmentObject(garage)
        }
        .sheet(isPresented: $showingSettings) { ContinuationSettings(value: garage.preferences).environmentObject(garage) }
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
            if LocalBackend.testScope != nil {
                Text("TEST WINDOW").font(.system(size: 10, weight: .bold))
                    .foregroundStyle(ParkingStyle.yellow)
            }
            Button { garage.scan() } label: { Image(systemName: "arrow.clockwise").frame(width: 28, height: 28) }
                .buttonStyle(.plain).disabled(garage.busy).help("Refresh window count")
                .accessibilityLabel("Refresh window count")
            Button { showingSettings = true } label: { Image(systemName: "gearshape").frame(width: 28, height: 28) }
                .buttonStyle(.plain).disabled(garage.busy).help("Continuation settings")
                .accessibilityLabel("Continuation settings")
            ArchiveButton()
        }
        .padding(.bottom, 2)
    }

    private var status: some View {
        HStack(spacing: 7) {
            if garage.busy {
                ProgressView().controlSize(.small)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(garage.activity + (garage.operationStartedAt.map { " · \(max(0, Int(context.date.timeIntervalSince($0))))s" } ?? ""))
                        .lineLimit(2)
                }
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
            .buttonStyle(.plain).disabled(garage.busy)
            .help("Save and close my iTerm windows").accessibilityLabel("Park my windows")
            .accessibilityIdentifier("park-bay")
            Button("Park my windows") { garage.parkAll() }
                .buttonStyle(ParkingButtonStyle(prominent: true))
                .disabled(garage.busy)
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
                Text(working ? (garage.removingCar ? "Archiving…" : "Opening…") : car.hasReturned ? "Opened, awaiting check" : "")
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
                Text("\(car.tabs.count) tabs, \(car.date.formatted(.dateTime.hour().minute()))")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(ParkingStyle.secondary)
            }
            .contentShape(Rectangle())
            .onTapGesture(count: 1) { garage.open(car) }
            .onHover { value in withAnimation(reduceMotion ? nil : .easeOut(duration: 0.18)) { hovered = value } }
            .help(car.tabNamesHelp + (car.canFinishParking ? "\n\nClick to finish parking without reopening any tabs." : car.isChecking ? "\n\nClick to check this saved window’s status without repeating commands." : "\n\nClick to reopen this window in iTerm."))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(car.date.formatted()), \(car.tabs.count) tabs, \(car.statusLabel)")
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("open-car-" + car.id)
            .accessibilityAction { garage.open(car) }
            .focusable()
            .onKeyPress(.return) { garage.open(car); return .handled }
            .onKeyPress(.space) { garage.open(car); return .handled }

            Button { garage.selectedCar = car } label: {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(car.tabs.prefix(8))) { tab in
                        Text("\(tab.index + 1). \(tab.displayTitle)").font(.system(size: 11, weight: .medium)).lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if car.tabs.count > 8 { Text("+ \(car.tabs.count - 8) more saved tabs").font(.system(size: 11)) }
                    Text("Tab details and permissions")
                        .font(.system(size: 10)).underline().foregroundStyle(ParkingStyle.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain).help(car.tabNamesHelp)
            .accessibilityIdentifier("show-tabs-car-" + car.id)
            if let progress = car.partialRestoreLabel {
                Text(progress).font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(ParkingStyle.warning)
                if !working && !car.isChecking {
                    Button("Open remaining \(car.tabs.count - car.openedTabCount) tabs") { garage.open(car) }
                        .buttonStyle(ParkingButtonStyle()).disabled(garage.busy)
                }
            }
            if car.canFinishParking, !working {
                Button("Finish parking") { garage.open(car) }
                    .buttonStyle(ParkingButtonStyle()).disabled(garage.busy)
                    .help("Complete parking this saved window without reopening any tabs.")
            }
            if car.isChecking, !working {
                Button { garage.open(car) } label: {
                    Label("Check confirmation status", systemImage: "clock")
                        .font(.system(size: 11, weight: .medium))
                }
                .buttonStyle(.plain).foregroundStyle(ParkingStyle.secondary)
                .help(car.note.isEmpty ? "Recovery is saved. Checking never repeats an unconfirmed command." : car.note)
                .disabled(garage.busy)
            }
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
            Button(car.actionLabel) { garage.open(car) }.disabled(garage.busy)
            Button("Show tabs") { garage.selectedCar = car }
            Button("Recovery…") { garage.recoveryCar = car }
            Divider()
            Button("Archive car") { garage.removeCar(car) }.disabled(garage.busy)
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
            action(car.actionLabel, icon: "arrow.up.forward") { garage.open(car) }
                .disabled(garage.busy)
            Divider().overlay(ParkingStyle.secondary.opacity(0.2)).padding(.vertical, 4)
            action("Show tabs", icon: "list.bullet") { garage.selectedCar = car }
            action("Recovery…", icon: "lifepreserver") { garage.recoveryCar = car }
            Divider().overlay(ParkingStyle.secondary.opacity(0.2)).padding(.vertical, 4)
            action("Archive car", icon: "minus.circle") { garage.removeCar(car) }.disabled(garage.busy)
                .accessibilityIdentifier("menu-archive-car-" + car.id)
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
                        Text("\(car.date.formatted(date: .abbreviated, time: .shortened)), \(car.tabs.count) tabs")
                            .font(.system(size: 11)).foregroundStyle(ParkingStyle.secondary)
                    }
                    Spacer(minLength: 8)
                    Button(car.returnVerified == true ? "View saved tabs" : "Bring back car") {
                        if car.returnVerified == true {
                            garage.showingArchive = false
                            garage.selectedCar = car
                        } else { garage.bringBack(car) }
                    }
                        .buttonStyle(ParkingButtonStyle()).disabled(garage.busy)
                        .accessibilityIdentifier("unarchive-car-" + car.id)
                }
                .padding(12).background(ParkingStyle.surface, in: RoundedRectangle(cornerRadius: 9))
            }
        }
    }
}


private struct ArchiveButton: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Button { garage.showingArchive = true } label: {
            Image(systemName: garage.archiveAcknowledged ? "archivebox.fill" : "archivebox")
                .foregroundStyle(garage.archiveAcknowledged ? ParkingStyle.yellow : ParkingStyle.ink)
                .symbolEffect(.bounce, value: reduceMotion ? 0 : garage.archivePulse)
                .frame(width: 26, height: 28)
                .overlay(alignment: .topTrailing) {
                    if garage.archiveAcknowledged {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(ParkingStyle.darkInk, ParkingStyle.yellow)
                            .offset(x: 5, y: -3)
                            .transition(reduceMotion ? .opacity : .scale.combined(with: .opacity))
                    }
                }
        }
        .buttonStyle(.plain).help("Archived cars, recovery kept")
        .accessibilityLabel("Archived cars")
        .accessibilityValue(garage.archiveAcknowledged ? "Car archived" : "")
        .accessibilityIdentifier("show-archive")
    }
}


private struct WindowReview: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Your open windows").font(.system(size: 21, weight: .semibold))
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    ForEach(garage.windows) { window in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text(window.title).font(.headline).lineLimit(1)
                                Spacer()
                                Text("\(window.tabs.count) tabs").foregroundStyle(ParkingStyle.secondary)
                            }
                            ForEach(window.tabs) { tab in
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("\(tab.index + 1). \(tab.displayTitle)").font(.system(size: 13)).lineLimit(2)
                                    if !tab.issue.isEmpty { Text(tab.issue).font(.system(size: 12)).foregroundStyle(ParkingStyle.warning) }
                                }
                            }
                            Button("Park this window") { dismiss(); garage.park(window) }
                                .buttonStyle(ParkingButtonStyle()).disabled((!window.canPark && !window.canConfirm) || garage.busy)
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.frame(maxHeight: 350)
            Text("Unidentified tabs stay visible and remain open. No conversation is guessed.")
                .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
            HStack { Spacer(); Button("Done") { dismiss() }.buttonStyle(ParkingButtonStyle(prominent: true)) }
        }.padding(22).frame(width: 540).foregroundStyle(ParkingStyle.ink).background(ParkingStyle.background)
    }
}

private struct ContinuationSettings: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.dismiss) private var dismiss
    @State var value: ParkingPreferences
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Resume interrupted tasks").font(.system(size: 21, weight: .semibold))
            Toggle("Automatically continue tasks Free Parking stopped", isOn: $value.autoContinueInterrupted)
            TextField("Continuation message", text: $value.continueMessage).textFieldStyle(.roundedBorder)
            Text("Sent once on reopening only when a running task was confirmed interrupted. Idle sessions are left alone.")
                .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
            Text("Permissions are saved per tab when the agent reports them. Otherwise Codex reopens read-only and Claude in plan mode. Free Parking never gives every window full access.")
                .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.buttonStyle(ParkingButtonStyle())
                Button("Save") { garage.savePreferences(value); dismiss() }.buttonStyle(ParkingButtonStyle(prominent: true))
                    .disabled(value.continueMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(22).frame(width: 500).foregroundStyle(ParkingStyle.ink).background(ParkingStyle.background)
    }
}

struct ResumeConfirmationView: View {
    @EnvironmentObject private var garage: Garage
    @Environment(\.dismiss) private var dismiss
    let confirmation: ResumeConfirmation
    var exporting = false
    @State private var confirmed = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if garage.isPreview { SampleBadge() }
            Text("Confirm resumed sessions").font(.system(size: 21, weight: .semibold))
            Text("Codex still reports an old terminal endpoint. These are the exact saved sessions used to launch the tabs below—not a title-based match.")
                .font(.system(size: 13))
            if exporting { entries } else { ScrollView { entries }.frame(height: min(210, CGFloat(confirmation.entries.count) * 100)) }
            Text("If you switched conversations inside one of these tabs, cancel. Free Parking cannot detect that from the launch command.")
                .font(.system(size: 12)).foregroundStyle(ParkingStyle.secondary)
            Button { confirmed.toggle() } label: {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: confirmed ? "checkmark.square.fill" : "square")
                        .foregroundStyle(confirmed ? ParkingStyle.yellow : ParkingStyle.secondary)
                    Text("These are the conversations currently shown in those tabs")
                }.font(.system(size: 12))
            }.buttonStyle(.plain).accessibilityValue(confirmed ? "Checked" : "Unchecked")
            HStack {
                Spacer()
                Button("Cancel") { garage.pendingConfirmation = nil; dismiss() }.buttonStyle(ParkingButtonStyle())
                Button("Confirm and park") { garage.confirmParking(confirmation); dismiss() }
                    .buttonStyle(ParkingButtonStyle(prominent: true)).disabled(!confirmed || garage.busy)
            }
        }.padding(22).frame(width: 540).foregroundStyle(ParkingStyle.ink).background(ParkingStyle.background)
    }
    private var entries: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(confirmation.entries) { entry in
                VStack(alignment: .leading, spacing: 4) {
                    Text("Window \(entry.windowNumber), tab \(entry.tabNumber), \(entry.title)").font(.system(size: 13, weight: .semibold))
                    Text(entry.name).font(.system(size: 13))
                    Text(entry.cwd).font(.system(size: 11)).foregroundStyle(ParkingStyle.secondary)
                    Text(entry.sessionId).font(.system(size: 10, design: .monospaced)).foregroundStyle(ParkingStyle.secondary).textSelection(.enabled)
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}
