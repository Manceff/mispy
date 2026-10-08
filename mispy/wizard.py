"""`mispy setup` — the one interactive moment in Mispy's life.

Five minutes, once. Everything it asks is something it genuinely cannot work out
on its own, and every answer is pre-filled with what is already configured, so it
is safe to run again.
"""
from __future__ import annotations
import pathlib, shutil, subprocess, tempfile, time
from . import audio, capture, config, host, meeting, transcribe, ui

BREW_PACKAGES = {"ffmpeg": "ffmpeg"}


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _brew() -> str | None:
    return shutil.which("brew") or next(
        (p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
         if pathlib.Path(p).exists()), None)


def _short(p) -> str:
    return str(p).replace(str(pathlib.Path.home()), "~")


# --------------------------------------------------------------------------- 1
def step_tools() -> None:
    ui.heading("1/5", "The tools Mispy runs on")
    if host.WINDOWS:
        _tools_windows()
    else:
        _tools_macos()

    good, detail = transcribe.available(config.load())
    (ui.good if good else ui.bad)(f"transcription  {detail}")
    if not good:
        ui.note("re-run the installer; it is the one piece Mispy cannot do without")


def _tools_windows() -> None:
    for module, what in (("soundcard", "records the microphone and the call"),
                         ("numpy", "mixes the two tracks"),
                         ("faster_whisper", "turns speech into text, on this machine"),
                         ("win32com.client", "reads your Outlook calendar")):
        try:
            __import__(module)
            ui.good(f"{module.split('.')[0]:<16}{what}")
        except Exception:
            ui.bad(f"{module.split('.')[0]} is missing — re-run install.ps1")
    if shutil.which("tectonic"):
        ui.good(f"{'tectonic':<16}typesets a PDF as well as the Word file")
    else:
        ui.warn("tectonic is not installed — Markdown and Word only, no PDF")


def _tools_macos() -> None:
    missing = []
    for binary, formula in BREW_PACKAGES.items():
        (ui.good if _have(binary) else ui.bad)(binary if _have(binary)
                                               else f"{binary} is missing")
        if not _have(binary):
            missing.append(formula)
    if _have("tectonic"):
        ui.good("tectonic      minutes are also typeset as a PDF")
    else:
        ui.warn("tectonic is missing — you will get Markdown but no PDF")
        missing.append("tectonic")
    for helper, what in ((capture.HELPER, "call audio"), (meeting.HELPER, "diary reader")):
        if helper is not None:
            (ui.good if helper.exists() else ui.warn)(f"{what}  {helper.name}")
    if missing:
        brew = _brew()
        ui.console.print()
        if brew and ui.confirm(f"Install {', '.join(missing)} with Homebrew now?"):
            subprocess.run([brew, "install", *missing], check=False)
        elif not brew:
            ui.note("Homebrew is not installed — see https://brew.sh")


# --------------------------------------------------------------------------- 2
def claude_state() -> str:
    """not-installed | not-logged-in | ok"""
    if not _have("claude"):
        return "not-installed"
    try:
        r = subprocess.run(["claude", "-p", "--output-format", "text", "say ok"],
                           capture_output=True, text=True, timeout=120)
    except Exception:
        return "not-logged-in"
    blob = (r.stdout or "") + (r.stderr or "")
    if "Not logged in" in blob or "Please run /login" in blob or "/login" in blob:
        return "not-logged-in"
    return "ok" if r.returncode == 0 and (r.stdout or "").strip() else "not-logged-in"


def step_claude(cfg: dict) -> dict:
    ui.heading("2/5", "Connect Claude")
    ui.say("Mispy writes the minutes with Claude, through the Claude Code CLI.")
    ui.note("your own subscription — Mispy never asks you for a key and never stores one")
    ui.console.print()
    for _ in range(6):
        with ui.console.status("  [grey62]checking…[/]", spinner="dots"):
            st = claude_state()
        if st == "ok":
            ui.good("Claude is connected")
            cfg["minutes"]["backend"] = "claude_cli"
            return cfg
        if st == "not-installed":
            ui.bad("the `claude` command is not installed")
            if _have("npm") and ui.confirm("Install it now with npm?"):
                subprocess.run(["npm", "install", "-g", "@anthropic-ai/claude-code"], check=False)
                continue
            ui.note("install Node, then:  npm install -g @anthropic-ai/claude-code")
        else:
            ui.warn("Claude is installed but not signed in")
            ui.console.print()
            ui.say("In another terminal window:", style="bold")
            ui.note("1.  claude")
            ui.note("2.  type  /login  and follow the browser")
            ui.note("3.  come back here")
            ui.console.print()
        if not ui.confirm("Try again?"):
            break
    ui.warn("carrying on without Claude — recording will work, minutes will not")
    ui.note('you can also use the API: export ANTHROPIC_API_KEY and set backend = "api"')
    return cfg


