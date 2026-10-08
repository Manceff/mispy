"""Recording on macOS: the room through ffmpeg, the call through ScreenCaptureKit.

Two microphones, in effect: the room and the call.

A meeting at a table is one microphone. A meeting on Teams with headphones on is
the opposite problem — your own microphone hears only you, and everything that
matters is coming out of the speakers. Mispy can record both and mix them into
one track, which is the only arrangement that works for a call you are also
speaking in.

The room goes through ffmpeg. The call goes through ScreenCaptureKit, in the
small Swift helper next door, so there is no virtual audio device to install.
"""
from __future__ import annotations
import os, pathlib, signal, subprocess, time
from . import config, recorder

HELPER = config.BIN / "mispy-audio"


def devices() -> list[str]:
    """Audio inputs as macOS currently sees them."""
    return recorder.devices()


def system_available() -> bool:
    """Whether screen recording is permitted. The first call shows the macOS
    dialog; once refused, macOS will not ask again and only System Settings will
    do."""
    if not HELPER.exists():
        return False
    try:
        r = subprocess.run([str(HELPER), "--check"], capture_output=True, text=True, timeout=45)
        return r.stdout.strip() == "ok"
    except Exception:
        return False


def mic_path(work: pathlib.Path, index: int) -> pathlib.Path:
    return work / f"mic-{index:02d}.wav"


def sys_path(work: pathlib.Path, index: int) -> pathlib.Path:
    return work / f"sys-{index:02d}.wav"


def start(work: pathlib.Path, index: int, cfg: dict) -> dict:
    """Begin a segment. Returns the pids to stop later, one per track."""
    mode = cfg["audio"].get("capture", "mic")
    pids: dict[str, int] = {}

    if mode in ("mic", "both"):
        pid = recorder.start_at(mic_path(work, index), cfg["audio"]["device"],
                                cfg["audio"]["sample_rate"], work)
        pids["mic"] = pid

    if mode in ("system", "both"):
        try:
            pids["system"] = _start_system(sys_path(work, index), work)
        except Exception as exc:
            if mode == "system":                  # nothing else would be recorded
                for p in pids.values():
                    recorder.stop(p)
                raise RuntimeError(f"system audio unavailable: {exc}")
            # on "both", the room is still being recorded: carry on and say so
    if not pids:
        raise RuntimeError("nothing to record from")
    return pids


def _start_system(out: pathlib.Path, work: pathlib.Path) -> int:
    if not HELPER.exists():
        raise RuntimeError("mispy-audio is not installed")
    log = open(work / "system.log", "ab")
    proc = subprocess.Popen(["/usr/bin/nohup", "/usr/bin/caffeinate", "-i",
                             str(HELPER), str(out)],
                            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    time.sleep(1.2)                               # it fails fast, or not at all
    if proc.poll() is not None:
        tail = (work / "system.log").read_text(errors="replace").strip().splitlines()
        raise RuntimeError(tail[-1][:160] if tail else "the helper exited immediately")
    return proc.pid


def stop(pids: dict, work=None, index=None) -> None:
    for pid in (pids or {}).values():
        recorder.stop(pid)


def alive(pids: dict) -> bool:
    for pid in (pids or {}).values():
        try:
            os.killpg(os.getpgid(pid), 0)
            return True
        except (ProcessLookupError, PermissionError):
            continue
    return False


def _usable(p: pathlib.Path) -> bool:
    return p.exists() and p.stat().st_size > 4096


def mixdown(work: pathlib.Path, indices: list[int], out: pathlib.Path,
            rate: int = 16000) -> pathlib.Path:
    """One 16 kHz mono file out of every segment and every track.

    Mixed per segment rather than per track: the two recorders do not start on
    the same millisecond, and mixing whole tracks would stack that error up
    across a two-hour meeting.
    """
    pieces = []
    for i in indices:
        mic, sysa = mic_path(work, i), sys_path(work, i)
        has_mic, has_sys = _usable(mic), _usable(sysa)
        if not has_mic and not has_sys:
            continue
        piece = work / f"seg-{i:02d}.wav"
        if has_mic and has_sys:
            subprocess.run([
                "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                "-i", str(mic), "-i", str(sysa),
                "-filter_complex",
                f"[0:a]aresample={rate},pan=mono|c0=c0[a0];"
                f"[1:a]aresample={rate},pan=mono|c0=0.5*c0+0.5*c1[a1];"
                "[a0][a1]amix=inputs=2:duration=longest:normalize=0,"
                "dynaudnorm=f=250:g=7[out]",
                "-map", "[out]", "-ac", "1", "-ar", str(rate),
                "-c:a", "pcm_s16le", str(piece)], check=True)
        else:
            src = mic if has_mic else sysa
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                            "-i", str(src), "-ac", "1", "-ar", str(rate),
                            "-c:a", "pcm_s16le", str(piece)], check=True)
        pieces.append(piece)

    if not pieces:
        raise RuntimeError("no sound was captured")
    if len(pieces) == 1:
        pieces[0].replace(out)
        return out
    listing = work / "pieces.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in pieces), encoding="utf-8")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(out)], check=True)
    listing.unlink(missing_ok=True)
    return out


def minutes_captured(work: pathlib.Path, indices: list[int], rate: int = 16000) -> float:
    total = 0.0
    for i in indices:
        best = 0.0
        for p in (mic_path(work, i), sys_path(work, i)):
            if p.exists():
                # mic is mono 16-bit at `rate`; system is stereo 16-bit at 48 kHz
                per_sec = (2 * rate) if p.name.startswith("mic") else (2 * 2 * 48000)
                best = max(best, p.stat().st_size / per_sec)
        total += best
    return total / 60
