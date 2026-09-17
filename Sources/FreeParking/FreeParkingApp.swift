import AppKit
import SwiftUI

enum GarageLayout {
    static let width: CGFloat = 560
    static let height: CGFloat = 460
}

@main
struct FreeParkingApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var garage = Garage()

    var body: some Scene {
        Window("Free Parking", id: "free-parking-v1") {
            GarageView()
                .environmentObject(garage)
                .preferredColorScheme(.dark)
                .frame(minWidth: 520, minHeight: 420)
                .onAppear {
                    delegate.garage = garage
                    delegate.configurePreviewCapture()
                }
        }
        .defaultSize(width: GarageLayout.width, height: GarageLayout.height)
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("Garage") {
                Button("Refresh iTerm Windows") { garage.scan() }
                    .keyboardShortcut("r")
                    .disabled(garage.busy)
                Button("Show Recovery Files") { garage.revealGarage() }
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var garage: Garage?
    private var captureConfigured = false

    func configurePreviewCapture() {
        guard PreviewMode.enabled, !captureConfigured else { return }
        captureConfigured = true
        let prefix = "--preview-metadata="
        guard let argument = ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix(prefix) }) else { return }
        let destination = String(argument.dropFirst(prefix.count))
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(350))
            guard let window = NSApp.windows.first(where: { $0.canBecomeMain && $0.isVisible }) else { return }
            window.setContentSize(NSSize(width: GarageLayout.width, height: GarageLayout.height))
            window.center()
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            try? await Task.sleep(for: .seconds(2))
            let imagePrefix = "--preview-image="
            if let imageArgument = ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix(imagePrefix) }) {
                let imagePath = String(imageArgument.dropFirst(imagePrefix.count))
                // Export the same SwiftUI components and model shown by this
                // running app. ImageRenderer does not record the desktop.
                if let garage {
                    let content: AnyView
                    if PreviewMode.scene == "remove-confirm", let request = garage.pendingRemoval {
                        content = AnyView(RemovalConfirmation(request: request).environmentObject(garage))
                    } else if PreviewMode.scene == "details", let car = garage.cars.first {
                        content = AnyView(CarDetails(car: car, exporting: true).environmentObject(garage)
                            .background(Color(nsColor: .windowBackgroundColor)))
                    } else if PreviewMode.scene == "recovery", let car = garage.cars.first {
                        content = AnyView(RecoveryDetails(car: car).environmentObject(garage)
                            .background(Color(nsColor: .windowBackgroundColor)))
                    } else if PreviewMode.scene == "options", let car = garage.cars.first {
                        content = AnyView(CarActions(car: car).environmentObject(garage))
                    } else if ["attention", "window-review"].contains(PreviewMode.scene), let window = garage.windows.first {
                        content = AnyView(WindowDetails(window: window, exporting: true).environmentObject(garage)
                            .background(Color(nsColor: .windowBackgroundColor)))
                    } else {
                        content = AnyView(GarageView(exporting: true).environmentObject(garage)
                            .frame(width: GarageLayout.width, height: GarageLayout.height))
                    }
                    let renderer = ImageRenderer(content: content.environment(\.colorScheme, .dark))
                    renderer.scale = window.backingScaleFactor
                    if let image = renderer.cgImage,
                       let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) {
                        try? png.write(to: URL(fileURLWithPath: imagePath), options: .atomic)
                    }
                }
            }
            // Own-window identity only: no enumeration of terminals or other apps.
            let metadata: [String: Any] = ["windowId": window.windowNumber,
                                           "pid": ProcessInfo.processInfo.processIdentifier,
                                           "scene": PreviewMode.scene,
                                           "liveBackendEnabled": false,
                                           "captureMethod": "SwiftUI ImageRenderer export from running preview; not a desktop screenshot"]
            if let data = try? JSONSerialization.data(withJSONObject: metadata, options: .prettyPrinted) {
                try? data.write(to: URL(fileURLWithPath: destination), options: .atomic)
            }
            if ProcessInfo.processInfo.arguments.contains("--preview-auto-exit") {
                try? await Task.sleep(for: .seconds(3))
                garage?.pendingRemoval = nil
                garage?.selectedCar = nil
                garage?.recoveryCar = nil
                garage?.selectedWindow = nil
                try? await Task.sleep(for: .milliseconds(400))
                NSApp.terminate(nil) // This preview process only; never iTerm.
            }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard garage?.isPreview != true, garage?.busy == true else { return .terminateNow }
        // Do not abandon an in-flight save/close or create a restore ambiguity.
        let alert = NSAlert()
        alert.messageText = "Free Parking is still working"
        alert.informativeText = "Finish or cancel the iTerm close confirmation, then quit Free Parking. Your recovery file is kept."
        alert.addButton(withTitle: "Keep Free Parking Open")
        alert.runModal()
        return .terminateCancel
    }
}
