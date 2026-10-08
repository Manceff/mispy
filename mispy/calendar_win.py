"""Reading the diary on Windows, through Outlook itself.

Deliberately not the Graph API. Graph needs an application registered in Azure AD
and, in most companies, an administrator to consent to it — which is exactly the
door that is shut on a locked-down machine. Classic Outlook is already running,
already signed in, and exposes its calendar over COM to anything running as you.
Nothing to register, nothing to approve, no token to store.

Teams meetings need no special handling: a Teams meeting *is* an Outlook
appointment. Its audio is captured from the speakers like any other.
"""
from __future__ import annotations
import datetime, re

OL_FOLDER_CALENDAR = 9
MAX_ITEMS = 400

# The Jet filter Outlook uses is parsed with the machine's short date format, which
# is not knowable from here. Try the plausible ones and let the Python-side filter
# be the authority on what actually overlaps the window.
DATE_FORMATS = ("%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M",
                "%m/%d/%Y %I:%M %p")


def available() -> tuple[bool, str]:
    try:
        import win32com.client                                      # noqa: F401
    except Exception:
        return False, "pywin32 is not installed — re-run install.ps1"
    try:
        _namespace()
        return True, "classic Outlook"
    except Exception as exc:
        return False, _explain(exc)


def _explain(exc: Exception) -> str:
    text = str(exc)
    if "Invalid class string" in text or "-2147221005" in text:
        return ("Outlook is not reachable over COM. The new Outlook does not offer "
                "it — classic Outlook does, or use a published .ics instead.")
    return text[:160]


def _namespace():
    import win32com.client
    return win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")


def _as_datetime(value) -> datetime.datetime | None:
    try:
        d = datetime.datetime(value.year, value.month, value.day,
                              value.hour, value.minute, value.second)
        return d
    except Exception:
        try:
            return datetime.datetime.fromisoformat(str(value)[:19])
        except Exception:
            return None


def _people(item) -> list[str]:
    names: list[str] = []
    for attr in ("Organizer", "RequiredAttendees", "OptionalAttendees"):
        try:
            raw = getattr(item, attr, "") or ""
        except Exception:
            raw = ""
        for part in re.split(r"[;,]", str(raw)):
            name = part.strip()
            # Outlook hands back display names, but a non-resolved address slips
            # through as SMTP; keep the local part, it is still a person.
            if "@" in name and " " not in name:
                name = name.split("@", 1)[0].replace(".", " ").title()
            if name and name not in names:
                names.append(name)
    return names[:30]


def around(start: datetime.datetime, end: datetime.datetime) -> list[dict]:
    """Appointments overlapping [start, end]. Recurrences expanded."""
    ns = _namespace()
    folder = ns.GetDefaultFolder(OL_FOLDER_CALENDAR)
    pad_lo, pad_hi = start - datetime.timedelta(days=1), end + datetime.timedelta(days=1)

    for fmt in DATE_FORMATS:
        try:
            items = folder.Items
            items.IncludeRecurrences = True      # must come before Sort, then Restrict
            items.Sort("[Start]")
            restricted = items.Restrict(
                f"[Start] <= '{pad_hi.strftime(fmt)}' AND [End] >= '{pad_lo.strftime(fmt)}'")
            rows = _collect(restricted, start, end)
        except Exception:
            continue
        if rows:
            return rows
    return []


def _collect(items, start: datetime.datetime, end: datetime.datetime) -> list[dict]:
    out, seen = [], 0
    for item in items:
        seen += 1
        if seen > MAX_ITEMS:
            break                                 # a recurring series never ends
        try:
            if getattr(item, "AllDayEvent", False):
                continue
            s, e = _as_datetime(item.Start), _as_datetime(item.End)
            title = (getattr(item, "Subject", "") or "").strip()
        except Exception:
            continue
        if not s or not e or not title or e < start or s > end:
            continue
        out.append({
            "title": title,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "location": (getattr(item, "Location", "") or "").strip(),
            "calendar": "Outlook",
            "attendees": _people(item),
            "notes": "",
        })
    return out