# --------------------------------------------------------------------------- 3
def _peak(wav: pathlib.Path) -> float:
    try:
        samples, _rate = audio.read_wav(wav)
        return float(max(abs(samples.min()), abs(samples.max()))) if len(samples) else 0.0
    except Exception:
        return 0.0


def step_capture(cfg: dict) -> dict:
    ui.heading("3/5", "How you meet")
    choice = ui.choose("Where does the sound come from?", [
        ("Around a table", "your microphone picks up the room"),
        ("On calls — Teams, Zoom, Meet", "what this machine is playing, plus your own voice"),
        ("Both, depending on the day", "records the room and the call together"),
    ], default=3)
    cfg["audio"]["capture"] = {1: "mic", 2: "both", 3: "both"}[choice]

    cfg = _pick_microphone(cfg)
    if cfg["audio"]["capture"] in ("both", "system"):
        cfg = _pick_system(cfg)

    lang = ui.ask("Language people speak in your meetings (fr, en, de, es…)",
                  cfg["transcribe"]["language"])
    cfg["transcribe"]["language"] = (lang or "fr").strip().lower()[:5]
    return cfg


def _pick_from(cfg: dict, key: str, items: list[str], question: str) -> dict:
    for i, d in enumerate(items, 1):
        ui.console.print(f"    [bold deep_sky_blue1]{i}[/]  {d}", highlight=False)
    ui.console.print()
    current = cfg["audio"].get(key, "")
    default = str(items.index(current) + 1) if current in items else "1"
    while True:
        a = ui.ask(question, default)
        if a.isdigit() and 1 <= int(a) <= len(items):
            cfg["audio"][key] = items[int(a) - 1]
            ui.good(cfg["audio"][key])
            return cfg


def _pick_microphone(cfg: dict) -> dict:
    ui.console.print()
    try:
        devices = capture.devices()
    except Exception as exc:
        ui.bad(f"cannot list the inputs: {str(exc)[:140]}")
        return cfg
    if not devices:
        ui.bad("this machine reports no audio input at all")
        return cfg
    ui.note("chosen by name, never by number: the order shifts as soon as a headset "
            "or a phone appears, and that is how a meeting ends up recorded through "
            "the wrong thing")
    ui.console.print()
    cfg = _pick_from(cfg, "device", devices, "Which microphone?")

    ui.console.print()
    if not ui.confirm("Record four seconds to check it works?"):
        return cfg
    work = pathlib.Path(tempfile.mkdtemp())
    try:
        ui.say("[bold]Say something…[/]")
        only_mic = dict(cfg)
        only_mic["audio"] = dict(cfg["audio"], capture="mic")
        pids = capture.start(work, 1, only_mic)
        time.sleep(4.5)
        capture.stop(pids, work, 1)
        peak = _peak(capture.mic_path(work, 1))
        if peak < 0.01:
            ui.bad("nothing was captured")
            if host.WINDOWS:
                ui.note("Settings ▸ Privacy & security ▸ Microphone — allow desktop apps "
                        "to access it")
            else:
                ui.note("System Settings ▸ Privacy & Security ▸ Microphone, and allow the "
                        "terminal you are running mispy from")
        else:
            ui.good(f"heard you  [green]{'▁▂▃▄▅▆▇█'[: max(1, int(peak * 8))]}[/]")
    except Exception as exc:
        ui.bad(str(exc)[:140])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return cfg


def _pick_system(cfg: dict) -> dict:
    ui.console.print()
    ui.say("For calls, Mispy records what this machine is playing.")
    if host.WINDOWS:
        return _system_windows(cfg)
    return _system_macos(cfg)


def _system_windows(cfg: dict) -> dict:
    ui.note("Windows hands the sound of a call straight to an application that asks "
            "for it. No virtual cable to install, nothing for IT to approve.")
    ui.console.print()
    try:
        outs = capture.system_devices()
    except Exception as exc:
        outs, exc_text = [], str(exc)[:140]
        ui.bad(exc_text)
    if not outs:
        ui.bad("no output device offers a loopback")
        ui.note("set a default playback device in Windows sound settings, then re-run "
                "`mispy setup`. Until then only the microphone is recorded.")
        cfg["audio"]["capture"] = "mic"
        return cfg
    ui.note("pick the device you actually hear the call through — the headset if you "
            "wear one, the speakers otherwise")
    ui.console.print()
    return _pick_from(cfg, "system_device", outs, "Which output?")


