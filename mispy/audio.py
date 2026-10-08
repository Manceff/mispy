"""WAV arithmetic, in numpy, for platforms without ffmpeg in the loop.

macOS mixes with ffmpeg because ffmpeg is already there for the recording. On
Windows nothing is recorded through ffmpeg, so there is no reason to make a
locked-down work machine install it just to add two tracks together.
"""
from __future__ import annotations
import pathlib, wave

TARGET = 16000          # what whisper wants, on any engine


def read_wav(path: pathlib.Path):
    """-> (samples as float32 in [-1, 1], mono, sample rate)."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        rate, channels, width, frames = (w.getframerate(), w.getnchannels(),
                                         w.getsampwidth(), w.getnframes())
        raw = w.readframes(frames)
    if not raw:
        return np.zeros(0, dtype="float32"), rate
    dtype = {1: "u1", 2: "<i2", 4: "<i4"}.get(width)
    if dtype is None:
        raise RuntimeError(f"{path.name}: {width * 8}-bit WAV is not supported")
    data = np.frombuffer(raw, dtype=dtype).astype("float32")
    data /= {1: 128.0, 2: 32768.0, 4: 2147483648.0}[width]
    if width == 1:
        data -= 1.0
    if channels > 1:
        usable = (len(data) // channels) * channels
        data = data[:usable].reshape(-1, channels).mean(axis=1)
    return data, rate


def to_target(samples, rate: int):
    """Down to 16 kHz. An integer ratio is averaged, which is both a decimation
    and the low-pass it needs; anything else is interpolated."""
    import numpy as np
    if rate == TARGET or len(samples) == 0:
        return samples
    if rate % TARGET == 0:
        factor = rate // TARGET
        usable = (len(samples) // factor) * factor
        return samples[:usable].reshape(-1, factor).mean(axis=1)
    n = int(len(samples) * TARGET / rate)
    return np.interp(np.linspace(0, len(samples) - 1, n),
                     np.arange(len(samples)), samples).astype("float32")


def normalise(samples, target_rms: float = 0.06):
    """Bring a track to a usable level. A voice coming out of the speakers and a
    voice in the room almost never arrive at the same loudness, and whichever is
    quieter is the one the recogniser loses."""
    import numpy as np
    if len(samples) == 0:
        return samples
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < 1e-6:
        return samples
    gain = min(target_rms / rms, 20.0)
    return samples * gain


def mix(tracks: list, pad: bool = True):
    """Sum tracks of unequal length, then pull the peak back under full scale."""
    import numpy as np
    tracks = [t for t in tracks if len(t)]
    if not tracks:
        return np.zeros(0, dtype="float32")
    if len(tracks) == 1:
        out = tracks[0].copy()
    else:
        n = max(len(t) for t in tracks) if pad else min(len(t) for t in tracks)
        out = np.zeros(n, dtype="float32")
        for t in tracks:
            out[: len(t)] += t[:n]
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 0.95:
        out = out * (0.95 / peak)
    return out


def write_wav(path: pathlib.Path, samples, rate: int = TARGET) -> pathlib.Path:
    import numpy as np
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


def join(paths: list[pathlib.Path], out: pathlib.Path) -> pathlib.Path:
    """Concatenate finished 16 kHz mono segments."""
    import numpy as np
    pieces = []
    for p in paths:
        if p.exists() and p.stat().st_size > 44:
            s, r = read_wav(p)
            pieces.append(to_target(s, r))
    if not pieces:
        raise RuntimeError("no sound was captured")
    return write_wav(out, np.concatenate(pieces))


def duration(path: pathlib.Path) -> float:
    """Seconds, read from the header rather than guessed from the file size."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0
