"""Two documents out of the same structure: Markdown to paste, PDF to keep.

Markdown is the deliverable -- it goes straight into Slack, an email or Notion.
The PDF is a bonus, and is treated as one: if it fails to typeset, the job still
succeeds and you still have the minutes.
"""
from __future__ import annotations
import datetime, pathlib, re, subprocess
from . import config

STYLE = config.LIB / "style/minutes.tex"

HEADINGS = {
    "en": dict(summary="Summary", decisions="Decisions", actions="Actions",
               discussion="Discussion", questions="Open questions",
               followups="Before the next meeting", who="Owner", what="Action",
               when="Due", attendees="Attendees", present="Present", minutes="min"),
    "fr": dict(summary="Résumé", decisions="Décisions", actions="Actions",
               discussion="Discussion", questions="Questions ouvertes",
               followups="Avant la prochaine réunion", who="Qui", what="Quoi",
               when="Pour quand", attendees="Participants", present="Présents",
               minutes="min"),
    "es": dict(summary="Resumen", decisions="Decisiones", actions="Acciones",
               discussion="Discusión", questions="Preguntas abiertas",
               followups="Antes de la próxima reunión", who="Quién", what="Acción",
               when="Para cuándo", attendees="Asistentes", present="Presentes",
               minutes="min"),
    "de": dict(summary="Zusammenfassung", decisions="Entscheidungen", actions="Aufgaben",
               discussion="Diskussion", questions="Offene Fragen",
               followups="Vor dem nächsten Treffen", who="Wer", what="Aufgabe",
               when="Bis wann", attendees="Teilnehmer", present="Anwesend",
               minutes="Min."),
    "it": dict(summary="Sintesi", decisions="Decisioni", actions="Azioni",
               discussion="Discussione", questions="Questioni aperte",
               followups="Prima del prossimo incontro", who="Chi", what="Azione",
               when="Entro", attendees="Partecipanti", present="Presenti",
               minutes="min"),
}


def words(cfg: dict) -> dict:
    lang = (cfg["minutes"].get("language") or "auto").lower()
    if lang in ("auto", ""):
        lang = (cfg["transcribe"].get("language") or "en").lower()
    return HEADINGS.get(lang[:2], HEADINGS["en"])


# --------------------------------------------------------------------- markdown
def markdown(parts: dict, meta: dict, cfg: dict) -> str:
    w = words(cfg)
    from . import minutes as M
    out = [f"# {meta['title']}", ""]

    when = meta["start"]
    line = f"{when:%d/%m/%Y} · {when:%H:%M}"
    if meta.get("end"):
        line += f"–{meta['end']:%H:%M}"
    if meta.get("duration_min"):
        line += f" · {round(meta['duration_min'])} {w['minutes']}"
    out += [f"**{line}**", ""]
    if meta.get("attendees"):
        out += [f"**{w['present']} :** " + ", ".join(meta["attendees"]), ""]

    if parts.get("summary"):
        out += [f"## {w['summary']}", "", parts["summary"].strip(), ""]
    if parts.get("decisions"):
        out += [f"## {w['decisions']}", "", _bullets(parts["decisions"]), ""]

    rows = M.actions(parts.get("actions", ""))
    if rows:
        out += [f"## {w['actions']}", "",
                f"| {w['who']} | {w['what']} | {w['when']} |",
                "|---|---|---|"]
        for r in rows:
            out.append(f"| {r['owner'] or '—'} | {_cell(r['what'])} | {r['when'] or '—'} |")
        out.append("")
    if parts.get("discussion"):
        out += [f"## {w['discussion']}", "", parts["discussion"].strip(), ""]
    if parts.get("questions"):
        out += [f"## {w['questions']}", "", _bullets(parts["questions"]), ""]
    if parts.get("followups"):
        out += [f"## {w['followups']}", "", _bullets(parts["followups"]), ""]
    return "\n".join(out).rstrip() + "\n"


def _bullets(block: str) -> str:
    lines = []
    for l in block.splitlines():
        l = l.strip()
        if not l:
            continue
        lines.append(l if l.startswith(("-", "*", "#")) else f"- {l}")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


# ------------------------------------------------------------------------ latex
SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
           "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
           "^": r"\textasciicircum{}"}


def tex_escape(text: str) -> str:
    return "".join(SPECIAL.get(c, c) for c in text)


def inline(text: str) -> str:
    """Escape everything, then put back the little Markdown the model uses."""
    t = tex_escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\\emph{\1}", t)
    t = re.sub(r"`(.+?)`", r"\\texttt{\1}", t)
    for bad, good in GLYPHS.items():
        t = t.replace(bad, good)
    return t