def _system_macos(cfg: dict) -> dict:
    ui.note("macOS calls that screen recording, so it will ask for the permission by "
            "that name. Nothing from the screen is captured: the stream is two pixels "
            "wide and only its sound is read.")
    ui.console.print()
    for attempt in range(4):
        with ui.console.status("  [grey62]asking macOS…[/]", spinner="dots"):
            ok = capture.system_available()
        if ok:
            ui.good("call audio is allowed")
            return cfg
        ui.bad("screen recording is not allowed for this terminal")
        ui.note("System Settings ▸ Privacy & Security ▸ Screen & System Audio Recording, "
                "switch on your terminal, then quit and reopen it")
        ui.console.print()
        if attempt == 2 and ui.confirm("Open that settings pane for me?"):
            subprocess.run(["open", "x-apple.systempreferences:com.apple.preference."
                                    "security?Privacy_ScreenCapture"], check=False)
        if not ui.confirm("Try again?"):
            break
    ui.warn("falling back to the microphone alone")
    ui.note("that still works in a room; on a call with headphones it will only hear you")
    cfg["audio"]["capture"] = "mic"
    return cfg


# --------------------------------------------------------------------------- 4
def step_output(cfg: dict) -> dict:
    ui.heading("4/5", "Where the minutes go")
    root = ui.ask("Folder for your meetings", _short(cfg["output"]["root"]))
    path = pathlib.Path(root).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    cfg["output"]["root"] = str(path)
    ui.good(_short(path))
    ui.note("one folder per meeting, named by date")
    ui.console.print()

    has_tex = shutil.which("tectonic") is not None
    options = [("Markdown and Word", "paste one anywhere, forward the other"),
               ("Markdown only", "one file, no formatting to fight with"),
               ("Markdown, Word and PDF" if has_tex else "Markdown, Word and PDF (needs tectonic)",
                "the PDF is the one to archive")]
    choice = ui.choose("Which files do you want?", options,
                       default=3 if (has_tex and not host.WINDOWS) else 1)
    cfg["output"]["formats"] = {1: ["md", "docx"], 2: ["md"],
                                3: ["md", "docx", "pdf"]}[choice]
    if "pdf" in cfg["output"]["formats"] and not has_tex:
        ui.warn("tectonic is not installed, so the PDF will be skipped until it is")
    ui.good(", ".join(cfg["output"]["formats"]))
    ui.console.print()

    same = ui.confirm("Write the minutes in the same language as the meeting?")
    cfg["minutes"]["language"] = "auto" if same else \
        (ui.ask("Language for the minutes (fr, en, de…)", "en").strip().lower()[:2] or "en")
    style = ui.choose("How much detail?", [
        ("Standard", "summary, decisions, actions, and the substance of the discussion"),
        ("Brief", "summary, decisions and actions — three or four lines per topic"),
        ("Exhaustive", "follows the meeting in order and keeps everything"),
    ], default=1)
    cfg["minutes"]["style"] = {1: "standard", 2: "brief", 3: "verbatim"}[style]
    cfg["output"]["organisation"] = ui.ask("Printed on the document (optional)",
                                           cfg["output"]["organisation"])
    ui.console.print()
    keep = ui.confirm("Keep the transcript alongside the minutes?", default=False)
    cfg["privacy"]["keep_transcript"] = keep
    if not keep:
        ui.note("the recording and the transcript are destroyed as soon as the "
                "minutes exist")
    return cfg


# --------------------------------------------------------------------------- 5
def step_calendar(cfg: dict) -> dict:
    ui.heading("5/5", "Your diary  (optional)")
    ui.say("If Mispy can see your diary, a meeting names itself and arrives with its "
           "attendee list already filled in.")
    ui.note("an attendee list is what turns « someone will send the deck » into "
            "« Alice will send the deck »")
    ui.console.print()
    if not ui.confirm("Let Mispy read your calendar?"):
        cfg["calendar"]["enabled"] = False
        ui.note('you will name each meeting yourself: mispy start "Weekly"')
        return cfg
    cfg["calendar"]["enabled"] = True
    return _calendar_outlook(cfg) if host.WINDOWS else _calendar_macos(cfg)


