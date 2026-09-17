import Foundation

/// UI formatting only. Compile with CarDateLabel.swift, no app or backend linked.
@main
struct CarDateLabelTests {
    static func main() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/London")!
        let locale = Locale(identifier: "en_GB")
        func date(_ string: String) -> Date { ISO8601DateFormatter().date(from: string)! }
        let now = date("2026-09-17T14:00:00+01:00")
        func label(_ string: String, at reference: Date = now) -> String {
            CarDateLabel.title(for: date(string), now: reference, calendar: calendar, locale: locale)
        }
        assert(label("2026-09-17T09:00:00+01:00") == "Thursday")
        assert(label("2026-09-16T18:15:00+01:00") == "Wednesday")
        assert(label("2026-09-15T17:42:00+01:00") == "Tuesday")
        assert(label("2026-09-14T18:15:00+01:00") == "Monday")
        assert(label("2026-09-11T00:00:00+01:00") == "Friday")
        assert(label("2026-09-10T23:59:00+01:00") == "10 Sep")
        assert(label("2026-08-27T17:30:00+01:00") == "27 Aug")
        assert(label("2025-09-17T14:00:00+01:00") == "17 Sep 2025")
        assert(label("2026-09-18T09:00:00+01:00") == "18 Sep")
        // Last-year dates still get a weekday when they fall within seven days.
        assert(label("2025-12-31T23:30:00Z", at: date("2026-01-01T08:00:00Z")) == "Wednesday")
        // Seven calendar days across the spring DST transition: use a date.
        assert(label("2026-03-23T23:30:00Z", at: date("2026-03-30T00:05:00+01:00")) == "23 Mar")
        // Six days across the autumn transition still gets a weekday.
        assert(label("2026-10-19T00:00:00+01:00", at: date("2026-10-25T23:59:00Z")) == "Monday")
        print("12 UI date-label checks passed; no app or terminal code was loaded.")
    }
}
