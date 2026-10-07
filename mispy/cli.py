"""The command surface. One command to start, one to stop, and that is the job."""
from __future__ import annotations
import datetime, pathlib, subprocess, sys, time
from . import capture, config, meeting, pipeline, recorder, state

PY = sys.executable
LIB = str(config.LIB)


def start(argv: list[str] | None = None) -> int:
    cfg = config.load()
    job = state.load()
    argv = argv or []

    if job["stage"] == state.RECORDING and capture.alive(job.get("pids")):
        print("● Already recording.")
        return 0
    if job["stage"] in state.RUNNING_STAGES + (state.DETAILS,):
        print(f"● Busy: {state.HUMAN[job['stage']]}.")
        return 0

    work = config.work_dir()
    if job["stage"] == state.PAUSED:                       # another segment
        index = len(job.get("segments", [])) + 1
    else:
        index = 1
        now = datetime.datetime.now()
        given = " ".join(argv).strip()
        attendees: list[str] = []
        title = given
        if not title:
            event = meeting.best(now, cfg)
            if event:
                title = (event.get("title") or "").strip()
                attendees = [a for a in (event.get("attendees") or []) if a][:25]
        provisional = title or f"Meeting {now:%H:%M}"
        folder = meeting.folder(provisional, now, pathlib.Path(cfg["output"]["root"]))
        job = state.save({"stage": state.IDLE, "title": provisional,
                          "title_given": given, "attendees": attendees,
                          "date": f"{now:%Y-%m-%d}", "folder": str(folder),
                          "segments": [], "pids": {},
                          "started_at": now.isoformat(timespec="seconds")})

    try:
        pids = capture.start(work, index, cfg)
    except RuntimeError as exc:
        state.set_stage(state.FAILED, error=str(exc))
        print(f"✗ {exc}")
        return 1
    segs = job.get("segments", []) + [index]
    state.set_stage(state.RECORDING, pids=pids, segments=segs, error=None,
                    segment_started=time.time())
    tracks = " + ".join(sorted(pids))
    if index == 1:
        print(f"● {job['title']} — recording ({tracks}).")
        if cfg["audio"]["capture"] == "both" and "system" not in pids:
            print("  ⚠ system audio did not start — only the room is being recorded.")
    else:
        print(f"● Resumed — segment {index} ({tracks}).")
    print("  `mispy stop` when the meeting is over. You can close the terminal.")
    return 0


def pause() -> int:
    job = state.load()
    if job["stage"] != state.RECORDING:
        return 1
    capture.stop(job.get("pids"))
    state.set_stage(state.PAUSED, pids={})
    print("⏸ Paused.  `mispy start` resumes.")
    return 0


