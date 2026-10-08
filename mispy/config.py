"""Configuration: defaults, overridable from the config file.

  macOS    ~/.config/mispy/config.toml
  Windows  %APPDATA%\\Mispy\\config.toml
"""
from __future__ import annotations
import os, pathlib, tomllib
from . import host

HOME = host.HOME
CONFIG_DIR = host.config_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"
LIB = pathlib.Path(__file__).resolve().parent.parent
SHARE = host.data_dir()
MODELS = SHARE / "models"
BIN = host.bin_dir()

# The engine differs by platform for one reason: whisper.cpp needs compiling, which
# on a Mac is one cmake away and on a locked-down work PC is a morning lost.
# faster-whisper is a pip install and nothing else.
DEFAULT_MODEL = "large-v3-turbo" if host.WINDOWS else str(MODELS / "ggml-large-v3.bin")
DEFAULT_FORMATS = ["md", "docx"] if host.WINDOWS else ["md", "pdf"]

DEFAULTS: dict = {
    "audio": {
        # mic = the room. system = what this machine is playing, i.e. the other
        # people in a call. both = mixed into one track, which is what you want on
        # a call you are also speaking in.
        "capture": "mic",          # mic | system | both
        "device": "",              # input NAME; empty = ask at setup
        "system_device": "",       # Windows: which output to listen to; empty = default
        "sample_rate": 16000,
    },
    "transcribe": {
        "engine": "auto",          # auto | whisper_cpp | faster_whisper
        "model": DEFAULT_MODEL,
        "vad_model": str(MODELS / "ggml-silero-v6.2.0.bin"),   # whisper.cpp only
        "compute": "auto",         # faster-whisper: auto | int8 | int8_float16 | float16
        "device": "auto",          # faster-whisper: auto | cpu | cuda
        "language": "fr",
        "threads": 0,              # 0 = one per performance core
        "timestamps": True,
    },
    "minutes": {
        "backend": "claude_cli",   # claude_cli (subscription) | api (ANTHROPIC_API_KEY)
        "model": "claude-opus-5",
        "timeout_seconds": 1800,
        "language": "auto",        # auto = the language the meeting was held in
        "style": "standard",       # standard | brief | verbatim
    },
    "calendar": {
        "enabled": True,           # name the meeting from what is in your diary
        "source": "auto",          # auto | eventkit | outlook | none
        "calendars": [],           # macOS: which calendars; [] = all of them
        "lookaround_minutes": 30,
    },
    "privacy": {
        "keep_audio": False,       # destroyed once the minutes exist
        "keep_transcript": False,
    },
    "output": {
        "root": str(host.documents() / "Meetings"),
        "formats": DEFAULT_FORMATS,     # md, docx, pdf — in any combination
        "organisation": "",
    },
    "runtime": {
        "state_dir": str(host.state_dir()),
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load() -> dict:
    cfg = DEFAULTS
    if CONFIG_PATH.exists():
        try:
            cfg = _merge(cfg, tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"{CONFIG_PATH} is not valid TOML: {exc}")
    for section in ("output", "runtime", "transcribe"):
        for key, val in cfg[section].items():
            if isinstance(val, str) and val.startswith("~"):
                cfg[section][key] = str(pathlib.Path(val).expanduser())
    if not cfg["transcribe"]["threads"]:
        cfg["transcribe"]["threads"] = max(4, (os.cpu_count() or 8) - 2)
    if "pdf" in cfg["output"] and isinstance(cfg["output"]["pdf"], bool):
        want = set(cfg["output"].get("formats") or [])        # an older config file
        cfg["output"]["formats"] = sorted(want | {"pdf"}) if cfg["output"]["pdf"] \
            else sorted(want - {"pdf"})
    return cfg


def formats(cfg: dict) -> list[str]:
    out = [f.lower().lstrip(".") for f in (cfg["output"].get("formats") or ["md"])]
    return [f for f in ("md", "docx", "pdf") if f in out] or ["md"]


def exists() -> bool:
    return CONFIG_PATH.exists()


def state_dir() -> pathlib.Path:
    d = pathlib.Path(load()["runtime"]["state_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir() -> pathlib.Path:
    """Where audio and transcripts live while the job runs. Never the output folder."""
    d = state_dir() / "work"
    d.mkdir(parents=True, exist_ok=True)
    if not host.WINDOWS:
        os.chmod(d, 0o700)
    return d


def write(cfg: dict) -> pathlib.Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    a, t, m, o, p = (cfg["audio"], cfg["transcribe"], cfg["minutes"],
                     cfg["output"], cfg["privacy"])

    def tilde(v: str) -> str:
        s = str(v)
        return s.replace(str(HOME), "~", 1) if s.startswith(str(HOME)) else s

    def q(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return "[" + ", ".join(f'"{x}"' for x in v) + "]"
        if isinstance(v, (int, float)):
            return str(v)
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    CONFIG_PATH.write_text(f"""# Mispy — written by `mispy setup`, yours to edit.

[audio]
# mic    = the room, through your microphone
# system = what this machine is playing: the other people on a call
# both   = the two mixed into one track
capture = {q(a["capture"])}
# By NAME, never by index: indices shift as soon as a headset or a phone shows up,
# and Mispy refuses to start rather than record the wrong one.
device        = {q(a["device"])}
system_device = {q(a.get("system_device", ""))}

[transcribe]
engine   = {q(t["engine"])}   # auto | whisper_cpp | faster_whisper
model    = {q(tilde(t["model"]))}
language = {q(t["language"])}   # what people speak in your meetings
compute  = {q(t.get("compute", "auto"))}   # faster-whisper: auto | int8 | float16
device   = {q(t.get("device", "auto"))}    # faster-whisper: auto | cpu | cuda

[minutes]
backend  = {q(m["backend"])}   # claude_cli = your Claude subscription
language = {q(m["language"])}   # auto = the language the meeting was held in
style    = {q(m["style"])}   # standard | brief | verbatim

[calendar]
enabled   = {q(cfg["calendar"]["enabled"])}
source    = {q(cfg["calendar"].get("source", "auto"))}   # auto | eventkit | outlook | none
calendars = {q(cfg["calendar"]["calendars"])}

[privacy]
# Both are destroyed as soon as the minutes exist. Turn one on if you need to be
# able to go back to what was actually said.
keep_audio      = {q(p["keep_audio"])}
keep_transcript = {q(p["keep_transcript"])}

[output]
root         = {q(tilde(o["root"]))}
formats      = {q(formats(cfg))}   # md, docx, pdf
organisation = {q(o["organisation"])}
""", encoding="utf-8")
    return CONFIG_PATH
