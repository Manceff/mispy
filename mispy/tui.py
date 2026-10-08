"""Mispy's face. One screen, five keys, and a creature whose expression tells you
what is going on without you having to read."""
from __future__ import annotations
import contextlib, io, os, sys, time

from . import host

if host.WINDOWS:
    import msvcrt
else:
    import select, termios, tty

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import capture, cli, config, state

WORDMARK = r"""╔╦╗ ╦ ╔═╗ ╔═╗ ╦ ╦
║║║ ║ ╚═╗ ╠═╝ ╚╦╝
╩ ╩ ╩ ╚═╝ ╩     ╩"""

# Each mood is a palette plus a face -- the body takes the mood colour, the
# cheeks stay pink, the sparkles stay gold, so it reads at a glance.
FACES = {
    "idle":   dict(body="sky_blue3",       eyes="grey54",        eyeL="–", eyeR="–",
                   mouth="·", left="    ", right="  ᶻ ", spark="",
                   caption="nothing on"),
    "ready":  dict(body="pale_turquoise1", eyes="deep_sky_blue1", eyeL="◕", eyeR="◕",
                   mouth="‿", left="    ", right="    ", spark="✦",
                   caption="ready when you are"),
    "record": dict(body="light_salmon1",   eyes="deep_pink2",    eyeL="◕", eyeR="◕",
                   mouth="○", left="))  ", right="  ((", spark="",
                   caption="listening"),
    "paused": dict(body="khaki1",          eyes="orange3",       eyeL="˘", eyeR="˘",
                   mouth="‿", left="    ", right="    ", spark="",
                   caption="holding"),
    "work":   dict(body="plum2",           eyes="medium_purple1", eyeL="◔", eyeR="◔",
                   mouth="~", left="    ", right="  ✎ ", spark="",
                   caption="writing it up"),
    "done":   dict(body="pale_green1",     eyes="spring_green1", eyeL="^", eyeR="^",
                   mouth="‿", left="    ", right="    ", spark="✦",
                   caption="minutes ready"),
    "sad":    dict(body="grey62",          eyes="indian_red1",   eyeL="×", eyeR="×",
                   mouth="⌒", left="    ", right="    ", spark="",
                   caption="something went wrong"),
}

CHEEK, SPARK = "hot_pink2", "gold1"

STAGE_FACE = {
    state.IDLE: "idle", state.RECORDING: "record", state.PAUSED: "paused",
    state.DETAILS: "work", state.TRANSCRIBING: "work",
    state.SYNTHESISING: "work", state.BUILDING: "work",
    state.DONE: "done", state.FAILED: "sad",
}
BLINKING = {"ready", "record", "work", "done"}


