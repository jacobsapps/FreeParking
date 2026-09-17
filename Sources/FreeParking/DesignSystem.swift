import AppKit
import SwiftUI

enum ParkingStyle {
    static let background = Color(red: 0.09, green: 0.12, blue: 0.14)
    static let surface = Color(red: 0.14, green: 0.18, blue: 0.20)
    static let ink = Color(red: 0.96, green: 0.97, blue: 0.95)
    static let secondary = Color(red: 0.72, green: 0.77, blue: 0.79)
    static let yellow = Color(red: 1, green: 0.84, blue: 0.32)
    static let darkInk = Color(red: 0.07, green: 0.10, blue: 0.12)
    static let warning = Color(red: 1, green: 0.76, blue: 0.47)

    static func signFont(_ size: CGFloat) -> Font {
        Font(NSFont(name: "DINCondensed-Bold", size: size)
             ?? NSFont.monospacedSystemFont(ofSize: size, weight: .bold))
    }
}

struct ParkingButtonStyle: ButtonStyle {
    var prominent = false
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(prominent ? ParkingStyle.darkInk : ParkingStyle.ink)
            .padding(.horizontal, 13).padding(.vertical, 8)
            .background(prominent ? ParkingStyle.yellow : ParkingStyle.surface,
                        in: RoundedRectangle(cornerRadius: 7))
            .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(.white.opacity(prominent ? 0 : 0.18)))
            .opacity(enabled ? (configuration.isPressed ? 0.75 : 1) : 0.45)
    }
}

struct ParkingMark: View {
    var body: some View {
        if let url = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"),
           let image = NSImage(contentsOf: url) {
            Image(nsImage: image).resizable().interpolation(.high).aspectRatio(contentMode: .fit)
        } else {
            Text("P").font(.system(size: 24, weight: .bold)).foregroundStyle(.white)
                .frame(width: 34, height: 36).background(.blue, in: RoundedRectangle(cornerRadius: 7))
        }
    }
}

struct SampleBadge: View {
    var body: some View {
        Text("SAMPLE DATA").font(.system(size: 9, weight: .semibold, design: .monospaced))
            .foregroundStyle(ParkingStyle.secondary)
            .padding(.horizontal, 7).padding(.vertical, 4)
            .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(ParkingStyle.secondary.opacity(0.4)))
            .help("Isolated preview. These are fictional sessions, not your terminals.")
    }
}

struct RegistrationPlate: View {
    let text: String

    var body: some View {
        HStack(spacing: 5) {
            bolt
            Text(text.uppercased()).font(ParkingStyle.signFont(22)).tracking(1.3)
                .lineLimit(1).minimumScaleFactor(0.65).frame(maxWidth: .infinity)
                .padding(.top, 2)
            bolt
        }
        .foregroundStyle(ParkingStyle.darkInk)
        .padding(.horizontal, 7).padding(.vertical, 3)
        .background(ParkingStyle.yellow, in: RoundedRectangle(cornerRadius: 4))
        .overlay(RoundedRectangle(cornerRadius: 3).strokeBorder(.black.opacity(0.35)).padding(2))
        .accessibilityLabel(text)
    }

    private var bolt: some View { Circle().fill(.black.opacity(0.4)).frame(width: 2, height: 2) }
}

struct BayMarkings: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        return path
    }
}

struct Asphalt: View {
    var body: some View {
        LinearGradient(colors: [ParkingStyle.surface, ParkingStyle.background],
                       startPoint: .topLeading, endPoint: .bottomTrailing)
            .overlay {
                Canvas { context, size in
                    for x in stride(from: 0.0, through: size.width, by: 14) {
                        for y in stride(from: 0.0, through: size.height, by: 14) {
                            let offset = sin(x * 3.7 + y * 7.1) * 3
                            let dot = CGRect(x: x + offset, y: y - offset, width: 1, height: 1)
                            context.fill(Path(ellipseIn: dot), with: .color(.white.opacity(0.025)))
                        }
                    }
                }.allowsHitTesting(false)
            }
    }
}

/// Worn road paint, drawn directly on the asphalt rather than a bordered card.
/// The speckles are deterministic artwork—not an imported texture or screenshot.
struct PaintedParkingSpace: View {
    var compact = false

    var body: some View {
        Canvas { context, size in
            let left = size.width * 0.12
            let right = size.width * 0.88
            let top = size.height * 0.10
            let bottom = size.height * 0.94
            var lines = Path()
            lines.move(to: CGPoint(x: left, y: bottom))
            lines.addLine(to: CGPoint(x: left + 4, y: top))
            lines.addLine(to: CGPoint(x: right - 4, y: top + 1))
            lines.addLine(to: CGPoint(x: right, y: bottom))
            let paint = Color(red: 0.89, green: 0.88, blue: 0.75)
            let thickness: CGFloat = compact ? 5 : 7
            context.drawLayer { paintLayer in
                paintLayer.stroke(lines, with: .color(paint.opacity(0.62)),
                               style: StrokeStyle(lineWidth: thickness, lineCap: .square, lineJoin: .miter))
                paintLayer.draw(Text("P").font(ParkingStyle.signFont(compact ? 51 : 82))
                    .foregroundColor(paint.opacity(0.57)),
                    at: CGPoint(x: size.width / 2, y: size.height * 0.50))
                paintLayer.blendMode = .destinationOut
                // Thin breaks and chipped grain expose the road beneath the paint.
                for i in 0..<750 {
                    let x = CGFloat((i * 137 + 31) % 997) / 997 * size.width
                    let y = CGFloat((i * 263 + 73) % 991) / 991 * size.height
                    let width = CGFloat(i % 3 + 1) * 0.75
                    paintLayer.fill(Path(CGRect(x: x, y: y, width: width, height: i % 7 == 0 ? 1.6 : 0.7)),
                                 with: .color(ParkingStyle.background.opacity(i % 2 == 0 ? 0.65 : 0.35)))
                }
                for offset in [0.29, 0.72] {
                    let y = size.height * offset
                    var crack = Path()
                    crack.move(to: CGPoint(x: left - 5, y: y))
                    crack.addLine(to: CGPoint(x: left + 7, y: y + 1.5))
                    paintLayer.stroke(crack, with: .color(ParkingStyle.background.opacity(0.55)), lineWidth: 1)
                }
            }
        }
        .accessibilityHidden(true)
    }
}