# XeTeX prints what it is given, but these either have no glyph in the font or
# are the wrong shape in text: spell them the LaTeX way.
GLYPHS = {
    "—": "---", "–": "--", "…": r"\ldots{}",
    "’": "'", "‘": "`", "“": "``", "”": "''",
    "·": r"\textperiodcentered{}", "•": r"\textbullet{}",
    "€": r"\texteuro{}", "°": r"\textdegree{}", "±": r"$\pm$",
    "×": r"$\times$", "→": r"$\rightarrow$", "←": r"$\leftarrow$",
    "≤": r"$\leq$", "≥": r"$\geq$", "≈": r"$\approx$", "≠": r"$\neq$",
    "™": r"\texttrademark{}", "©": r"\textcopyright{}", "®": r"\textregistered{}",
    "\u00a0": "~", "\u202f": "~", "\u2009": "~",
}


def block_to_tex(block: str) -> str:
    """Markdown as our prompt produces it: ### headings, bullets, paragraphs."""
    out, bullets = [], []

    def flush():
        # extend, never `out +=`: an augmented assignment would make `out` local
        # to this closure and the first bullet list would blow up.
        if bullets:
            out.append("\\begin{itemize}")
            out.extend(f"  \\item {b}" for b in bullets)
            out.append("\\end{itemize}")
            bullets.clear()

    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            out.append("")
            continue
        m = re.match(r"^\s*#{3,6}\s+(.*)$", line)
        if m:
            flush()
            out.append(f"\\subsection*{{{inline(m.group(1))}}}")
            continue
        m = re.match(r"^\s*[-*•]\s+(.*)$", line)
        if m:
            bullets.append(inline(m.group(1)))
            continue
        flush()
        out.append(inline(line.strip()))
    flush()
    return "\n".join(out).strip()


def latex(parts: dict, meta: dict, cfg: dict) -> str:
    w = words(cfg)
    from . import minutes as M
    preamble = STYLE.read_text(encoding="utf-8")
    when = meta["start"]
    stamp = f"{when:%d/%m/%Y} · {when:%H:%M}"
    if meta.get("end"):
        stamp += f"–{meta['end']:%H:%M}"
    if meta.get("duration_min"):
        stamp += f" · {round(meta['duration_min'])} {w['minutes']}"

    body = [f"\\renewcommand{{\\mispyrunning}}{{{inline(meta['title'])} --- {when:%d/%m/%Y}}}",
            "\\begin{flushleft}"]
    org = (cfg["output"].get("organisation") or "").strip()
    if org:
        body.append(f"{{\\color{{grayish}}\\small {inline(org)}}}\\\\[6pt]")
    body += [f"{{\\color{{slate}}\\LARGE\\bfseries {inline(meta['title'])}}}\\\\[8pt]",
             f"{{\\color{{grayish}}\\small {inline(stamp)}}}\\\\[4pt]"]
    if meta.get("attendees"):
        body.append(f"{{\\color{{grayish}}\\small \\textbf{{{w['present']}}} : "
                    f"{inline(', '.join(meta['attendees']))}}}\\\\[4pt]")
    body += ["\\end{flushleft}", "\\vspace{2pt}{\\color{rule}\\hrule height 0.8pt}\\vspace{10pt}"]

    if parts.get("summary"):
        body += ["\\begin{summarybox}", block_to_tex(parts["summary"]), "\\end{summarybox}", ""]
    if parts.get("decisions"):
        body += [f"\\section*{{{w['decisions']}}}", block_to_tex(parts["decisions"]), ""]

    rows = M.actions(parts.get("actions", ""))
    if rows:
        body += [f"\\section*{{{w['actions']}}}",
                 "\\begin{tabularx}{\\linewidth}{@{}>{\\bfseries}p{3.1cm} X p{2.6cm}@{}}",
                 "\\toprule",
                 f"{w['who']} & {w['what']} & {w['when']} \\\\",
                 "\\midrule"]
        for r in rows:
            body.append(f"{inline(r['owner'] or '—')} & {inline(r['what'])} & "
                        f"{inline(r['when'] or '—')} \\\\")
        body += ["\\bottomrule", "\\end{tabularx}", ""]

    if parts.get("discussion"):
        body += [f"\\section*{{{w['discussion']}}}", block_to_tex(parts["discussion"]), ""]
    if parts.get("questions"):
        body += [f"\\section*{{{w['questions']}}}", block_to_tex(parts["questions"]), ""]
    if parts.get("followups"):
        body += [f"\\section*{{{w['followups']}}}", block_to_tex(parts["followups"]), ""]

    return preamble + "\n\\begin{document}\n\n" + "\n".join(body) + "\n\n\\end{document}\n"


def compile_pdf(tex: pathlib.Path, outdir: pathlib.Path) -> tuple[pathlib.Path | None, str]:
    try:
        r = subprocess.run(["tectonic", "-X", "compile", str(tex),
                            "--outdir", str(outdir), "--keep-logs"],
                           capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return None, "tectonic is not installed"
    except subprocess.TimeoutExpired:
        return None, "tectonic timed out"
    pdf = outdir / (tex.stem + ".pdf")
    (outdir / (tex.stem + ".log")).unlink(missing_ok=True)
    if r.returncode == 0 and pdf.exists():
        return pdf, ""
    errors = "\n".join(l for l in r.stderr.splitlines()
                       if re.search(r"error|undefined|missing|runaway", l, re.I))
    return None, (errors or r.stderr)[-1500:]