def _calendar_outlook(cfg: dict) -> dict:
    cfg["calendar"]["source"] = "outlook"
    ui.note("through Outlook itself, over COM — not the Graph API, which would need "
            "an app registered in Azure AD and an administrator to approve it. A "
            "Teams meeting is an Outlook appointment, so Teams needs nothing at all.")
    ui.console.print()
    from . import calendar_win
    with ui.console.status("  [grey62]asking Outlook…[/]", spinner="dots"):
        ok, detail = calendar_win.available()
    if ok:
        ui.good(f"Outlook answered ({detail})")
        import datetime
        try:
            n = len(meeting.around(datetime.datetime.now(), cfg))
            ui.note(f"{n} appointment(s) around now")
        except Exception:
            pass
        return cfg
    ui.bad(detail)
    ui.note("if you are on the new Outlook, switch the toggle to the classic one, or "
            "turn the diary off and name meetings yourself")
    if not ui.confirm("Keep the diary switched on anyway?", default=False):
        cfg["calendar"]["enabled"] = False
    return cfg


def _calendar_macos(cfg: dict) -> dict:
    cfg["calendar"]["source"] = "eventkit"
    try:
        with ui.console.status("  [grey62]waiting for the permission dialog…[/]", spinner="dots"):
            cals = meeting.calendars()
    except Exception as exc:
        ui.bad(str(exc)[:160])
        ui.note("System Settings ▸ Privacy & Security ▸ Calendars, allow your terminal, "
                "then run `mispy setup` again")
        return cfg
    if not cals:
        ui.warn("no calendar found")
        return cfg
    names = [c[0] for c in cals]
    labels = [f"{n}   ({acct})" for n, acct in cals]
    picked = ui.pick_many("Which calendars hold your meetings?", labels,
                          [l for l, (n, _) in zip(labels, cals)
                           if n in cfg["calendar"]["calendars"]])
    cfg["calendar"]["calendars"] = [names[labels.index(l)] for l in picked]
    ui.good(", ".join(cfg["calendar"]["calendars"]) or "all of them")
    return cfg


# --------------------------------------------------------------------------- run
WELCOME = """[bold]Mispy records your meetings and hands you back the minutes.[/]

You run one command when the meeting starts and one when it ends. It listens — to
the room, to the call, or to both — and then transcribes it on this Mac, writes up
what was decided and who owes what to whom, and saves it as a Markdown file you can
paste anywhere and a PDF you can keep.

The recording never leaves your Mac. The text of the transcript goes to Claude,
on your own subscription, because that is what writes the minutes. Both the
recording and the transcript are destroyed once the minutes exist."""

CONSENT = """[bold]Before anything else.[/]

Recording people who have not been told is, in a number of countries — France
among them — a criminal offence, whatever the recording is for. In most workplaces
it is also a disciplinary matter on its own.

Mispy has no way of knowing who is in your meetings or what your employer allows.
[bold]Saying at the start that you are recording, and letting anyone object, is yours
to do.[/] It takes one sentence, and it is the whole difference."""


def run(argv: list[str] | None = None) -> int:
    if not ui.is_tty():
        ui.bad("`mispy setup` needs a terminal")
        return 1
    ui.console.print()
    ui.panel(WELCOME, title="[bold]Mispy[/]")
    ui.panel(CONSENT, title="[bold]Consent[/]", style="yellow")
    if not ui.confirm("Understood?"):
        ui.note("nothing was changed.")
        return 1

    cfg = config.load()
    try:
        step_tools()
        cfg = step_claude(cfg)
        cfg = step_capture(cfg)
        cfg = step_output(cfg)
        cfg = step_calendar(cfg)
        path = config.write(cfg)
    except SystemExit:
        ui.console.print()
        ui.note("stopped. Nothing was changed.")
        return 130

    ui.console.print()
    ui.panel(f"""[bold]You are set up.[/]

  [bold deep_sky_blue1]mispy start[/]    at the beginning of a meeting
  [bold deep_sky_blue1]mispy stop[/]     at the end — the minutes write themselves
  [bold deep_sky_blue1]mispy last[/]     opens what it wrote

  [bold deep_sky_blue1]mispy test[/]     eight seconds, right now, to be sure

Settings live in [grey62]{_short(path)}[/].
Names, products and acronyms it should never mishear go one per line in
[grey62]{_short(config.CONFIG_DIR / "glossary.txt")}[/] — that is the single best
thing you can do for the quality of the minutes.""", title="[bold]Done[/]", style="green")
    return 0
