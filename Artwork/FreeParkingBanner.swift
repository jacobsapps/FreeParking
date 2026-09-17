import SwiftUI

/// Public README artwork only. No application state or real session data.
struct FreeParkingBanner: View {
    var body: some View {
        ZStack {
            Asphalt()
            HStack(spacing: 54) {
                VStack(alignment: .leading, spacing: 21) {
                    HStack(spacing: 17) {
                        FreeParkingIcon().scaleEffect(0.09).frame(width: 92, height: 92)
                        Text("FREE\nPARKING").font(ParkingStyle.signFont(62)).tracking(2).lineSpacing(-9)
                            .foregroundStyle(ParkingStyle.ink)
                    }
                    Text("Park your tabs.\nCall it a day.")
                        .font(.system(size: 29, weight: .medium)).foregroundStyle(ParkingStyle.ink)
                        .lineSpacing(3)
                    Text("iTerm  /  Claude + Codex  /  macOS")
                        .font(.system(size: 13, design: .monospaced)).foregroundStyle(ParkingStyle.secondary)
                }
                .frame(width: 445, alignment: .leading)
                HStack(alignment: .top, spacing: 30) {
                    VStack(spacing: 18) {
                        PaintedParkingSpace().frame(width: 120, height: 218)
                        Text("PARK HERE").font(ParkingStyle.signFont(24)).tracking(2)
                            .foregroundStyle(ParkingStyle.secondary)
                    }
                    VStack(spacing: 18) {
                        ZStack {
                            BayMarkings().stroke(ParkingStyle.secondary.opacity(0.42), lineWidth: 4)
                                .padding(.horizontal, 5)
                            CarIllustration(color: Color(red: 0.38, green: 0.70, blue: 0.72))
                                .frame(width: 100, height: 191)
                                .shadow(color: .black.opacity(0.3), radius: 9, x: 4, y: 8)
                        }.frame(width: 154, height: 218)
                        RegistrationPlate(text: "OFF DUTY").frame(width: 154)
                    }
                }
            }
            .padding(.horizontal, 72)
            VStack { Spacer(); HStack(spacing: 12) {
                ForEach(0..<17) { _ in Rectangle().fill(ParkingStyle.yellow.opacity(0.5)).frame(width: 40, height: 3) }
            }.padding(.bottom, 30) }
        }
        .frame(width: 1200, height: 460)
    }
}
