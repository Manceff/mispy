"""Turning a transcript into minutes.

The job is not summarising. It is answering the three questions a person asks a
week later: what did we decide, who is doing what, and what is still open. The
prompt is built around those, and around one rule -- nothing that was not said.
"""
from __future__ import annotations
import os, re, subprocess, tempfile

SECTIONS = ("title", "summary", "decisions", "actions", "discussion", "questions", "followups")

STYLE_NOTE = {
    "standard": "Give the discussion section real substance: the arguments made, the "
                "numbers quoted, the objections raised and how they were answered.",
    "brief":    "Keep it short. The summary, the decisions and the actions are what "
                "matter; three or four lines of discussion per topic at most.",
    "verbatim": "Be exhaustive in the discussion section: follow the meeting in order "
                "and keep every point, every figure and every disagreement.",
}

RULES = """\
You are writing the minutes of a meeting from its transcript.

Return your answer using these markers, each on a line of its own, in this order.
Write nothing outside them -- no preamble, no code fence, no closing remark.

::title::
A short title for the meeting, six words at most. Omit this whole section if a
title was given to you below.

::summary::
Four to six sentences. What the meeting was about and where it landed. Someone who
reads only this should know whether they need to read the rest.

::decisions::
One bullet per decision actually taken, each ending with its timestamp in brackets.
A decision is something settled, not something discussed. If nothing was decided,
write exactly: none

::actions::
One line per action, in exactly this shape, and nothing else on the line:
- owner: <name or empty> | what: <the task> | when: <deadline or empty>
Only tasks someone actually committed to. Leave the owner empty rather than
guessing who it was. If there are none, write exactly: none

::discussion::
The substance, in Markdown, with `### ` subheadings per topic, in the order the
meeting took them. Put a timestamp in brackets where a point begins. %(style)s

::questions::
One bullet per question left open or point explicitly deferred. If there are none,
write exactly: none

::followups::
One bullet per thing that needs to happen before the next meeting but that nobody
owns yet, or that was mentioned as a next step. If there are none, write: none

Rules that matter more than the format:
  - Only what was said. Never add a decision, an action, a figure or a deadline
    because a meeting like this usually has one.
  - Attribute something to a person only when a name was spoken or the attendee
    list leaves no doubt. An unattributed action is correct; a wrongly attributed
    one is a problem at work.
  - The transcript comes from speech recognition and contains mistakes. Where a
    passage is garbled, use the surrounding discussion to recover the meaning; if
    it cannot be recovered, leave it out rather than guessing. Where something
    important is audible but unclear, say so in brackets: [unclear].
  - Keep names, products, figures and dates exactly as they were said.
  - Write plainly, in the third person, in the past tense. No corporate filler,
    no "the team aligned on synergies".
  - Write the whole thing in %(language)s.
"""


def language_of(cfg: dict, spoken: str) -> str:
    want = (cfg["minutes"].get("language") or "auto").strip().lower()
    names = {"fr": "French", "en": "English", "de": "German", "es": "Spanish",
             "it": "Italian", "nl": "Dutch", "pt": "Portuguese"}
    if want in ("auto", ""):
        return names.get(spoken, "the language the meeting was held in")
    return names.get(want, want)


def prompt_for(transcript: str, title: str, when, attendees: list[str],
               cfg: dict, documents: list[tuple[str, str]] | None = None) -> str:
    style = STYLE_NOTE.get(cfg["minutes"].get("style", "standard"), STYLE_NOTE["standard"])
    head = RULES % {"style": style,
                    "language": language_of(cfg, cfg["transcribe"]["language"])}
    parts = [head, "", f"Date: {when:%A %d %B %Y, %H:%M}"]
    if title:
        parts.append(f"Title, already decided (do not write a ::title:: section): {title}")
    if attendees:
        parts.append("Attendees, as listed in the diary: " + ", ".join(attendees))
        parts.append("Treat that list as who was in the room, not as who spoke.")
    for name, text in (documents or []):
        parts.append(f"\n=== Document brought to the meeting: {name} ===\n{text[:60000]}")
    parts.append("\n=== Transcript ===\n" + transcript)
    return "\n".join(parts)


def parse(reply: str) -> dict:
    """Split the reply on its markers. A missing section is an empty one; the
    document still renders, with a gap where the model said nothing."""
    out = {k: "" for k in SECTIONS}
    pattern = re.compile(r"^::(" + "|".join(SECTIONS) + r")::\s*$", re.M)
    marks = list(pattern.finditer(reply))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(reply)
        out[m.group(1)] = reply[m.end():end].strip()
    if not marks:                       # the model ignored the format: keep everything
        out["discussion"] = reply.strip()
    for k, v in out.items():
        if v.strip().lower() in ("none", "aucune", "aucun", "- none", "néant"):
            out[k] = ""
    return out


ACTION = re.compile(r"owner\s*:\s*(?P<owner>.*?)\s*\|\s*what\s*:\s*(?P<what>.*?)"
                    r"(?:\s*\|\s*when\s*:\s*(?P<when>.*?))?\s*$", re.I)


def actions(block: str) -> list[dict]:
    out = []
    for line in block.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        m = ACTION.search(line)
        if m:
            out.append({"owner": m.group("owner").strip(" -—"),
                        "what": m.group("what").strip(" -—"),
                        "when": (m.group("when") or "").strip(" -—")})
        else:                              # a plain bullet is still an action
            out.append({"owner": "", "what": line, "when": ""})
    return out


def via_cli(prompt: str, model: str, timeout: int) -> str:
    """The subscription path. Runs in an empty directory with every tool refused,
    so it can only produce text."""
    with tempfile.TemporaryDirectory() as sandbox:
        r = subprocess.run(
            ["claude", "-p", "--output-format", "text", "--model", model,
             "--permission-mode", "default", "--disallowedTools",
             "Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"],
            input=prompt, capture_output=True, text=True, cwd=sandbox, timeout=timeout)
    out = (r.stdout or "").strip()
    if "Please run /login" in out or "Not logged in" in out:
        raise RuntimeError("claude-cli-not-logged-in")
    if r.returncode != 0 or not out:
        raise RuntimeError(f"claude-cli-failed: {(r.stderr or out)[:400]}")
    return out


def via_api(prompt: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=model, max_tokens=32000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()
    if getattr(message, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined to produce these minutes")
    return "".join(b.text for b in message.content if b.type == "text").strip()


def generate(prompt: str, cfg: dict) -> dict:
    m = cfg["minutes"]
    if m["backend"] == "claude_cli":
        try:
            return parse(via_cli(prompt, m["model"], m["timeout_seconds"]))
        except RuntimeError:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise
    return parse(via_api(prompt, m["model"]))
