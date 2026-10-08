"""Recording on Windows.

Nothing here goes through ffmpeg or a virtual audio cable. Windows already knows
how to hand a program the sound it is playing -- WASAPI loopback -- and the
`soundcard` package exposes it. That matters more than elegance on a work laptop:
there is no driver to install, no admin password, nothing for IT to approve.

The recorder runs as its own detached process so it survives the terminal, and
stops when a sentinel file appears next to its output. Signals on Windows are not
worth the trouble.
"""
from __future__ import annotations
import argparse, pathlib, sys, threading, time, wave
from . import audio, host

RECORD_RATE = 48000          # ask for what the hardware is already doing
CHUNK = 0.25                 # seconds per read


# ----------------------------------------------------------------- devices ----
def _sc():
    try:
        import soundcard
        return soundcard
    except Exception as exc:                     # noqa: BLE001 - reported to the user
        raise RuntimeError(
            "the `soundcard` package is missing or will not load "
            f"({str(exc)[:80]}) — re-run install.ps1") from exc


def devices() -> list[str]:
    """Microphones, as Windows currently sees them."""
    sc = _sc()
    return [m.name for m in sc.all_microphones(include_loopback=False)]


def loopbacks() -> list[str]:
    """Outputs that can be listened to: the speakers, the headset, the Teams
    device. Recording one of these is how the other people in a call are heard."""
    sc = _sc()
    out = []
    for m in sc.all_microphones(include_loopback=True):
        if getattr(m, "isloopback", False):
            out.append(m.name)
    return out


def default_loopback() -> str:
    sc = _sc()
    try:
        return sc.default_speaker().name
    except Exception:
        names = loopbacks()
        return names[0] if names else ""


def resolve(wanted: str, loopback: bool = False) -> str:
    """By name, never by position. Refuse rather than record the wrong thing."""
    available = loopbacks() if loopback else devices()
    if not wanted:
        if loopback:
            d = default_loopback()
            if d:
                return d
        raise RuntimeError("no audio device configured — run `mispy setup`")
    for name in available:
        if name.lower() == wanted.lower():
            return name
    for name in available:
        if wanted.lower() in name.lower() or name.lower() in wanted.lower():
            return name
    raise RuntimeError(f"device « {wanted} » not found; available: "
                       + (", ".join(available) or "none"))


# ------------------------------------------------------------------ paths -----
def mic_path(work: pathlib.Path, index: int) -> pathlib.Path:
    return work / f"mic-{index:02d}.wav"


def sys_path(work: pathlib.Path, index: int) -> pathlib.Path:
    return work / f"sys-{index:02d}.wav"


def stop_file(work: pathlib.Path, index: int) -> pathlib.Path:
    return work / f"stop-{index:02d}"


# ----------------------------------------------------------------- control ----
def start(work: pathlib.Path, index: int, cfg: dict) -> dict:
    """Spawn the detached recorder. Returns the pid to stop later."""
    mode = cfg["audio"].get("capture", "mic")
    stop_file(work, index).unlink(missing_ok=True)

    mic_name = ""
    if mode in ("mic", "both"):
        mic_name = resolve(cfg["audio"].get("device", ""))
    sys_name = ""
    if mode in ("system", "both"):
        try:
            sys_name = resolve(cfg["audio"].get("system_device", ""), loopback=True)
        except RuntimeError:
            if mode == "system":
                raise
            # on "both", the room is still being recorded: carry on without it

    log = open(work / "record.log", "ab")
    proc = host.detached([sys.executable, "-m", "mispy.capture_win", "--record",
                          "--work", str(work), "--index", str(index),
                          "--mic", mic_name, "--system", sys_name], log=log)
    time.sleep(1.5)                           # it fails fast, or not at all
    if proc.poll() is not None:
        tail = (work / "record.log").read_text(errors="replace").strip().splitlines()
        raise RuntimeError(tail[-1][:200] if tail else "the recorder exited immediately")
    tracks = {}
    if mic_name:
        tracks["mic"] = proc.pid
    if sys_name:
        tracks["system"] = proc.pid
    return tracks or {"mic": proc.pid}


