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


def local_naive(value) -> datetime.datetime | None:
    """Every datetime inside Mispy is naive local time: that is what the job file
    records and what the minutes print. The calendars are the one place offsets
    come in -- EventKit always, Outlook sometimes -- so they are flattened here,
    at the border, rather than being allowed to meet a naive `now` later on."""
    if isinstance(value, datetime.datetime):
        d = value
    else:
        try:
            d = datetime.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return d.astimezone().replace(tzinfo=None) if d.tzinfo is not None else d


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
        # The reader parses RFC 3339, which needs an offset and no fractional
        # seconds; a bare "2026-10-12T12:37:00.123456" was silently unparseable
        # and the window quietly fell back to a default hour either side.
        lo = (now - span).astimezone().isoformat(timespec="seconds")
        hi = (now + span).astimezone().isoformat(timespec="seconds")
        r = subprocess.run([str(HELPER), "--from", lo, "--to", hi],
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


def _starts_at(event: dict) -> datetime.datetime | None:
    return local_naive(event.get("start"))


def best(now: datetime.datetime, cfg: dict) -> dict | None:
    """The event you are most likely sitting in: the one whose start is nearest."""
    events = around(now, cfg)
    if not events:
        return None
    here = local_naive(now) or datetime.datetime.now()

    def distance(e):
        start = _starts_at(e)
        return 1e9 if start is None else abs((start - here).total_seconds())

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
