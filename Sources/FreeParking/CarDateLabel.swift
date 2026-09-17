import Foundation

enum CarDateLabel {
    /// Weekdays for the last seven calendar days, explicit dates thereafter.
    /// Calendar days (not 24-hour intervals) keep this correct across DST.
    static func title(for date: Date, now: Date = .now,
                      calendar: Calendar = .current, locale: Locale = .current) -> String {
        let age = calendar.dateComponents([.day], from: calendar.startOfDay(for: date),
                                           to: calendar.startOfDay(for: now)).day ?? Int.max
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        if (0..<7).contains(age) {
            formatter.setLocalizedDateFormatFromTemplate("EEEE")
        } else {
            let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: now)
            formatter.setLocalizedDateFormatFromTemplate(sameYear ? "dMMM" : "dMMMy")
        }
        return formatter.string(from: date)
    }
}