def stop(pids: dict, work: pathlib.Path | None = None, index: int | None = None) -> None:
    if work is not None and index is not None:
        stop_file(work, index).write_text("", encoding="utf-8")
    deadline = time.time() + 12
    while time.time() < deadline:
        if not any(host.pid_alive(p) for p in (pids or {}).values()):
            return
        time.sleep(0.3)
    for p in set((pids or {}).values()):
        host.kill_tree(p)


def alive(pids: dict) -> bool:
    return any(host.pid_alive(p) for p in (pids or {}).values())


def mixdown(work: pathlib.Path, indices: list[int], out: pathlib.Path,
            rate: int = 16000) -> pathlib.Path:
    """One 16 kHz mono file out of every segment and every track, mixed per
    segment: the two recorders do not start on the same millisecond, and mixing
    whole tracks would carry that error across the whole meeting."""
    pieces = []
    for i in indices:
        tracks = []
        for p in (mic_path(work, i), sys_path(work, i)):
            if p.exists() and p.stat().st_size > 44:
                samples, r = audio.read_wav(p)
                if len(samples):
                    tracks.append(audio.normalise(audio.to_target(samples, r)))
        if tracks:
            piece = work / f"seg-{i:02d}.wav"
            audio.write_wav(piece, audio.mix(tracks))
            pieces.append(piece)
    if not pieces:
        raise RuntimeError("no sound was captured")
    return audio.join(pieces, out)


def minutes_captured(work: pathlib.Path, indices: list[int], rate: int = 16000) -> float:
    total = 0.0
    for i in indices:
        total += max(audio.duration(mic_path(work, i)), audio.duration(sys_path(work, i)))
    return total / 60


# ------------------------------------------------------------- the recorder ---
def _record(name: str, out: pathlib.Path, loopback: bool, stop: threading.Event) -> None:
    import numpy as np
    sc = _sc()
    mic = sc.get_microphone(name, include_loopback=loopback)
    channels = max(1, min(2, int(getattr(mic, "channels", 1) or 1)))
    frames = int(RECORD_RATE * CHUNK)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RECORD_RATE)
        with mic.recorder(samplerate=RECORD_RATE, channels=list(range(channels)),
                          blocksize=frames) as rec:
            while not stop.is_set():
                data = rec.record(numframes=frames)
                if data is None or len(data) == 0:
                    continue
                if getattr(data, "ndim", 1) > 1:
                    data = data.mean(axis=1)
                w.writeframes((np.clip(data, -1.0, 1.0) * 32767).astype("<i2").tobytes())


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="mispy.capture_win")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--work", required=True)
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("--mic", default="")
    ap.add_argument("--system", default="")
    args = ap.parse_args(argv)

    work = pathlib.Path(args.work)
    stop = threading.Event()
    threads = []
    host.prevent_sleep(True)
    try:
        if args.mic:
            threads.append(threading.Thread(target=_record, daemon=False,
                                            args=(args.mic, mic_path(work, args.index),
                                                  False, stop)))
        if args.system:
            threads.append(threading.Thread(target=_record, daemon=False,
                                            args=(args.system, sys_path(work, args.index),
                                                  True, stop)))
        if not threads:
            print("nothing to record from", file=sys.stderr)
            return 2
        for t in threads:
            t.start()
        time.sleep(0.8)
        if not any(t.is_alive() for t in threads):
            print("every recorder thread died on startup", file=sys.stderr)
            return 3
        marker = stop_file(work, args.index)
        while not marker.exists():
            if not any(t.is_alive() for t in threads):
                break                       # the device went away; stop cleanly
            time.sleep(0.2)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)
        host.prevent_sleep(False)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
