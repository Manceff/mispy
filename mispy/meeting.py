"""What the diary knows about the meeting you are in.

Optional, and never blocking: if there is no calendar, or no permission, or
nothing scheduled, you simply type a title instead. When there is an event, the
title and the attendee list arrive for free -- and an attendee list is what makes
the difference between "someone will send the deck" and "Alice will send the deck".
"""
from __future__ import annotations
import datetime, json, pathlib, subprocess
from . import config

HELPER = config.BIN / "mispy-cal"


def calendars() -> list[tuple[str, str]]:
    if not HELPER.exists():
        raise RuntimeError("mispy-cal is not installed")
    r = subprocess.run([str(HELPER), "--list"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "the macOS calendar is unreachable").strip())
    return [tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l]


def around(now: datetime.datetime, cfg: dict) -> list[dict]:
    """Events overlapping a window centred on now."""
    if not cfg["calendar"].get("enabled", True) or not HELPER.exists():
        return []
    span = datetime.timedelta(minutes=int(cfg["calendar"].get("lookaround_minutes", 30)))
    try:
        r = subprocess.run([str(HELPER), "--from", (now - span).isoformat(),
                            "--to", (now + span).isoformat()],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return []
        rows = json.loads(r.stdout or "[]")
    except Exception:
        return []
    wanted = {c.strip().lower() for c in cfg["calendar"].get("calendars", []) if c.strip()}
    out = []
    for e in rows:
        if wanted and e.get("calendar", "").strip().lower() not in wanted:
            continue
        if not (e.get("title") or "").strip():
            continue
        out.append(e)
    return out


def best(now: datetime.datetime, cfg: dict) -> dict | None:
    """The event you are most likely sitting in: the one whose start is nearest."""
    events = around(now, cfg)
    if not events:
        return None
    def distance(e):
        try:
            start = datetime.datetime.fromisoformat(e["start"])
        except Exception:
            return 1e9
        return abs((start - now).total_seconds())
    return min(events, key=distance)


def folder(title: str, when: datetime.datetime, root: pathlib.Path) -> pathlib.Path:
    """One folder per meeting, dated first so the Finder sorts by it."""
    name = f"{when:%Y-%m-%d} — {safe(title)}"
    d = root / name
    n = 2
    while d.exists() and any(d.iterdir()) and not (d / ".mispy").exists():
        d = root / f"{name} ({n})"              # two meetings of the same name in a day
        n += 1
    d.mkdir(parents=True, exist_ok=True)
    (d / ".mispy").write_text("", encoding="utf-8")
    return d


def safe(title: str) -> str:
    bad = '/\\:*?"<>|'
    out = "".join(" " if c in bad else c for c in (title or "Meeting")).strip()
    return (" ".join(out.split()) or "Meeting")[:70]
