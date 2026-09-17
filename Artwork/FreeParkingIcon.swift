import SwiftUI

/// The app's blue parking sign, redrawn as resolution-independent icon artwork.
/// Transparent margins preserve the sign's small, characteristic tilt in the Dock.
struct FreeParkingIcon: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 156, style: .continuous)
                .fill(Color(red: 0.08, green: 0.19, blue: 0.32))
                .offset(y: 10)

            RoundedRectangle(cornerRadius: 156, style: .continuous)
                .fill(LinearGradient(colors: [Color(red: 0.23, green: 0.43, blue: 0.64),
                                              Color(red: 0.10, green: 0.25, blue: 0.43)],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
                .overlay {
                    RoundedRectangle(cornerRadius: 156, style: .continuous)
                        .strokeBorder(LinearGradient(colors: [Color(red: 0.38, green: 0.59, blue: 0.77),
                                                              Color(red: 0.16, green: 0.33, blue: 0.52)],
                                                     startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 9)
                }

            RoundedRectangle(cornerRadius: 114, style: .continuous)
                .strokeBorder(LinearGradient(colors: [Color(red: 0.83, green: 0.90, blue: 0.96),
                                                      Color(red: 0.65, green: 0.78, blue: 0.91)],
                                             startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 16)
                .padding(46)

            Text("P")
                .font(.custom("HelveticaNeue-Bold", size: 566))
                .foregroundStyle(Color(red: 0.98, green: 0.985, blue: 1))
                .offset(x: 5, y: -8)
        }
        .frame(width: 806, height: 850)
        .compositingGroup()
        .rotationEffect(.degrees(-4))
        .shadow(color: .black.opacity(0.26), radius: 20, x: 0, y: 18)
        .frame(width: 1024, height: 1024)
    }
}
