// Looks up what is in the diary right now, so a meeting can name itself and
// arrive with its attendee list already filled in. EventKit: no OAuth, no token.
//
//   mispy-cal --from <iso> --to <iso>   events overlapping that window, as JSON
//   mispy-cal --list                    the calendars this Mac knows about
import Foundation
import EventKit

let store = EKEventStore()
let sem = DispatchSemaphore(value: 0)
var granted = false

if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, _ in granted = ok; sem.signal() }
} else {
    store.requestAccess(to: .event) { ok, _ in granted = ok; sem.signal() }
}
_ = sem.wait(timeout: .now() + 60)

guard granted else {
    FileHandle.standardError.write(Data(
        "mispy-cal: calendar access refused — System Settings > Privacy & Security > Calendars\n".utf8))
    exit(2)
}

let cals = store.calendars(for: .event)
let args = CommandLine.arguments
if args.contains("--list") {
    for c in cals { print("\(c.title)\t\(c.source.title)") }
    exit(0)
}

let iso = ISO8601DateFormatter()
iso.formatOptions = [.withInternetDateTime]
func arg(_ name: String) -> Date? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return iso.date(from: args[i + 1])
}
let now = Date()
let from = arg("--from") ?? now.addingTimeInterval(-3600)
let to = arg("--to") ?? now.addingTimeInterval(3600)

let events = store.events(matching: store.predicateForEvents(withStart: from, end: to,
                                                             calendars: cals))
var rows: [[String: Any]] = []
for e in events where !e.isAllDay {
    var people: [String] = []
    for p in e.attendees ?? [] {
        let name = p.name ?? p.url.absoluteString.replacingOccurrences(of: "mailto:", with: "")
        if !name.isEmpty { people.append(name) }
    }
    if let organiser = e.organizer?.name, !people.contains(organiser) { people.insert(organiser, at: 0) }
    rows.append([
        "title": e.title ?? "",
        "start": iso.string(from: e.startDate),
        "end": iso.string(from: e.endDate),
        "location": e.location ?? "",
        "calendar": e.calendar.title,
        "attendees": people,
        "notes": String((e.notes ?? "").prefix(2000)),
    ])
}
let data = try JSONSerialization.data(withJSONObject: rows, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
