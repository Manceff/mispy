"""Configuration: defaults, overridable from ~/.config/mispy/config.toml."""
from __future__ import annotations
import os, pathlib, tomllib

HOME = pathlib.Path.home()
CONFIG_DIR = HOME / ".config/mispy"
CONFIG_PATH = CONFIG_DIR / "config.toml"
LIB = pathlib.Path(__file__).resolve().parent.parent
SHARE = HOME / ".local/share/mispy"
MODELS = SHARE / "models"
BIN = HOME / ".local/bin"

DEFAULTS: dict = {
    "audio": {
        # mic = the room. system = what your Mac is playing, i.e. the other people
        # in a video call. both = mixed into one track, which is what you want on a
        # call you are also speaking in.
        "capture": "mic",          # mic | system | both
        "device": "",              # avfoundation input NAME; empty = ask at setup
        "sample_rate": 16000,
    },
    "transcribe": {
        "model": str(MODELS / "ggml-large-v3.bin"),
        "vad_model": str(MODELS / "ggml-silero-v6.2.0.bin"),
        "language": "fr",          # the language people speak in your meetings
        "threads": 0,              # 0 = one per performance core
        "timestamps": True,        # minutes are far more useful with them
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
        "calendars": [],           # which calendars to look at; [] = all of them
        "lookaround_minutes": 30,
    },
    "privacy": {
        "keep_audio": False,       # destroyed once the minutes exist
        "keep_transcript": False,  # same
    },
    "output": {
        "root": str(HOME / "Documents/Meetings"),
        "pdf": True,               # also typeset a PDF, if tectonic is installed
        "organisation": "",        # printed on the PDF, optional
    },
    "runtime": {
        "state_dir": str(HOME / ".local/state/mispy"),
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
    return cfg


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
    os.chmod(d, 0o700)
    return d


def write(cfg: dict) -> pathlib.Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    a, t, m, o, p = (cfg["audio"], cfg["transcribe"], cfg["minutes"],
                     cfg["output"], cfg["privacy"])

    def tilde(v: str) -> str:
        return str(v).replace(str(HOME), "~", 1) if str(v).startswith(str(HOME)) else str(v)

    def q(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return "[" + ", ".join(f'"{x}"' for x in v) + "]"
        if isinstance(v, (int, float)):
            return str(v)
        return '"' + str(v).replace('"', '\\"') + '"'

    CONFIG_PATH.write_text(f"""# Mispy — written by `mispy setup`, yours to edit.

[audio]
# mic    = the room, through your microphone
# system = what your Mac is playing: the other people on a call
# both   = the two mixed into one track
capture = {q(a["capture"])}
# By NAME, never by index: indices shift as soon as an iPhone or a pair of
# AirPods shows up, and Mispy refuses to start rather than record the wrong one.
device  = {q(a["device"])}

[transcribe]
language = {q(t["language"])}   # what people speak in your meetings
model    = {q(tilde(t["model"]))}

[minutes]
backend  = {q(m["backend"])}   # claude_cli = your Claude subscription
language = {q(m["language"])}   # auto = the language the meeting was held in
style    = {q(m["style"])}   # standard | brief | verbatim

[calendar]
enabled   = {q(cfg["calendar"]["enabled"])}   # name the meeting from your diary
calendars = {q(cfg["calendar"]["calendars"])}

[privacy]
# Both are destroyed as soon as the minutes exist. Turn one on if you need to
# be able to go back to what was actually said.
keep_audio      = {q(p["keep_audio"])}
keep_transcript = {q(p["keep_transcript"])}

[output]
root         = {q(tilde(o["root"]))}
pdf          = {q(o["pdf"])}
organisation = {q(o["organisation"])}
""", encoding="utf-8")
    return CONFIG_PATH