def stop() -> int:
    job = state.load()
    if job["stage"] not in (state.RECORDING, state.PAUSED):
        print("Nothing is being recorded.")
        return 1
    if job["stage"] == state.RECORDING:
        capture.stop(job.get("pids"))
    captured = capture.minutes_captured(config.work_dir(), job.get("segments", []),
                                        config.load()["audio"]["sample_rate"])
    state.set_stage(state.DETAILS, pids={}, minutes_captured=captured)
    subprocess.Popen([PY, "-c", f"import sys; sys.path.insert(0, {LIB!r}); "
                                "from mispy import cli; cli.work()"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    print(f"■ Stopped — {captured:.0f} min. The minutes are being written.")
    print("  You can close the terminal, and your Mac.")
    return 0


def discard() -> int:
    """Throw the current recording away. A test, a false start, or a conversation
    that should not have been recorded after all."""
    import shutil
    from . import shred
    job = state.load()
    if job.get("stage", state.IDLE) == state.IDLE:
        print("Nothing to discard.")
        return 1
    if job.get("stage") == state.RECORDING:
        capture.stop(job.get("pids"))
    subprocess.run(["pkill", "-f", "cli.work()"], capture_output=True)
    shred.clean(config.work_dir())
    folder = pathlib.Path(job.get("folder", ""))
    if folder.is_dir() and not [p for p in folder.iterdir() if p.name != ".mispy"]:
        shutil.rmtree(folder, ignore_errors=True)        # only if we left it empty
    state.clear()
    print("✗ Recording discarded. Nothing was kept.")
    return 0


def work() -> int:
    pipeline.run(state.load())
    return 0


def last() -> int:
    job = state.load()
    path = job.get("pdf") or job.get("markdown")
    if not path or not pathlib.Path(path).exists():
        print("No minutes yet.")
        return 1
    subprocess.run(["open", path], check=False)
    print(path)
    return 0


def status() -> int:
    job = state.load()
    stage = job.get("stage", state.IDLE)
    label = state.HUMAN.get(stage, stage)
    if stage == state.IDLE:
        print(f"Mispy — {label}." + (f"\n{job['error']}." if job.get("error") else ""))
        return 0
    print(f"Mispy — {label}\n{job.get('title', '?')} · {job.get('date', '')}")

    if stage in (state.RECORDING, state.PAUSED):
        cfg = config.load()
        mins = capture.minutes_captured(config.work_dir(), job.get("segments", []),
                                        cfg["audio"]["sample_rate"])
        tracks = ", ".join(sorted(job.get("pids", {}))) or "—"
        print(f"segment {len(job.get('segments', []))} · {mins:.0f} min · {tracks}")
        if stage == state.RECORDING and not capture.alive(job.get("pids")):
            print("⚠ the capture is not running — `mispy start` restarts it")
        else:
            print("All OK.")
    elif stage == state.DONE:
        print(job.get("markdown", ""))
        if job.get("pdf"):
            print(job["pdf"])
        for n in job.get("notes") or []:
            print(f"  · {n}")
    elif stage == state.FAILED:
        print(f"⚠ {job.get('error', '')}\nThe audio is kept: `mispy stop` runs it again.")
    else:
        print("All OK. You can close your Mac, it resumes on wake.")
    return 0


def selftest() -> int:
    """Record eight seconds and transcribe them. The point is to trigger the
    permission prompts and prove the chain works before a real meeting."""
    import tempfile
    from . import transcribe
    cfg = config.load()
    work = pathlib.Path(tempfile.mkdtemp())
    try:
        pids = capture.start(work, 1, cfg)
    except RuntimeError as exc:
        print(f"✗ {exc}")
        return 1
    print(f"Mispy — testing {', '.join(sorted(pids))}. Speak for 8 seconds…")
    time.sleep(8)
    capture.stop(pids)
    try:
        wav = capture.mixdown(work, [1], work / "t.wav", cfg["audio"]["sample_rate"])
    except Exception as exc:
        print(f"✗ {exc}")
        print("  System Settings ▸ Privacy & Security ▸ Microphone, and allow")
        print("  the terminal you run mispy from.")
        return 1
    print(f"✓ {wav.stat().st_size / (2 * cfg['audio']['sample_rate']):.0f} s captured. "
          "Transcribing…")
    try:
        text = transcribe.run(wav, cfg, work / "t")
    except Exception as exc:
        print(f"✗ transcription failed: {exc}")
        return 1
    print(f"\n  « {text.strip()[:300] or '(silence)'} »\n")
    print("All set." if text.strip() else "It recorded, but heard nothing.")
    for f in work.iterdir():
        f.unlink(missing_ok=True)
    work.rmdir()
    return 0


def doctor() -> int:
    from . import wizard
    cfg = config.load()
    ok = True

    def check(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  {'✓' if good else '✗'} {name}" + (f"  {detail}" if detail else ""))

    print("Mispy")
    check("configuration", config.exists(),
          "" if config.exists() else "never set up — run `mispy setup`")
    mode = cfg["audio"]["capture"]
    print(f"  · capture mode  {mode}")
    if mode in ("mic", "both"):
        try:
            check("microphone", True, recorder.resolve(cfg["audio"]["device"]))
        except RuntimeError as exc:
            check("microphone", False, str(exc)[:90])
    if mode in ("system", "both"):
        good = capture.system_available()
        check("system audio", good,
              "" if good else "allow Screen Recording for your terminal in System Settings")
    check("speech model", pathlib.Path(cfg["transcribe"]["model"]).exists(),
          pathlib.Path(cfg["transcribe"]["model"]).name)
    for binary, hint in (("ffmpeg", "brew install ffmpeg"),):
        found = subprocess.run(["which", binary], capture_output=True).returncode == 0
        check(binary, found, "" if found else hint)
    tectonic = subprocess.run(["which", "tectonic"], capture_output=True).returncode == 0
    print(f"  {'✓' if tectonic else '·'} tectonic"
          + ("" if tectonic else "  not installed — Markdown only, no PDF"))
    st = wizard.claude_state()
    check("Claude", st == "ok",
          {"ok": "", "not-installed": "npm install -g @anthropic-ai/claude-code",
           "not-logged-in": "run `claude` once, then /login"}[st])
    check("output folder", pathlib.Path(cfg["output"]["root"]).exists(),
          cfg["output"]["root"].replace(str(pathlib.Path.home()), "~"))
    if cfg["calendar"].get("enabled", True):
        try:
            n = len(meeting.around(datetime.datetime.now(), cfg))
            print(f"  ✓ calendar  {n} event(s) around now")
        except Exception as exc:
            print(f"  · calendar  unavailable ({str(exc)[:50]}) — you can still name "
                  "meetings yourself")
    return 0 if ok else 1


HELP = """\
Mispy — records your meetings and hands you back the minutes.

  mispy                    open the app
  mispy start              start recording; names itself from your diary
  mispy start "Weekly"     ...or give it a name
  mispy stop               finish, and write the minutes
  mispy pause              pause; `start` resumes on a new segment
  mispy discard            throw the recording away, keep nothing
  mispy status             what it is doing right now
  mispy last               open the minutes it wrote last

  mispy setup              connect Claude, the microphone, the call audio
  mispy doctor             check everything is wired
  mispy test               record eight seconds and transcribe them

Settings:  ~/.config/mispy/config.toml
Names and jargon it should not mishear:  ~/.config/mispy/glossary.txt
"""


def app() -> int:
    from . import tui
    return tui.run()


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "").lower()
    if cmd in ("setup", "install"):
        from . import wizard
        return wizard.run(argv[2:])
    if cmd in ("help", "-h", "--help"):
        print(HELP)
        return 0
    if cmd in ("", "start", "go", "resume", "record") and not config.exists():
        print("Mispy is not set up yet.\n\n  mispy setup\n")
        return 1
    if cmd in ("start", "go", "record"):
        return start(argv[2:])
    if cmd == "resume":
        return start([])
    fn = {"": app, "pause": pause, "stop": stop, "status": status, "last": last,
          "doctor": doctor, "test": selftest, "discard": discard,
          "__work": work}.get(cmd)
    if fn is None:
        print(f"Unknown command: {cmd}\n")
        print(HELP)
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
