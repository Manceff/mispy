"""One capture interface, two implementations.

  macOS    ffmpeg on the microphone, ScreenCaptureKit on the call audio
  Windows  WASAPI, both tracks, through the `soundcard` package

Everything above this line in the call stack -- the pipeline, the CLI, the app --
only ever sees these names.
"""
from __future__ import annotations
import pathlib
from . import host

if host.WINDOWS:
    from . import capture_win as _backend
else:
    from . import capture_mac as _backend

devices = _backend.devices
start = _backend.start
alive = _backend.alive
mixdown = _backend.mixdown
minutes_captured = _backend.minutes_captured
mic_path = _backend.mic_path
HELPER = getattr(_backend, "HELPER", None)   # macOS: the ScreenCaptureKit helper
sys_path = _backend.sys_path


def stop(pids: dict, work: pathlib.Path | None = None, index: int | None = None) -> None:
    _backend.stop(pids, work, index)


def resolve(name: str, loopback: bool = False) -> str:
    """Turn a configured device name into the one actually present, or refuse."""
    if host.WINDOWS:
        return _backend.resolve(name, loopback=loopback)
    if loopback:
        if _backend.system_available():
            return "system audio"
        raise RuntimeError(system_hint())
    from . import recorder
    return recorder.resolve(name)


def system_available() -> bool:
    """Whether the other people in a call can be recorded at all."""
    if host.WINDOWS:
        try:
            return bool(_backend.loopbacks())
        except Exception:
            return False
    return _backend.system_available()


def system_devices() -> list[str]:
    """Outputs that can be listened to. macOS has exactly one route and no choice
    to offer; Windows has one per output device."""
    if host.WINDOWS:
        try:
            return _backend.loopbacks()
        except Exception:
            return []
    return ["system audio"] if _backend.system_available() else []


def system_hint() -> str:
    """What to tell someone whose call audio will not start."""
    if host.WINDOWS:
        return ("no output device reports a loopback. Check that something is set as "
                "the default playback device in Windows sound settings.")
    return ("allow Screen Recording for your terminal in System Settings ▸ Privacy & "
            "Security, then quit and reopen it.")
