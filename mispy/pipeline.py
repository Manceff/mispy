"""What happens once the meeting is over. Every stage checks whether its own
output already exists, so a job interrupted by a closing lid resumes where it
stopped rather than starting over."""
from __future__ import annotations
import datetime, pathlib, subprocess, traceback
from . import attachments, capture, config, meeting, minutes, render, shred, state, transcribe


def notify(title: str, text: str, subtitle: str = "") -> None:
    sub = f' subtitle "{subtitle}"' if subtitle else ""
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "{title}"{sub}'], check=False)


def ask_details(folder: pathlib.Path, prefill: list[str], title: str) -> list[str]:
    """One dialog, at the end rather than the start: who was there. Prefilled from
    the diary when the diary knew."""
    default = ", ".join(prefill)
    script = (
        f'display dialog "Who was in {title[:40]}?\\n\\nNames, separated by commas. '
        f'Leave it empty if you would rather not." '
        f'default answer "{default}" buttons {{"Add files…", "OK"}} default button "OK" '
        f'with title "Mispy" giving up after 900')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    out = r.stdout or ""
    names = ""
    for part in out.split(", button returned:"):
        if part.startswith("text returned:"):
            names = part.split(":", 1)[1]
    if "text returned:" in out and not names:
        names = out.split("text returned:", 1)[1].split(", button returned:")[0]
    if "button returned:Add files" in out:
        subprocess.run(["open", str(folder)], check=False)
        subprocess.run(["osascript", "-e",
                        'display dialog "Drop your files in the folder, then continue." '
                        'buttons {"Continue"} default button "Continue" with title "Mispy" '
                        'giving up after 900'], capture_output=True)
    return [n.strip() for n in names.replace(";", ",").split(",") if n.strip()]


def run(job: dict) -> None:
    cfg = config.load()
    work = config.work_dir()
    folder = pathlib.Path(job["folder"])
    stem = f"{meeting.safe(job['title'])} — {job['date']}"
    wav = work / "meeting.wav"
    transcript_path = work / "transcript.txt"

    try:
        if not wav.exists():
            capture.mixdown(work, job.get("segments", []), wav,
                            cfg["audio"]["sample_rate"])

        if job["stage"] == state.DETAILS:
            names = ask_details(folder, job.get("attendees", []), job["title"])
            job = state.set_stage(state.TRANSCRIBING, attendees=names)

        if not transcript_path.exists():
            state.set_stage(state.TRANSCRIBING)
            transcript_path.write_text(transcribe.run(wav, cfg, work / "transcript"),
                                       encoding="utf-8")
        transcript = transcript_path.read_text(encoding="utf-8")
        if len(transcript.split()) < 40:
            raise RuntimeError("almost nothing was captured — check the input")

        state.set_stage(state.SYNTHESISING)
        docs, skipped = attachments.gather(folder)
        start = datetime.datetime.fromisoformat(job["started_at"])
        prompt = minutes.prompt_for(transcript, job.get("title_given") or "", start,
                                    job.get("attendees", []), cfg, docs)
        parts = minutes.generate(prompt, cfg)

        title = job.get("title_given") or (parts.get("title") or "").strip().strip('"#') \
            or job["title"]
        title = " ".join(title.split())[:80]
        # The end is the start plus what was actually captured, not the clock: the
        # minutes may well be written hours later, on a train.
        dur = float(job.get("minutes_captured") or 0)
        meta = {"title": title, "start": start,
                "end": start + datetime.timedelta(minutes=dur) if dur else None,
                "attendees": job.get("attendees", []), "duration_min": dur}

        # If we started from a provisional name, the meeting has now named itself.
        if not job.get("title_given") and meeting.safe(title) != meeting.safe(job["title"]):
            renamed = folder.parent / f"{job['date']} — {meeting.safe(title)}"
            if not renamed.exists():
                try:
                    folder.rename(renamed)
                    folder = renamed
                except OSError:
                    pass

        state.set_stage(state.BUILDING, title=title, folder=str(folder))
        stem = f"{meeting.safe(title)} — {job['date']}"
        md_path = folder / f"{stem}.md"
        md_path.write_text(render.markdown(parts, meta, cfg), encoding="utf-8")

        notes = [f"unreadable attachment, ignored: {n}" for n in skipped]
        pdf_path = None
        if cfg["output"].get("pdf", True):
            tex = folder / f"{stem}.tex"
            tex.write_text(render.latex(parts, meta, cfg), encoding="utf-8")
            pdf_path, err = render.compile_pdf(tex, folder)
            if pdf_path is None:
                notes.append("the PDF did not typeset — the Markdown is complete")
            else:
                tex.unlink(missing_ok=True)   # the folder keeps .md and .pdf

        if not cfg["privacy"].get("keep_audio", False):
            shred.wipe(wav)
            for i in job.get("segments", []):
                shred.wipe(capture.mic_path(work, i))
                shred.wipe(capture.sys_path(work, i))
        if not cfg["privacy"].get("keep_transcript", False):
            shred.wipe(transcript_path)
        else:
            (folder / f"{stem} — transcript.txt").write_text(transcript, encoding="utf-8")
            shred.wipe(transcript_path)
        shred.clean(work)

        if not minutes.actions(parts.get("actions", "")):
            notes.append("no action was recorded — check that is right")
        state.set_stage(state.DONE, markdown=str(md_path), error=None, trace=None,
                        pdf=str(pdf_path) if pdf_path else "", notes=notes,
                        finished_at=datetime.datetime.now().isoformat(timespec="seconds"))
        notify("Mispy", title[:60], "Minutes ready")

    except Exception as exc:                       # the audio is kept, always
        state.set_stage(state.FAILED, error=f"{exc}", trace=traceback.format_exc()[-2000:])
        notify("Mispy failed", str(exc)[:90].replace('"', "'"), "The audio is kept")
