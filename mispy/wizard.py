"""`mispy setup` — the one interactive moment in Mispy's life.

Five minutes, once. Everything it asks is something it genuinely cannot work out
on its own, and every answer is pre-filled with what is already configured, so it
is safe to run again.
"""
from __future__ import annotations
import array, pathlib, shutil, subprocess, tempfile, time, wave
from . import capture, config, meeting, recorder, transcribe, ui

BREW_PACKAGES = {"ffmpeg": "ffmpeg"}
OPTIONAL = {"tectonic": "tectonic"}

LANGUAGES = [("fr", "French"), ("en", "English"), ("de", "German"),
             ("es", "Spanish"), ("it", "Italian"), ("nl", "Dutch")]


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
    missing = []
    for binary, formula in BREW_PACKAGES.items():
        (ui.good if _have(binary) else ui.bad)(binary if _have(binary) else f"{binary} is missing")
        if not _have(binary):
            missing.append(formula)
    if _have("tectonic"):
        ui.good("tectonic      minutes are also typeset as a PDF")
    else:
        ui.warn("tectonic is missing — you will get Markdown but no PDF")
        missing.append("tectonic")
    try:
        ui.good(f"whisper-cli   {_short(transcribe.whisper_cli())}")
    except Exception:
        ui.bad("whisper-cli is missing — re-run install.sh")

    model = pathlib.Path(config.load()["transcribe"]["model"])
    if model.exists():
        ui.good(f"speech model  {model.name}  ({model.stat().st_size / 1e9:.1f} GB)")
    else:
        ui.bad(f"speech model missing: {model.name}")
        ui.note("install.sh downloads it")
    for helper, what in ((capture.HELPER, "call audio"), (meeting.HELPER, "diary reader")):
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
        with wave.open(str(wav)) as w:
            frames = w.readframes(w.getnframes())
        a = array.array("h")
        a.frombytes(frames[: len(frames) // 2 * 2])
        return (max(abs(x) for x in a) / 32768) if a else 0.0
    except Exception:
        return 0.0


def step_capture(cfg: dict) -> dict:
    ui.heading("3/5", "How you meet")
    choice = ui.choose("Where does the sound come from?", [
        ("Around a table", "your microphone picks up the room"),
        ("On calls — Teams, Zoom, Meet", "what your Mac is playing, plus your own voice"),
        ("Both, depending on the day", "records the room and the call together"),
    ], default=3)
    cfg["audio"]["capture"] = {1: "mic", 2: "both", 3: "both"}[choice]

    cfg = _pick_microphone(cfg)
    if cfg["audio"]["capture"] in ("both", "system"):
        _check_system(cfg)

    lang = ui.ask("Language people speak in your meetings (fr, en, de, es…)",
                  cfg["transcribe"]["language"])
    cfg["transcribe"]["language"] = (lang or "fr").strip().lower()[:5]
    return cfg


def _pick_microphone(cfg: dict) -> dict:
    ui.console.print()
    try:
        devices = recorder.devices()
    except Exception as exc:
        ui.bad(f"cannot list the inputs: {exc}")
        return cfg
    if not devices:
        ui.bad("macOS reports no audio input at all")
        return cfg
    ui.note("chosen by name, never by number: indices shift as soon as an iPhone or "
            "a pair of AirPods appears, and that is how a meeting ends up recorded "
            "through a phone in someone's pocket")
    ui.console.print()
    for i, d in enumerate(devices, 1):
        ui.console.print(f"    [bold deep_sky_blue1]{i}[/]  {d}", highlight=False)
    ui.console.print()
    current = cfg["audio"]["device"]
    default = str(devices.index(current) + 1) if current in devices else "1"
    while True:
        a = ui.ask("Which microphone?", default)
        if a.isdigit() and 1 <= int(a) <= len(devices):
            cfg["audio"]["device"] = devices[int(a) - 1]
            break
    ui.good(cfg["audio"]["device"])

    ui.console.print()
    if not ui.confirm("Record four seconds to check it works?"):
        return cfg
    work = pathlib.Path(tempfile.mkdtemp())
    try:
        ui.say("[bold]Say something…[/]")
        pid = recorder.start(work, 1, cfg["audio"]["device"], cfg["audio"]["sample_rate"])
        time.sleep(4.5)
        recorder.stop(pid)
        peak = _peak(recorder.segment_path(work, 1))
        if peak < 0.01:
            ui.bad("nothing was captured")
            ui.note("System Settings ▸ Privacy & Security ▸ Microphone, and allow the "
                    "terminal you are running mispy from")
        else:
            ui.good(f"heard you  [green]{'▁▂▃▄▅▆▇█'[: max(1, int(peak * 8))]}[/]")
    except Exception as exc:
        ui.bad(str(exc)[:120])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return cfg


def _check_system(cfg: dict) -> None:
    ui.console.print()
    ui.say("For calls, Mispy records what your Mac is playing.")
    ui.note("macOS calls that screen recording, so it will ask for the permission by "
            "that name. Nothing from the screen is captured: the stream is two pixels "
            "wide and only its sound is read.")
    ui.console.print()
    if not capture.HELPER.exists():
        ui.bad("mispy-audio is not installed — re-run install.sh")
        return
    for attempt in range(4):
        with ui.console.status("  [grey62]asking macOS…[/]", spinner="dots"):
            ok = capture.system_available()
        if ok:
            ui.good("call audio is allowed")
            return
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


# --------------------------------------------------------------------------- 4
def step_output(cfg: dict) -> dict:
    ui.heading("4/5", "Where the minutes go")
    root = ui.ask("Folder for your meetings", _short(cfg["output"]["root"]))
    path = pathlib.Path(root).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    cfg["output"]["root"] = str(path)
    ui.good(_short(path))
    ui.note("one folder per meeting, named by date — a Markdown file to paste "
            "anywhere, and a PDF to keep")
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
    cfg["output"]["organisation"] = ui.ask("Printed on the PDF (optional)",
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
        ui.note("you will name each meeting yourself: mispy start \"Weekly\"")
        return cfg
    cfg["calendar"]["enabled"] = True
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
[grey62]~/.config/mispy/glossary.txt[/] — that is the single best thing you can do
for the quality of the minutes.""", title="[bold]Done[/]", style="green")
    return 0
