"""Speech to text, after the meeting, on this machine. Never anywhere else.

Two engines, chosen by what the platform can actually offer:

  whisper.cpp     a compiled binary on the GPU through Metal. Fast, and already
                  there on a Mac where one `cmake` is all it takes.
  faster-whisper  a pip install and nothing else. No compiler, no admin rights,
                  no Visual Studio — which is what a locked-down work PC needs.

Minutes need timestamps in a way a course document does not: "we agreed at 00:41"
is checkable, "we agreed" is not. Both engines are made to produce timed lines.
"""
from __future__ import annotations
import pathlib, re, shutil, subprocess
from . import config, host

GLOSSARY = config.CONFIG_DIR / "glossary.txt"
WHISPER_CPP, FASTER = "whisper_cpp", "faster_whisper"


def whisper_cli() -> pathlib.Path:
    home = host.HOME
    names = [config.SHARE / "whisper.cpp/build/bin" / host.exe("whisper-cli")]
    if not host.WINDOWS:
        names += [home / ".local/share/kispy/whisper.cpp/build/bin/whisper-cli",
                  home / ".local/src/whisper.cpp/build/bin/whisper-cli",
                  home / "src/whisper.cpp/build/bin/whisper-cli"]
    for p in names:
        if p.exists():
            return p
    found = shutil.which(host.exe("whisper-cli"))
    if found:
        return pathlib.Path(found)
    raise RuntimeError("whisper-cli not found")


def have_faster() -> bool:
    try:
        import faster_whisper          # noqa: F401
        return True
    except Exception:
        return False


def engine(cfg: dict) -> str:
    want = (cfg["transcribe"].get("engine") or "auto").lower()
    if want in (WHISPER_CPP, FASTER):
        return want
    # The model says which engine it belongs to: a .bin file is whisper.cpp's
    # format, anything else is a faster-whisper model name. Guessing from what
    # happens to be installed would make the same config behave differently on
    # two machines, which is exactly what you do not want in a config file.
    return WHISPER_CPP if str(cfg["transcribe"].get("model", "")).endswith(".bin") else FASTER


def available(cfg: dict) -> tuple[bool, str]:
    """For `mispy doctor`: can this machine transcribe, and with what."""
    kind = engine(cfg)
    model = str(cfg["transcribe"].get("model", ""))
    if kind == WHISPER_CPP:
        try:
            whisper_cli()
        except RuntimeError as exc:
            return False, str(exc)
        if not pathlib.Path(model).exists():
            return False, f"model file missing: {model}"
        return True, f"whisper.cpp · {pathlib.Path(model).name}"
    if not have_faster():
        return False, "faster-whisper is not installed — re-run the installer"
    return True, f"faster-whisper · {model} · {_device(cfg)[0]}"


def glossary() -> str:
    """Names of colleagues, products, clients, internal acronyms. This is where
    mishearings are prevented; correcting them afterwards is guesswork, and in
    minutes a wrong name is worse than no name."""
    if not GLOSSARY.exists():
        return ""
    text = re.sub(r"#[^\n]*", " ", GLOSSARY.read_text(encoding="utf-8", errors="replace"))
    return re.sub(r"\s+", " ", text).strip()[:900]      # the model only reads so far


def _lines(segments, merge_under: float = 12.0) -> str:
    """(start, text) pairs into `[hh:mm:ss] sentence` lines, short neighbours
    merged so the transcript reads as speech rather than as subtitles."""
    out: list[tuple[float, list[str]]] = []
    for start, text in segments:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if out and start - out[-1][0] < merge_under and len(" ".join(out[-1][1])) < 400:
            out[-1][1].append(text)
        else:
            out.append((start, [text]))
    return "\n".join(f"[{int(t)//3600:02d}:{int(t)//60%60:02d}:{int(t)%60:02d}] "
                     + " ".join(parts) for t, parts in out)


# --------------------------------------------------------------- whisper.cpp ---
def _srt_segments(srt: str):
    for block in re.split(r"\n\s*\n", srt.strip()):
        m = re.search(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->", block)
        if not m:
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        yield start, " ".join(l.strip() for l in block.splitlines()[2:])


def _run_cpp(wav: pathlib.Path, cfg: dict, out_stem: pathlib.Path):
    t = cfg["transcribe"]
    cmd = [str(whisper_cli()), "-m", t["model"], "-f", str(wav),
           "-l", t["language"], "-t", str(t["threads"]),
           "-bs", "5", "-bo", "5", "-mc", "64", "-osrt", "-of", str(out_stem)]
    if pathlib.Path(t.get("vad_model", "")).exists():
        cmd += ["--vad", "-vm", t["vad_model"], "-vmsd", "30", "-vsd", "200"]
    prompt = glossary()
    if prompt:
        cmd += ["--prompt", prompt, "--carry-initial-prompt"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return list(_srt_segments(out_stem.with_suffix(".srt").read_text(encoding="utf-8")))


# ------------------------------------------------------------ faster-whisper ---
def _device(cfg: dict) -> tuple[str, str]:
    """(device, compute type). A work laptop almost never has CUDA; int8 on the
    CPU is what makes a one-hour meeting take minutes instead of hours."""
    want_dev = (cfg["transcribe"].get("device") or "auto").lower()
    want_ct = (cfg["transcribe"].get("compute") or "auto").lower()
    if want_dev == "auto":
        want_dev = "cpu"
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                want_dev = "cuda"
        except Exception:
            pass
    if want_ct == "auto":
        want_ct = "float16" if want_dev == "cuda" else "int8"
    return want_dev, want_ct


_MODEL_CACHE: dict = {}


def _run_faster(wav: pathlib.Path, cfg: dict):
    from faster_whisper import WhisperModel
    t = cfg["transcribe"]
    device, compute = _device(cfg)
    key = (t["model"], device, compute)
    model = _MODEL_CACHE.get(key)
    if model is None:
        config.MODELS.mkdir(parents=True, exist_ok=True)
        model = WhisperModel(t["model"], device=device, compute_type=compute,
                             cpu_threads=int(t["threads"]),
                             download_root=str(config.MODELS))
        _MODEL_CACHE[key] = model
    prompt = glossary()
    segments, _info = model.transcribe(
        str(wav), language=t["language"] or None, beam_size=5,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
        initial_prompt=prompt or None, condition_on_previous_text=True)
    return [(s.start, s.text) for s in segments]


# ------------------------------------------------------------------- public ---
def run(wav: pathlib.Path, cfg: dict, out_stem: pathlib.Path) -> str:
    kind = engine(cfg)
    segments = _run_cpp(wav, cfg, out_stem) if kind == WHISPER_CPP else _run_faster(wav, cfg)
    text = _lines(segments)
    if not cfg["transcribe"].get("timestamps", True):
        text = re.sub(r"\[\d{2}:\d{2}:\d{2}\]\s*", "", text)
    return text
