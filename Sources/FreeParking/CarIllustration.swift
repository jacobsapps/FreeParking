import SwiftUI

/// Code-native, top-down toy car. No external assets, rendering service or fonts.
struct CarIllustration: View {
    let color: Color

    var body: some View {
        GeometryReader { proxy in
            let scale = min(proxy.size.width / 120, proxy.size.height / 220)
            ZStack {
                ForEach([-1.0, 1.0], id: \.self) { side in
                    ForEach([-1.0, 1.0], id: \.self) { end in
                        RoundedRectangle(cornerRadius: 5)
                            .fill(LinearGradient(colors: [.black, Color(white: 0.18), .black], startPoint: .leading, endPoint: .trailing))
                            .frame(width: 15, height: 34).offset(x: side * 45, y: end * 58)
                    }
                    Capsule().fill(color.gradient).frame(width: 12, height: 8)
                        .offset(x: side * 48, y: -24)
                }
                CarBody()
                    .fill(LinearGradient(stops: [.init(color: color.opacity(0.7), location: 0), .init(color: color, location: 0.18), .init(color: color, location: 0.7), .init(color: color.opacity(0.7), location: 1)], startPoint: .leading, endPoint: .trailing))
                    .overlay(CarBody().stroke(.black.opacity(0.45), lineWidth: 1.5))
                    .frame(width: 96, height: 204)
                CarBody().stroke(.white.opacity(0.22), lineWidth: 1)
                    .frame(width: 89, height: 195)
                RoundedRectangle(cornerRadius: 15)
                    .stroke(.black.opacity(0.15), lineWidth: 1)
                    .frame(width: 67, height: 53).offset(y: -68)
                Capsule().fill(.white.opacity(0.23)).frame(width: 56, height: 2).offset(y: -90)
                GlassPanel()
                    .fill(LinearGradient(colors: [Color(red: 0.32, green: 0.43, blue: 0.47), Color(red: 0.09, green: 0.15, blue: 0.18)], startPoint: .topLeading, endPoint: .bottomTrailing))
                    .overlay(GlassPanel().stroke(.black.opacity(0.65), lineWidth: 2))
                    .overlay(GlassPanel().stroke(.white.opacity(0.20), lineWidth: 0.7).padding(3))
                    .frame(width: 74, height: 36).offset(y: -28)
                RoundedRectangle(cornerRadius: 10)
                    .fill(LinearGradient(colors: [color, color.opacity(0.85)], startPoint: .topLeading, endPoint: .bottomTrailing))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(.white.opacity(0.15), lineWidth: 1))
                    .shadow(color: .black.opacity(0.3), radius: 2, x: 0, y: 2)
                    .frame(width: 66, height: 52).offset(y: 18)
                GlassPanel()
                    .fill(LinearGradient(colors: [Color(red: 0.14, green: 0.23, blue: 0.26), Color(red: 0.28, green: 0.36, blue: 0.39)], startPoint: .top, endPoint: .bottom))
                    .overlay(GlassPanel().stroke(.black.opacity(0.5), lineWidth: 2))
                    .frame(width: 70, height: 28).rotationEffect(.degrees(180)).offset(y: 56)
                ForEach([-1.0, 1.0], id: \.self) { side in
                    Capsule().fill(Color(white: 0.12)).frame(width: 5, height: 40).offset(x: side * 38, y: 16)
                    Capsule().fill(.white.opacity(0.3)).frame(width: 2, height: 18).offset(x: side * 40, y: 26)
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color(red: 1, green: 0.96, blue: 0.76).gradient)
                        .overlay(RoundedRectangle(cornerRadius: 4).stroke(.black.opacity(0.2), lineWidth: 1))
                        .frame(width: 17, height: 9).rotationEffect(.degrees(side * 12)).offset(x: side * 28, y: -88)
                    RoundedRectangle(cornerRadius: 2).fill(Color(red: 0.76, green: 0.18, blue: 0.12).gradient)
                        .frame(width: 16, height: 5).offset(x: side * 30, y: 88)
                }
                Capsule().fill(Color(white: 0.2)).frame(width: 48, height: 4).offset(y: -98)
                Capsule().fill(Color(white: 0.22)).frame(width: 64, height: 4).offset(y: 97)
                RoundedRectangle(cornerRadius: 1).fill(Color(red: 0.98, green: 0.8, blue: 0.3))
                    .frame(width: 21, height: 6).offset(y: 89)
            }
            .frame(width: 120, height: 220)
            .scaleEffect(scale, anchor: .topLeading)
            .offset(x: (proxy.size.width - 120 * scale) / 2, y: (proxy.size.height - 220 * scale) / 2)
        }
        .accessibilityHidden(true)
    }
}

private struct GlassPanel: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.width * 0.14, y: 0))
        p.addQuadCurve(to: CGPoint(x: r.width * 0.86, y: 0), control: CGPoint(x: r.midX, y: -r.height * 0.1))
        p.addLine(to: CGPoint(x: r.maxX, y: r.maxY))
        p.addQuadCurve(to: CGPoint(x: 0, y: r.maxY), control: CGPoint(x: r.midX, y: r.height * 0.85))
        p.closeSubpath()
        return p
    }
}

private struct CarBody: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.width * 0.25, y: 0))
        p.addQuadCurve(to: CGPoint(x: r.width * 0.75, y: 0), control: CGPoint(x: r.midX, y: -r.height * 0.02))
        p.addCurve(to: CGPoint(x: r.width, y: r.height * 0.22), control1: CGPoint(x: r.width * 0.98, y: 0), control2: CGPoint(x: r.width, y: r.height * 0.12))
        p.addLine(to: CGPoint(x: r.width, y: r.height * 0.83))
        p.addQuadCurve(to: CGPoint(x: r.width * 0.74, y: r.height), control: CGPoint(x: r.width, y: r.height))
        p.addLine(to: CGPoint(x: r.width * 0.26, y: r.height))
        p.addQuadCurve(to: CGPoint(x: 0, y: r.height * 0.83), control: CGPoint(x: 0, y: r.height))
        p.addLine(to: CGPoint(x: 0, y: r.height * 0.22))
        p.addCurve(to: CGPoint(x: r.width * 0.25, y: 0), control1: CGPoint(x: 0, y: r.height * 0.12), control2: CGPoint(x: r.width * 0.02, y: 0))
        p.closeSubpath()
        return p
    }
}
