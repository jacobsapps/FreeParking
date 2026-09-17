import AppKit
import SwiftUI

@main
struct RenderBanner {
    @MainActor static func main() throws {
        guard CommandLine.arguments.count == 2 else { fatalError("Usage: render-banner <output.png>") }
        let renderer = ImageRenderer(content: FreeParkingBanner())
        renderer.scale = 1
        guard let image = renderer.cgImage,
              let data = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
            fatalError("Could not render banner")
        }
        try data.write(to: URL(fileURLWithPath: CommandLine.arguments[1]), options: .atomic)
    }
}
