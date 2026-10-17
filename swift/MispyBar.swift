// Mispy dans la barre de menus : un point, et deux clics.
//
// Le terminal convient pour régler l'outil une fois ; il ne convient pas pour
// lancer un enregistrement entre deux réunions. Cette application ne fait rien
// d'elle-même : elle lit le fichier d'état de Mispy et appelle la commande
// `mispy`. Toute la logique reste dans le CLI, qui est testé.
import AppKit
import Foundation

let home = FileManager.default.homeDirectoryForCurrentUser
let cli = home.appendingPathComponent(".local/bin/mispy")
let stateFile = home.appendingPathComponent(".local/state/mispy/job.json")

struct Job {
    var stage = "idle"
    var title = ""
    var startedAt: Date?
    var markdown = ""
    var folder = ""

    static func read() -> Job {
        var j = Job()
        guard let data = try? Data(contentsOf: stateFile),
              let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return j }
        j.stage = o["stage"] as? String ?? "idle"
        j.title = o["title"] as? String ?? ""
        j.markdown = o["markdown"] as? String ?? ""
        j.folder = o["folder"] as? String ?? ""
        if let s = o["started_at"] as? String {
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime, .withColonSeparatorInTime]
            j.startedAt = f.date(from: s) ?? ISO8601DateFormatter().date(from: s + "Z")
            if j.startedAt == nil {
                let df = DateFormatter()
                df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
                j.startedAt = df.date(from: s)
            }
        }
        return j
    }

    var recording: Bool { stage == "recording" }
    var paused: Bool { stage == "paused" }
    var busy: Bool { ["details", "transcribing", "writing", "typesetting"].contains(stage) }
    var done: Bool { stage == "done" }

    var label: String {
        switch stage {
        case "idle": return "Prêt"
        case "recording": return "Enregistre"
        case "paused": return "En pause"
        case "details": return "Attend tes précisions"
        case "transcribing": return "Transcrit"
        case "writing": return "Rédige le compte rendu"
        case "typesetting": return "Met en page"
        case "done": return "Compte rendu prêt"
        case "failed": return "Arrêté sur une erreur"
        default: return stage
        }
    }

    var symbol: String {
        if recording { return "record.circle.fill" }
        if paused { return "pause.circle" }
        if busy { return "pencil.and.outline" }
        if done { return "checkmark.circle" }
        if stage == "failed" { return "exclamationmark.triangle" }
        return "mic"
    }

    var elapsed: String? {
        guard recording || paused, let s = startedAt else { return nil }
        let secs = Int(Date().timeIntervalSince(s))
        return String(format: "%d:%02d", secs / 60, secs % 60)
    }
}

func run(_ args: [String], then: @escaping (String) -> Void = { _ in }) {
    DispatchQueue.global().async {
        let p = Process()
        p.executableURL = cli
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch {
            DispatchQueue.main.async { then("mispy introuvable : \(error.localizedDescription)") }
            return
        }
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        p.waitUntilExit()
        DispatchQueue.main.async { then(out.trimmingCharacters(in: .whitespacesAndNewlines)) }
    }
}

func notify(_ text: String) {
    guard !text.isEmpty else { return }
    let a = NSAlert()
    a.messageText = "Mispy"
    a.informativeText = String(text.prefix(400))
    a.alertStyle = .informational
    NSApp.activate(ignoringOtherApps: true)
    a.runModal()
}

final class Bar: NSObject, NSApplicationDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var timer: Timer?

    func applicationDidFinishLaunching(_ note: Notification) {
        item.menu = NSMenu()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in self.refresh() }
    }

    @objc func start() { run(["start"]) { out in if out.hasPrefix("✗") { notify(out) }; self.refresh() } }
    @objc func startNamed() {
        let a = NSAlert()
        a.messageText = "Nom de la réunion"
        a.informativeText = "Laisse vide pour qu'il le prenne dans ton agenda."
        a.addButton(withTitle: "Démarrer")
        a.addButton(withTitle: "Annuler")
        let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        a.accessoryView = f
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        let name = f.stringValue.trimmingCharacters(in: .whitespaces)
        run(name.isEmpty ? ["start"] : ["start", name]) { out in
            if out.hasPrefix("✗") { notify(out) }
            self.refresh()
        }
    }
    @objc func stop() { run(["stop"]) { _ in self.refresh() } }
    @objc func pause() { run(["pause"]) { _ in self.refresh() } }
    @objc func discard() {
        let a = NSAlert()
        a.messageText = "Supprimer la prise en cours ?"
        a.informativeText = "L'enregistrement est détruit. C'est sans retour."
        a.addButton(withTitle: "Supprimer")
        a.addButton(withTitle: "Annuler")
        a.alertStyle = .warning
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn { run(["discard"]) { _ in self.refresh() } }
    }
    @objc func openLast() { run(["last"]) { _ in } }
    @objc func openFolder() {
        let j = Job.read()
        let path = j.folder.isEmpty
            ? home.appendingPathComponent("Documents/Meetings").path : j.folder
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
    @objc func quit() { NSApp.terminate(nil) }

    func add(_ menu: NSMenu, _ title: String, _ sel: Selector?, enabled: Bool = true,
             key: String = "") {
        let mi = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        mi.target = self
        mi.isEnabled = enabled && sel != nil
        menu.addItem(mi)
    }

    func refresh() {
        let j = Job.read()
        if let b = item.button {
            b.image = NSImage(systemSymbolName: j.symbol, accessibilityDescription: "Mispy")
            b.image?.isTemplate = true
            b.title = j.recording ? " \(j.elapsed ?? "")" : ""
        }
        let menu = NSMenu()
        let head = NSMenuItem(title: "Mispy — \(j.label)", action: nil, keyEquivalent: "")
        head.isEnabled = false
        menu.addItem(head)
        if !j.title.isEmpty {
            let sub = NSMenuItem(title: "   \(j.title)", action: nil, keyEquivalent: "")
            sub.isEnabled = false
            menu.addItem(sub)
        }
        menu.addItem(.separator())

        if j.recording {
            add(menu, "Terminer et rédiger", #selector(stop), key: "s")
            add(menu, "Mettre en pause", #selector(pause))
        } else if j.paused {
            add(menu, "Reprendre", #selector(start), key: "s")
            add(menu, "Terminer et rédiger", #selector(stop))
        } else if j.busy {
            add(menu, "Travail en cours…", nil, enabled: false)
        } else {
            add(menu, "Démarrer", #selector(start), key: "s")
            add(menu, "Démarrer en nommant…", #selector(startNamed))
        }
        if !(j.stage == "idle" || j.busy) {
            add(menu, "Supprimer la prise", #selector(discard))
        }
        menu.addItem(.separator())
        add(menu, "Ouvrir le dernier compte rendu", #selector(openLast),
            enabled: !j.markdown.isEmpty)
        add(menu, "Ouvrir le dossier des réunions", #selector(openFolder))
        menu.addItem(.separator())
        add(menu, "Quitter", #selector(quit), key: "q")
        item.menu = menu
    }
}

let app = NSApplication.shared
let bar = Bar()
app.delegate = bar
app.setActivationPolicy(.accessory)      // pas d'icône dans le Dock
app.run()