def creature(kind: str, frame: int = 0) -> Text:
    """Drawn on a fixed grid so nothing shifts between moods. `frame` advances
    four times a second -- it blinks, and its sparkles twinkle."""
    f = dict(FACES[kind])
    if kind in BLINKING and frame % 20 == 0:
        f["eyeL"] = f["eyeR"] = "–"
    if f["spark"] and frame % 6 < 3:
        f["spark"] = "·"
    if kind == "record":
        f["left"], f["right"] = (("))  ", "  ((") if frame % 4 < 2 else ((" )  ", "  ( ")))
    body, eyes, spark = f["body"], f["eyes"], f["spark"]
    t = Text()

    def line(*segments):
        for txt, style in segments:
            t.append(txt, style=style)
        t.append("\n")

    sp = spark or " "
    line(("  " + sp + "  ", SPARK), ("╭╮", body), ("     ", None), ("╭╮", body),
         ("  " + sp, SPARK))
    line(("    ", None), ("╭╯╰─────╯╰╮", body))
    line((f["left"], body),
         ("│  ", body), (f["eyeL"], f"bold {eyes}"), ("   ", None),
         (f["eyeR"], f"bold {eyes}"), ("  │", body),
         (f["right"], body))
    line(("    ", None), ("│ ", body), ("◦", f"bold {CHEEK}"), ("  ", None),
         (f["mouth"], f"bold {eyes}"), ("  ", None), ("◦", f"bold {CHEEK}"),
         (" │", body))
    line(("    ", None), ("╰─┬─────┬─╯", body))
    t.append("      ", style=None)
    t.append("╰╯", style=body)
    t.append("    ", style=None)
    t.append("╰╯", style=body)
    return t


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def elapsed_of(job: dict) -> str:
    try:
        mins = capture.minutes_captured(config.work_dir(), job.get("segments", []))
    except Exception:
        mins = 0.0
    secs = int(mins * 60)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def screen(job: dict, frame: int = 0, confirming: bool = False) -> Panel:
    stage = job.get("stage", state.IDLE)
    kind = STAGE_FACE.get(stage, "idle")
    if stage == state.IDLE and job.get("title"):
        kind = "ready"
    colour, caption = FACES[kind]["body"], FACES[kind]["caption"]

    head = Text(WORDMARK, style=f"bold {colour}")
    head.append(f"\n\n Mispy, {caption}", style="italic grey62")

    top = Table.grid(padding=(0, 2))
    top.add_column(width=32, no_wrap=True)     # fixed, so the creature never shifts
    top.add_column(no_wrap=True)
    top.add_row(head, creature(kind, frame))

    lines = Text()
    if job.get("title"):
        lines.append(f"\n  {job['title'][:46]}\n", style="bold white")
        bits = [b for b in (job.get("date"),
                            ", ".join(job.get("attendees", [])[:3])[:40] or None) if b]
        if bits:
            lines.append("  " + "  ·  ".join(bits) + "\n", style="grey62")
    else:
        lines.append("\n  No meeting in memory.\n", style="grey62")

    lines.append("\n  ")
    lines.append("●" if stage == state.RECORDING else "○", style=colour)
    lines.append(f" {state.HUMAN.get(stage, stage)}", style=f"bold {colour}")
    if stage in (state.RECORDING, state.PAUSED):
        lines.append(f"        {elapsed_of(job)}", style="bold white")
        tracks = ", ".join(sorted(job.get("pids", {}))) or "—"
        lines.append(f"   {tracks}", style="grey62")
    lines.append("\n")

    if stage == state.DONE and job.get("markdown"):
        for key in ("markdown", "docx", "pdf"):
            if job.get(key):
                lines.append(f"\n  {os.path.basename(job[key])}" if key == "markdown"
                             else f"  {os.path.basename(job[key])}", style="spring_green3")
                lines.append("\n", style="spring_green3")
        for n in (job.get("notes") or [])[:3]:
            lines.append(f"   · {n[:62]}\n", style="gold3")
        lines.append("  O to open it.\n", style="grey62")
    if stage == state.FAILED and job.get("error"):
        lines.append(f"\n  {job['error'][:70]}\n", style="red3")
        lines.append("  The audio is kept — press T to try again.\n", style="grey62")
    if stage in (state.TRANSCRIBING, state.SYNTHESISING, state.BUILDING, state.DETAILS):
        lines.append("\n  You can close the lid; it resumes on wake.\n", style="grey62")

    if confirming:
        lines.append("\n  Discard this recording? ", style="bold red3")
        lines.append("Y", style="bold reverse red3")
        lines.append(" to confirm, any other key to cancel.\n", style="red3")

    keys = Text("\n  ")
    for key, label, on in (("L", "start", stage in (state.IDLE, state.PAUSED, state.FAILED)),
                           ("P", "pause", stage == state.RECORDING),
                           ("T", "stop", stage in (state.RECORDING, state.PAUSED)),
                           ("X", "discard", stage != state.IDLE),
                           ("O", "open", stage == state.DONE),
                           ("Q", "quit", True)):
        keys.append(f" {key} ", style=("reverse bold" if on else "dim"))
        keys.append(f" {label}   ", style=("white" if on else "dim"))

    return Panel(Group(top, lines, keys), border_style=colour,
                 title="[grey62]minutes, without taking them[/]", title_align="right",
                 padding=(1, 3))


def key_pressed(timeout: float) -> str | None:
    """One keystroke, or None if the time ran out.

    On Unix, read the raw descriptor and never sys.stdin: a buffered reader drains
    the fd into its own buffer, after which select() reports nothing and every
    later keystroke is silently swallowed.
    """
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    if host.WINDOWS:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):       # a function or arrow key: swallow it
                    msvcrt.getwch()
                    return None
                return ch.lower()
            time.sleep(0.03)
        return None
    fd = sys.stdin.fileno()
    if select.select([fd], [], [], timeout)[0]:
        data = os.read(fd, 1)
        return data.decode("utf-8", "ignore").lower() if data else ""
    return None


def run() -> int:
    console = Console()
    if not sys.stdin.isatty():
        console.print(screen(state.load(), 3))
        return 0

    fd = None if host.WINDOWS else sys.stdin.fileno()
    saved = None if host.WINDOWS else termios.tcgetattr(fd)
    try:
        if not host.WINDOWS:
            tty.setcbreak(fd)
        frame, confirming = 0, False
        with Live(screen(state.load()), console=console, refresh_per_second=4,
                  screen=True) as live:
            while True:
                frame += 1
                job = state.load()
                stage = job.get("stage", state.IDLE)
                k = key_pressed(0.5)
                if confirming:
                    if k is not None:
                        if k == "y":
                            quiet(cli.discard)
                        confirming = False
                    live.update(screen(state.load(), frame, confirming))
                    continue
                if k == "q" or k == "":
                    break
                if k == "l" and stage in (state.IDLE, state.PAUSED, state.FAILED):
                    quiet(cli.start)
                elif k == "p" and stage == state.RECORDING:
                    quiet(cli.pause)
                elif k == "t" and stage in (state.RECORDING, state.PAUSED):
                    quiet(cli.stop, False)       # no inline questions: we own stdin
                elif k == "o" and stage == state.DONE:
                    quiet(cli.last)
                elif k == "x" and stage != state.IDLE:
                    confirming = True
                live.update(screen(state.load(), frame, confirming))
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    job = state.load()
    if job.get("stage") in (state.RECORDING,) + state.RUNNING_STAGES + (state.DETAILS,):
        console.print("[grey62]Mispy keeps going in the background — [/][bold]mispy[/]"
                      "[grey62] brings you back.[/]")
    return 0
