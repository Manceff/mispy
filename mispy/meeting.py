"""What the diary knows about the meeting you are in.

Optional, and never blocking: if there is no calendar, or no permission, or
nothing scheduled, you simply type a title instead. When there is an event, the
title and the attendee list arrive for free -- and an attendee list is what makes
the difference between "someone will send the deck" and "Alice will send the deck".
"""
from __future__ import annotations
import datetime, json, pathlib, subprocess
from . import config, host

HELPER = config.BIN / host.exe("mispy-cal")


def source(cfg: dict) -> str:
    """Which diary to ask: the Calendar app, Outlook, or nobody."""
    if not cfg["calendar"].get("enabled", True):
        return "none"
    want = (cfg["calendar"].get("source") or "auto").lower()
    if want in ("eventkit", "outlook", "none"):
        return want
    return "outlook" if host.WINDOWS else "eventkit"


def available(cfg: dict) -> tuple[bool, str]:
    kind = source(cfg)
    if kind == "none":
        return False, "turned off"
    if kind == "outlook":
        from . import calendar_win
        return calendar_win.available()
    if not HELPER.exists():
        return False, "mispy-cal is not installed"
    try:
        calendars()
        return True, "Calendar app"
    except Exception as exc:
        return False, str(exc)[:120]


def calendars() -> list[tuple[str, str]]:
    """macOS only: Outlook is asked for its default calendar and that is that."""
    if not HELPER.exists():
        raise RuntimeError("mispy-cal is not installed")
    r = subprocess.run([str(HELPER), "--list"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "the macOS calendar is unreachable").strip())
    return [tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l]


def around(now: datetime.datetime, cfg: dict) -> list[dict]:
    """Events overlapping a window centred on now."""
    kind = source(cfg)
    if kind == "none":
        return []
    span = datetime.timedelta(minutes=int(cfg["calendar"].get("lookaround_minutes", 30)))
    if kind == "outlook":
        try:
            from . import calendar_win
            rows = calendar_win.around(now - span, now + span)
        except Exception:
            return []
        return [e for e in rows if (e.get("title") or "").strip()]

    if not HELPER.exists():
        return []
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
