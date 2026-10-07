"""Speech to text, after the meeting, with whisper.cpp. Local, on the GPU.

Minutes need timestamps in a way a course document does not: "we agreed at 00:41"
is checkable, "we agreed" is not. The transcript is therefore kept as timed lines.
"""
from __future__ import annotations
import pathlib, re, shutil, subprocess
from . import config

GLOSSARY = config.CONFIG_DIR / "glossary.txt"


def whisper_cli() -> pathlib.Path:
    home = pathlib.Path.home()
    for p in (config.SHARE / "whisper.cpp/build/bin/whisper-cli",
              home / ".local/share/kispy/whisper.cpp/build/bin/whisper-cli",
              home / ".local/src/whisper.cpp/build/bin/whisper-cli",   # built by hand
              home / "src/whisper.cpp/build/bin/whisper-cli"):
        if p.exists():
            return p
    found = shutil.which("whisper-cli")
    if found:
        return pathlib.Path(found)
    raise RuntimeError("whisper-cli not found -- re-run install.sh")


def glossary() -> str:
    """Names of colleagues, products, clients, internal acronyms. This is where
    mishearings are prevented; correcting them afterwards is guesswork, and in
    minutes a wrong name is worse than no name."""
    if not GLOSSARY.exists():
        return ""
    return re.sub(r"\s+", " ", GLOSSARY.read_text(encoding="utf-8")).strip()


def _srt_to_lines(srt: str, merge_under: float = 12.0) -> str:
    """SRT into `[mm:ss] sentence` lines, short neighbours merged so the
    transcript reads as speech rather than as subtitles."""
    blocks = re.split(r"\n\s*\n", srt.strip())
    out: list[tuple[float, list[str]]] = []
    for b in blocks:
        m = re.search(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->", b)
        if not m:
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        text = " ".join(l.strip() for l in b.splitlines()[2:]).strip()
        if not text:
            continue
        if out and start - out[-1][0] < merge_under and len(" ".join(out[-1][1])) < 400:
            out[-1][1].append(text)
        else:
            out.append((start, [text]))
    return "\n".join(f"[{int(t)//3600:02d}:{int(t)//60%60:02d}:{int(t)%60:02d}] "
                     + re.sub(r"\s+", " ", " ".join(parts)).strip()
                     for t, parts in out)


def run(wav: pathlib.Path, cfg: dict, out_stem: pathlib.Path) -> str:
    t = cfg["transcribe"]
    cmd = [
        str(whisper_cli()), "-m", t["model"], "-f", str(wav),
        "-l", t["language"], "-t", str(t["threads"]),
        "-bs", "5", "-bo", "5", "-mc", "64",
        "-osrt", "-of", str(out_stem),
    ]
    if pathlib.Path(t.get("vad_model", "")).exists():
        cmd += ["--vad", "-vm", t["vad_model"], "-vmsd", "30", "-vsd", "200"]
    prompt = glossary()
    if prompt:
        cmd += ["--prompt", prompt, "--carry-initial-prompt"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srt = out_stem.with_suffix(".srt").read_text(encoding="utf-8")
    if not t.get("timestamps", True):
        return re.sub(r"\[\d{2}:\d{2}:\d{2}\]\s*", "", _srt_to_lines(srt))
    return _srt_to_lines(srt)
