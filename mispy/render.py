"""Two documents out of the same structure: Markdown to paste, PDF to keep.

Markdown is the deliverable -- it goes straight into Slack, an email or Notion.
The PDF is a bonus, and is treated as one: if it fails to typeset, the job still
succeeds and you still have the minutes.
"""
from __future__ import annotations
import datetime, pathlib, re, shutil, subprocess
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


# ------------------------------------------------------------------------ docx
INLINE = re.compile(r"(\*\*.+?\*\*|(?<!\*)\*(?!\s).+?(?<!\s)\*(?!\*)|`.+?`)")


def _runs(paragraph, text: str) -> None:
    """Markdown emphasis into Word runs. The model only ever uses these three."""
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            paragraph.add_run(piece[2:-2]).bold = True
        elif piece.startswith("`") and piece.endswith("`"):
            run = paragraph.add_run(piece[1:-1])
            run.font.name = "Consolas"
        elif piece.startswith("*") and piece.endswith("*"):
            paragraph.add_run(piece[1:-1]).italic = True
        else:
            paragraph.add_run(piece)


def _block(doc, block: str, heading_level: int = 3) -> None:
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^#{3,6}\s+(.*)$", line)
        if m:
            doc.add_heading(m.group(1).strip(), level=heading_level)
            continue
        m = re.match(r"^[-*\u2022]\s+(.*)$", line)
        if m:
            _runs(doc.add_paragraph(style="List Bullet"), m.group(1).strip())
            continue
        _runs(doc.add_paragraph(), line)


def docx(parts: dict, meta: dict, cfg: dict, out: pathlib.Path) -> pathlib.Path:
    """A Word file, because at work that is the document people actually open,
    edit and forward."""
    from docx import Document
    from docx.shared import Pt, RGBColor
    from . import minutes as M
    w = words(cfg)

    doc = Document()
    org = (cfg["output"].get("organisation") or "").strip()
    if org:
        p = doc.add_paragraph()
        run = p.add_run(org)
        run.font.size, run.font.color.rgb = Pt(9), RGBColor(0x5A, 0x66, 0x72)
    doc.add_heading(meta["title"], level=0)

    when = meta["start"]
    stamp = f"{when:%d/%m/%Y} · {when:%H:%M}"
    if meta.get("end"):
        stamp += f"–{meta['end']:%H:%M}"
    if meta.get("duration_min"):
        stamp += f" · {round(meta['duration_min'])} {w['minutes']}"
    sub = doc.add_paragraph()
    run = sub.add_run(stamp)
    run.font.size, run.font.color.rgb = Pt(9), RGBColor(0x5A, 0x66, 0x72)
    if meta.get("attendees"):
        p = doc.add_paragraph()
        p.add_run(f"{w['present']} : ").bold = True
        p.add_run(", ".join(meta["attendees"]))

    if parts.get("summary"):
        doc.add_heading(w["summary"], level=1)
        _block(doc, parts["summary"])
    if parts.get("decisions"):
        doc.add_heading(w["decisions"], level=1)
        _block(doc, parts["decisions"])

    rows = M.actions(parts.get("actions", ""))
    if rows:
        doc.add_heading(w["actions"], level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for cell, title in zip(table.rows[0].cells, (w["who"], w["what"], w["when"])):
            cell.paragraphs[0].add_run(title).bold = True
        for r in rows:
            cells = table.add_row().cells
            cells[0].paragraphs[0].add_run(r["owner"] or "—").bold = True
            _runs(cells[1].paragraphs[0], r["what"])
            cells[2].paragraphs[0].add_run(r["when"] or "—")
        doc.add_paragraph()

    for key, heading in (("discussion", w["discussion"]), ("questions", w["questions"]),
                         ("followups", w["followups"])):
        if parts.get(key):
            doc.add_heading(heading, level=1)
            _block(doc, parts[key])

    doc.save(str(out))
    return out


# ------------------------------------------------------------------------- pdf
def compile_pdf(tex: pathlib.Path, outdir: pathlib.Path) -> tuple[pathlib.Path | None, str]:
    try:
        r = subprocess.run([shutil.which("tectonic") or "tectonic", "-X", "compile", str(tex),
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
