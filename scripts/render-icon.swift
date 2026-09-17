import AppKit
import SwiftUI

/// Build-time artwork renderer only. No Free Parking model or terminal helpers.
@main
struct RenderIcon {
    @MainActor
    static func main() throws {
        guard CommandLine.arguments.count == 3 else {
            fatalError("Usage: render-icon <iconset directory> <1024px PNG>")
        }
        let iconset = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
        for points in [16, 32, 128, 256, 512] {
            for scale in [1, 2] {
                let renderer = ImageRenderer(content: FreeParkingIcon())
                renderer.scale = CGFloat(points * scale) / 1024
                guard let image = renderer.cgImage,
                      let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
                    fatalError("Could not render icon")
                }
                let name = "icon_\(points)x\(points)\(scale == 2 ? "@2x" : "").png"
                try png.write(to: iconset.appendingPathComponent(name), options: .atomic)
                if points == 512 && scale == 2 {
                    try png.write(to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic)
                }
            }
        }
    }
}
