"""Everything that differs between a Mac and a Windows PC, in one file.

The rest of Mispy is written as if the operating system had no opinions. Where it
does -- where files belong, how you kill a process, how you put a notification on
screen -- the answer lives here and nowhere else.
"""
from __future__ import annotations
import os, pathlib, subprocess, sys

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"
NAME = "Windows" if WINDOWS else ("macOS" if MACOS else sys.platform)

HOME = pathlib.Path.home()


def config_dir() -> pathlib.Path:
    """Settings you are meant to open and edit."""
    if WINDOWS:
        return pathlib.Path(os.environ.get("APPDATA", HOME / "AppData/Roaming")) / "Mispy"
    return HOME / ".config/mispy"


def data_dir() -> pathlib.Path:
    """Big things that were installed: models, engines."""
    if WINDOWS:
        return pathlib.Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local")) / "Mispy"
    return HOME / ".local/share/mispy"


def state_dir() -> pathlib.Path:
    """Transient: the job file and the audio being recorded right now."""
    if WINDOWS:
        return data_dir() / "state"
    return HOME / ".local/state/mispy"


def bin_dir() -> pathlib.Path:
    return (data_dir() / "bin") if WINDOWS else (HOME / ".local/bin")


def lib_dir() -> pathlib.Path:
    return (data_dir() / "lib") if WINDOWS else (HOME / ".local/lib/mispy")


def documents() -> pathlib.Path:
    d = HOME / "Documents"
    if WINDOWS and not d.exists():
        one = HOME / "OneDrive/Documents"          # the usual corporate arrangement
        if one.exists():
            return one
    return d


def exe(name: str) -> str:
    return f"{name}.exe" if WINDOWS else name


# --------------------------------------------------------------------- processes
def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.killpg(os.getpgid(pid), 0)
        return True
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def kill_tree(pid: int | None) -> None:
    """Last resort. The recorders are asked to stop politely first; this is for
    when they did not."""
    if not pid:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    import signal
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def detached(cmd: list[str], cwd: str | None = None, log=None) -> subprocess.Popen:
    """Start something that must outlive this terminal."""
    kwargs: dict = dict(stdout=(log or subprocess.DEVNULL), stderr=(log or subprocess.DEVNULL),
                        stdin=subprocess.DEVNULL, cwd=cwd)
    if WINDOWS:
        # DETACHED_PROCESS so closing the window does not take it down with it.
        kwargs["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


# ------------------------------------------------------------------------- shell
def open_path(path) -> None:
    """Show a file or a folder to the person."""
    try:
        if WINDOWS:
            os.startfile(str(path))                       # noqa: S606 - the point of it
        else:
            subprocess.run(["open", str(path)], check=False)
    except Exception:
        pass


def notify(title: str, text: str, subtitle: str = "") -> None:
    """A notification, best effort. Never raises, never blocks: a meeting is not
    going to fail because a toast did not appear."""
    try:
        if WINDOWS:
            body = f"{subtitle} — {text}" if subtitle else text
            script = (
                "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                f"$n.ShowBalloonTip(6000,'{_ps(title)}','{_ps(body)}',"
                "[System.Windows.Forms.ToolTipIcon]::Info);Start-Sleep -Seconds 6;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                              "-Command", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000)     # CREATE_NO_WINDOW
        else:
            sub = f' subtitle "{_osa(subtitle)}"' if subtitle else ""
            subprocess.run(["osascript", "-e",
                            f'display notification "{_osa(text)}" with title '
                            f'"{_osa(title)}"{sub}'], check=False)
    except Exception:
        pass


def _osa(s: str) -> str:
    return s.replace("\\", "").replace('"', "'")


def _ps(s: str) -> str:
    return s.replace("'", "''").replace("`", "")


def keep_awake(cmd: list[str]) -> list[str]:
    """Wrap a command so the machine will not idle to sleep under it.

    macOS has caffeinate, which dies with the child it wraps -- exactly what is
    wanted. Windows has no equivalent for a child process; the recorder asks for
    it through SetThreadExecutionState instead, from inside.
    """
    if MACOS:
        return ["/usr/bin/nohup", "/usr/bin/caffeinate", "-i"] + cmd
    return cmd


def prevent_sleep(on: bool = True) -> None:
    """Windows: ask the OS not to sleep while we are recording."""
    if not WINDOWS:
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            (ES_CONTINUOUS | ES_SYSTEM_REQUIRED) if on else ES_CONTINUOUS)
    except Exception:
        pass


def ask_text(title: str, prompt: str, default: str = "") -> str | None:
    """A one-field dialog, for when the terminal that started the job is gone.
    Returns None if the person cancelled or nothing could be shown."""
    try:
        if WINDOWS:
            script = ("[void][Reflection.Assembly]::LoadWithPartialName('Microsoft.VisualBasic');"
                      f"[Microsoft.VisualBasic.Interaction]::InputBox('{_ps(prompt)}',"
                      f"'{_ps(title)}','{_ps(default)}')")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                               capture_output=True, text=True, timeout=900,
                               creationflags=0x08000000)
            return (r.stdout or "").strip() or None
        script = (f'display dialog "{_osa(prompt)}" default answer "{_osa(default)}" '
                  f'buttons {{"Skip", "OK"}} default button "OK" with title "{_osa(title)}" '
                  f'giving up after 900')
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        out = r.stdout or ""
        if "button returned:Skip" in out or "gave up:true" in out:
            return None
        # osascript returns its record in its own order, e.g.
        #   button returned:OK, text returned:Viet, gave up:false
        # Splitting on one trailing field only left « Viet, gave up:false » as a
        # name, which is what landed in a set of minutes.
        import re
        m = re.search(r"text returned:(.*?)(?:,\s*(?:button returned|gave up)\s*:|$)",
                      out, re.S)
        return m.group(1).strip() if m else None
    except Exception:
        return None
