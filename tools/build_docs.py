#!/usr/bin/env python3
"""Build the project report, code guide, and presentation.

Usage
-----
    python tools/build_docs.py
    python tools/build_docs.py --pdf-only
    python tools/build_docs.py --slides-only

Outputs
-------
    docs/KV-Snapshot-Project-Report.pdf
    docs/KV-Snapshot-Code-Discussion-Guide.pdf
    docs/KV-Snapshot-Presentation.pptx

The PDF builder uses only the Python standard library plus a local Chrome or
Edge installation.  ``pdfminer.six`` is optional and only used to measure the
rendered pages so the table of contents can show page numbers.  The slide
builder additionally needs ``python-pptx`` (the only documentation dependency;
the kvstore itself has none).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
IMAGES = DOCS / "images"
TEAM_FILE = DOCS / "team.json"

REPORT_MD = DOCS / "REPORT.md"
GUIDE_MD = DOCS / "CODE_GUIDE.md"
REPORT_PDF = DOCS / "KV-Snapshot-Project-Report.pdf"
GUIDE_PDF = DOCS / "KV-Snapshot-Code-Discussion-Guide.pdf"
SLIDES_PPTX = DOCS / "KV-Snapshot-Presentation.pptx"

TEAM_PLACEHOLDER = "<!-- TEAM_TABLE -->"

PAPER = "#F3EEE4"
PAPER_LIGHT = "#FBF8F1"
INK = "#292720"
INK_SOFT = "#3D3A31"
MUTED = "#756F62"
MUTED_LIGHT = "#978F80"
LINE = "#D8CEBD"
LINE_STRONG = "#BFB3A0"
CLAY = "#AD6047"
CLAY_DEEP = "#914C37"
CLAY_LIGHT = "#F0DFD5"
SAGE = "#59624E"
SAGE_DEEP = "#4F5744"
SAGE_LIGHT = "#E4E7DA"


# ---------------------------------------------------------------------------
# Markdown -> HTML
# ---------------------------------------------------------------------------
class Slugger:
    def __init__(self) -> None:
        self.used: dict[str, int] = {}

    def slug(self, text: str) -> str:
        plain = re.sub(r"[`*_]", "", text)
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
        base = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "section"
        count = self.used.get(base, 0) + 1
        self.used[base] = count
        return base if count == 1 else f"{base}-{count}"


def inline(text: str) -> str:
    """Render inline Markdown (code, links, images, bold, italic)."""

    code_spans: list[str] = []

    def keep_code(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        return f"\x00{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = html.escape(text, quote=True)

    def image(match: re.Match[str]) -> str:
        alt, src = match.group(1), match.group(2)
        return f'<img src="{src}" alt="{alt}">'

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", image, text)

    def link(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        return f'<a href="{href}">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(
        r"&lt;(https?://[^\s]+?)&gt;",
        lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub("\x00(\\d+)\x00", lambda m: code_spans[int(m.group(1))], text)
    return text


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells
    )


def table_alignment(cell: str) -> str:
    cell = cell.strip()
    if cell.startswith(":") and cell.endswith(":"):
        return "center"
    if cell.endswith(":"):
        return "right"
    return "left"


def markdown_to_html(
    text: str,
    *,
    toc: list[tuple[int, str, str]],
    toc_offset: int = 0,
    break_h1: bool = False,
) -> str:
    lines = text.splitlines()
    blocks: list[str] = []
    slugger = Slugger()
    seen_h1 = False
    i = 0

    def is_block_start(line: str) -> bool:
        stripped = line.strip()
        return (
            not stripped
            or stripped.startswith(("#", "```", ">", "|", "![", "<!--"))
            or bool(re.match(r"^\s*[-*]\s+", line))
            or bool(re.match(r"^\s*\d+\.\s+", line))
            or stripped in {"---", "***", "___"}
        )

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # A literal team-table placeholder.
        if stripped == TEAM_PLACEHOLDER:
            blocks.append(TEAM_PLACEHOLDER)
            i += 1
            continue

        # Fenced code block.
        fence = re.match(r"^```([A-Za-z0-9_+.-]*)\s*$", stripped)
        if fence:
            language = fence.group(1)
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence
            code = html.escape("\n".join(code_lines), quote=False)
            tag = (
                f'<span class="code-tag">{html.escape(language)}</span>'
                if language
                else ""
            )
            blocks.append(f'<div class="code-wrap">{tag}<pre><code>{code}</code></pre></div>')
            continue

        # Heading.
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*$", stripped)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2)
            anchor = slugger.slug(title)
            plain_title = re.sub(r"[`*_]", "", title)
            plain_title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain_title)
            if level <= 2:
                toc.append((max(1, level - toc_offset), anchor, plain_title))
            classes = ["new-page"] if (break_h1 and level == 1 and seen_h1) else []
            if level == 1:
                seen_h1 = True
            class_attr = f' class="{" ".join(classes)}"' if classes else ""
            blocks.append(
                f'<h{level} id="{anchor}"{class_attr}>{inline(title)}</h{level}>'
            )
            i += 1
            continue

        # Horizontal rule.
        if stripped in {"---", "***", "___"}:
            blocks.append("<hr>")
            i += 1
            continue

        # Blockquote.
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                content = re.sub(r"^\s*>\s?", "", lines[i]).rstrip()
                quote_lines.append(content)
                i += 1
            quoted = " ".join(part for part in quote_lines if part)
            blocks.append(f"<blockquote><p>{inline(quoted)}</p></blockquote>")
            continue

        # Standalone image with caption.
        image_match = re.fullmatch(r"!\[([^\]]*)\]\(([^)\s]+)\)", stripped)
        if image_match:
            alt, src = image_match.group(1), image_match.group(2)
            blocks.append(
                '<figure>'
                f'<img src="{html.escape(src, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}">'
                f"<figcaption>{inline(alt)}</figcaption>"
                "</figure>"
            )
            i += 1
            continue

        # Table.
        if stripped.startswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            if len(table_lines) >= 2:
                header_cells = split_table_row(table_lines[0])
                separator = split_table_row(table_lines[1])
                if is_table_separator(separator):
                    alignments = [
                        table_alignment(cell)
                        for cell in separator[: len(header_cells)]
                    ]
                    alignments += ["left"] * (len(header_cells) - len(alignments))
                    header_html = "".join(
                        f'<th style="text-align:{align}">{inline(cell)}</th>'
                        for cell, align in zip(header_cells, alignments)
                    )
                    rows_html: list[str] = []
                    for table_line in table_lines[2:]:
                        cells = split_table_row(table_line)
                        cells = (cells + [""] * len(header_cells))[: len(header_cells)]
                        row_html = "".join(
                            f'<td style="text-align:{align}">{inline(cell)}</td>'
                            for cell, align in zip(cells, alignments)
                        )
                        rows_html.append(f"<tr>{row_html}</tr>")
                    blocks.append(
                        "<table><thead><tr>"
                        f"{header_html}"
                        "</tr></thead><tbody>"
                        f"{''.join(rows_html)}"
                        "</tbody></table>"
                    )
                    continue
            # Not actually a table: render as a paragraph.
            blocks.append(f"<p>{inline(' '.join(part.strip() for part in table_lines))}</p>")
            continue

        # Unordered list.
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            items: list[str] = []
            current = bullet.group(1)
            i += 1
            while i < len(lines):
                candidate = lines[i]
                candidate_stripped = candidate.strip()
                if not candidate_stripped:
                    break
                next_bullet = re.match(r"^\s*[-*]\s+(.*)$", candidate)
                if next_bullet:
                    items.append(current)
                    current = next_bullet.group(1)
                    i += 1
                    continue
                if candidate.startswith("  ") and not is_block_start(candidate):
                    current += " " + candidate_stripped
                    i += 1
                    continue
                break
            items.append(current)
            rendered = "".join(f"<li>{inline(item)}</li>" for item in items)
            blocks.append(f"<ul>{rendered}</ul>")
            continue

        # Ordered list.
        numbered = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if numbered:
            start = int(numbered.group(1))
            items = [numbered.group(2)]
            i += 1
            while i < len(lines):
                candidate = lines[i]
                candidate_stripped = candidate.strip()
                if not candidate_stripped:
                    break
                next_number = re.match(r"^\s*\d+\.\s+(.*)$", candidate)
                if next_number:
                    items.append(next_number.group(1))
                    i += 1
                    continue
                if candidate.startswith("  ") and not is_block_start(candidate):
                    items[-1] += " " + candidate_stripped
                    i += 1
                    continue
                break
            start_attr = f' start="{start}"' if start != 1 else ""
            rendered = "".join(f"<li>{inline(item)}</li>" for item in items)
            blocks.append(f"<ol{start_attr}>{rendered}</ol>")
            continue

        # Paragraph, with two-space hard breaks.
        parts: list[str] = []
        while i < len(lines):
            candidate = lines[i]
            if not candidate.strip():
                break
            if parts and is_block_start(candidate):
                break
            hard_break = candidate.endswith("  ")
            parts.append(candidate.strip())
            if hard_break:
                parts[-1] += "\x01"
            i += 1
        paragraph = " ".join(parts).replace("\x01 ", "<br>")
        blocks.append(f"<p>{inline(paragraph)}</p>")

    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Team data and cover
# ---------------------------------------------------------------------------
def load_team() -> dict:
    with TEAM_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def team_table(team: dict) -> str:
    rows: list[str] = []
    for member in team.get("members", []):
        name = str(member.get("name", ""))
        role = str(member.get("role", ""))
        contribution = str(member.get("contributions", ""))
        is_pending = name.strip().startswith("[")
        name_class = ' class="pending"' if is_pending else ""
        rows.append(
            "<tr>"
            f"<td{name_class}>{html.escape(name)}</td>"
            f"<td>{html.escape(role)}</td>"
            f"<td>{html.escape(contribution)}</td>"
            "</tr>"
        )
    return (
        '<table class="team-table"><thead><tr>'
        "<th>Member</th><th>Role</th><th>Contribution</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def meta_value(value: str | None, fallback: str = "") -> str:
    value = (value or fallback).strip()
    return "" if value.startswith("[") else value


def cover_html(kind: str, team: dict) -> str:
    leader = html.escape(str(team.get("leader", "Seif Eldin Mohamed")))
    semester = html.escape(str(team.get("semester", "Fall 2026")))
    submission = html.escape(str(team.get("submission_date", "")))
    course = html.escape(str(team.get("course", "Systems Programming")))
    institution = html.escape(meta_value(team.get("institution")))

    if kind == "report":
        title = 'KV <em>Snapshot</em>'
        kicker = "A crash-safe key–value store"
        subtitle = (
            "Atomic, checksummed snapshots for an in-memory store, with a REST "
            "API, a browser dashboard, and 72 tests."
        )
        foot = "Python 3 standard library · 72 automated tests · Windows & Linux"
        meta_items = [
            ("Team lead", leader),
            ("Course", course),
            ("Semester", semester),
            ("Date", submission),
        ]
    else:
        title = "Code Discussion<br><em>&amp; Viva Guide</em>"
        kicker = "Prepared for the doctor’s code discussion"
        subtitle = (
            "A file-by-file walkthrough, 30 likely questions with short answers, "
            "and small live-modification exercises."
        )
        foot = "KV Snapshot · Python 3 · 30 questions · live modifications"
        meta_items = [
            ("Project", "KV Snapshot"),
            ("Team lead", leader),
            ("Semester", semester),
            ("Date", submission),
        ]
    if institution:
        meta_items.append(("Institution", institution))

    meta_html = "".join(
        f'<div><span>{html.escape(label)}</span><strong>{value}</strong></div>'
        for label, value in meta_items
    )

    return f"""
<div class="cover">
  <div class="cover-top">
    <span class="cover-mark">kv</span>
    <span class="cover-course">{course} · Team Project</span>
  </div>
  <div class="cover-main">
    <p class="cover-kicker">{kicker}</p>
    <h1 class="cover-title">{title}</h1>
    <p class="cover-sub">{subtitle}</p>
    <div class="cover-rule"></div>
    <p class="cover-abstract">Durability is an ordering problem, not just a file-writing
    problem. This project makes the order visible: copy consistently, write to a
    temporary file, flush, <code>fsync</code>, rename atomically, and verify
    everything before restoring.</p>
  </div>
  <div class="cover-meta">{meta_html}</div>
  <div class="cover-foot">{foot}</div>
  <svg class="cover-sprig" viewBox="0 0 160 300" fill="none" aria-hidden="true">
    <path d="M88 292C82 238 80 184 92 132C100 98 112 66 132 34" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>
    <path d="M90 250C69 244 50 229 42 205C67 207 84 222 90 250Z" stroke="currentColor" stroke-width="1" fill="currentColor" fill-opacity=".06"/>
    <path d="M89 215C109 208 127 193 135 170C112 172 96 187 89 215Z" stroke="currentColor" stroke-width="1" fill="currentColor" fill-opacity=".06"/>
    <path d="M92 178C72 172 55 158 48 136C71 138 86 152 92 178Z" stroke="currentColor" stroke-width="1" fill="currentColor" fill-opacity=".06"/>
    <path d="M96 140C115 133 131 119 139 98C118 100 102 114 96 140Z" stroke="currentColor" stroke-width="1" fill="currentColor" fill-opacity=".06"/>
    <path d="M101 101C84 96 69 84 62 65C82 67 95 79 101 101Z" stroke="currentColor" stroke-width="1" fill="currentColor" fill-opacity=".06"/>
    <path d="M110 67C124 61 136 49 141 32C124 34 112 46 110 67Z" stroke="currentColor" stroke-width="1" fill="currentColor" fill-opacity=".06"/>
  </svg>
</div>
"""


# ---------------------------------------------------------------------------
# CSS and page shell
# ---------------------------------------------------------------------------
CSS = f"""
@page {{
  size: A4;
  margin: 20mm 18mm 22mm 18mm;
  @bottom-left {{
    content: "__FOOTER__";
    color: {MUTED_LIGHT};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 7.2pt;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }}
  @bottom-right {{
    content: counter(page) " / " counter(pages);
    color: {MUTED};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 8pt;
    font-variant-numeric: tabular-nums;
  }}
}}
@page :first {{
  margin: 0;
  @bottom-left {{ content: none; }}
  @bottom-right {{ content: none; }}
}}
* {{ box-sizing: border-box; }}
html {{
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}}
body {{
  margin: 0;
  background: #FFFDF9;
  color: {INK};
  font-family: Georgia, "Times New Roman", serif;
  font-size: 10.5pt;
  line-height: 1.62;
}}
p {{
  margin: 0 0 8pt;
  text-align: justify;
  hyphens: auto;
}}
h1, h2, h3, h4 {{
  color: {INK};
  font-family: Georgia, "Times New Roman", serif;
  font-weight: 400;
  page-break-after: avoid;
  break-after: avoid;
}}
h1 {{
  margin: 0 0 12pt;
  font-size: 24pt;
  letter-spacing: -0.03em;
  line-height: 1.08;
}}
h1.new-page {{ page-break-before: always; break-before: page; }}
h2 {{
  margin: 18pt 0 6pt;
  font-size: 15pt;
  letter-spacing: -0.02em;
  line-height: 1.18;
}}
h3 {{
  margin: 14pt 0 5pt;
  font-size: 11.8pt;
  font-weight: 700;
  line-height: 1.3;
}}
strong {{ font-weight: 700; }}
em {{ font-style: italic; }}
a {{
  color: {CLAY_DEEP};
  text-decoration: none;
  border-bottom: 0.5pt dotted {CLAY};
  overflow-wrap: anywhere;
}}
code {{
  padding: 0 2.5pt;
  background: #EFE8DA;
  border-radius: 2px;
  color: {INK_SOFT};
  font-family: Consolas, "Cascadia Code", monospace;
  font-size: 0.86em;
}}
pre {{
  margin: 0;
  padding: 9pt 11pt;
  background: #EFE8DA;
  border-left: 2.5pt solid {CLAY};
  color: {INK_SOFT};
  font-family: Consolas, "Cascadia Code", monospace;
  font-size: 8.2pt;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}}
.code-wrap {{
  position: relative;
  margin: 10pt 0 12pt;
  page-break-inside: avoid;
  break-inside: avoid;
}}
.code-tag {{
  position: absolute;
  top: 4pt;
  right: 8pt;
  color: {MUTED_LIGHT};
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 7pt;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
ul, ol {{
  margin: 0 0 9pt 16pt;
  padding: 0;
}}
li {{
  margin: 0 0 3pt;
  padding-left: 2pt;
}}
blockquote {{
  margin: 12pt 0;
  padding: 8pt 12pt;
  background: {PAPER_LIGHT};
  border-left: 2.5pt solid {CLAY};
  color: {INK_SOFT};
  font-style: italic;
}}
blockquote p {{ margin: 0; text-align: left; }}
hr {{
  margin: 14pt 0;
  border: 0;
  border-top: 0.6pt solid {LINE};
}}
table {{
  width: 100%;
  margin: 8pt 0 12pt;
  border-collapse: collapse;
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 8.6pt;
  line-height: 1.42;
}}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; break-inside: avoid; }}
th {{
  padding: 4.5pt 6pt;
  background: {SAGE_LIGHT};
  border: 0.5pt solid {LINE};
  color: {INK};
  font-weight: 600;
  text-align: left;
}}
td {{
  padding: 4.5pt 6pt;
  border: 0.5pt solid {LINE};
  vertical-align: top;
}}
tbody tr:nth-child(even) td {{ background: #FCF9F2; }}
figure {{
  margin: 12pt 0 14pt;
  page-break-inside: avoid;
  break-inside: avoid;
  text-align: center;
}}
figure img {{
  display: block;
  width: 100%;
  height: auto;
  border: 0.5pt solid {LINE};
  border-radius: 3px;
}}
figcaption {{
  margin-top: 4pt;
  color: {MUTED};
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 8.2pt;
  font-style: normal;
}}
.team-table td:first-child {{ white-space: nowrap; }}
.pending {{ color: {CLAY_DEEP}; }}
/* Cover --------------------------------------------------------------- */
.cover {{
  position: relative;
  z-index: 3;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  width: 210mm;
  height: 297mm;
  padding: 22mm 22mm 18mm;
  overflow: hidden;
  background:
    radial-gradient(circle at 86% 14%, rgba(173, 96, 71, 0.07), transparent 70mm),
    radial-gradient(circle at 12% 92%, rgba(89, 98, 78, 0.08), transparent 80mm),
    {PAPER};
  page-break-after: always;
  break-after: page;
}}
.cover-top {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: {MUTED};
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 8.5pt;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}}
.cover-mark {{
  display: grid;
  place-items: center;
  width: 18mm;
  height: 18mm;
  border: 1px solid {INK};
  border-radius: 50%;
  color: {INK};
  font-family: Georgia, "Times New Roman", serif;
  font-size: 20pt;
  font-style: italic;
  letter-spacing: -0.06em;
}}
.cover-course {{ text-align: right; }}
.cover-main {{ margin-top: 24mm; max-width: 132mm; }}
.cover-kicker {{
  margin: 0 0 14pt;
  color: {MUTED};
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 9pt;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}}
.cover-title {{
  margin: 0;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 58pt;
  font-weight: 400;
  letter-spacing: -0.055em;
  line-height: 0.96;
}}
.cover-title em {{ color: {CLAY}; font-style: italic; }}
.cover-sub {{
  max-width: 116mm;
  margin: 24pt 0 0;
  color: {INK_SOFT};
  font-family: Georgia, "Times New Roman", serif;
  font-size: 14pt;
  line-height: 1.5;
  text-align: left;
}}
.cover-rule {{
  width: 38mm;
  height: 1px;
  margin: 24pt 0 18pt;
  background: {CLAY};
}}
.cover-abstract {{
  max-width: 118mm;
  margin: 0;
  color: {MUTED};
  font-size: 9.6pt;
  line-height: 1.65;
  text-align: left;
}}
.cover-abstract code {{ background: rgba(255, 255, 255, 0.45); }}
.cover-meta {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7mm 12mm;
  width: 150mm;
  padding-top: 10mm;
  border-top: 1px solid {LINE_STRONG};
}}
.cover-meta div {{ display: flex; flex-direction: column; gap: 2pt; }}
.cover-meta span {{
  color: {MUTED_LIGHT};
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 7.4pt;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}}
.cover-meta strong {{
  color: {INK};
  font-family: Georgia, "Times New Roman", serif;
  font-size: 11pt;
  font-weight: 400;
}}
.cover-foot {{
  color: {MUTED};
  font-family: Georgia, "Times New Roman", serif;
  font-size: 9pt;
  font-style: italic;
}}
.cover-sprig {{
  position: absolute;
  right: -8mm;
  bottom: 28mm;
  width: 78mm;
  height: 146mm;
  color: {SAGE};
  opacity: 0.2;
}}
/* TOC ----------------------------------------------------------------- */
.toc {{ page-break-after: always; break-after: page; }}
.toc .eyebrow {{
  margin: 0 0 6pt;
  color: {MUTED_LIGHT};
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 8pt;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}}
.toc h1 {{
  margin: 0 0 8pt;
  font-size: 30pt;
}}
.toc-rule {{
  width: 32mm;
  height: 1px;
  margin: 0 0 16pt;
  background: {INK};
}}
.toc-list {{
  margin: 0;
  padding: 0;
  columns: 2;
  column-gap: 12mm;
  list-style: none;
}}
.toc-list li {{
  margin: 0 0 3.2pt;
  padding: 0;
  break-inside: avoid;
  line-height: 1.35;
  display: flex;
  align-items: baseline;
  gap: 4pt;
}}
.toc-list a {{ color: {INK}; border: 0; flex: 0 1 auto; }}
.toc-list .leader {{
  flex: 1 1 auto;
  min-width: 8pt;
  margin: 0 1pt 2.5pt;
  border-bottom: 0.5pt dotted #C9BFAD;
}}
.toc-list .page-no {{
  flex: 0 0 auto;
  color: {MUTED};
  font-family: "Segoe UI", Arial, sans-serif;
  font-size: 8.4pt;
  font-variant-numeric: tabular-nums;
}}
.toc-list .lvl-1 {{
  margin-top: 7pt;
  font-size: 10.5pt;
}}
.toc-list .lvl-1:first-child {{ margin-top: 0; }}
.toc-list .lvl-2 {{
  margin-left: 7mm;
  color: {MUTED};
  font-size: 9.2pt;
}}
.toc-list .lvl-2 a {{ color: {MUTED}; }}
@media screen {{
  body {{ background: #E8E2D6; }}
  .cover, .toc {{ box-shadow: 0 10px 40px rgba(41, 39, 32, 0.12); }}
}}
"""


def page_html(
    *,
    kind: str,
    title: str,
    team: dict,
    body: str,
    toc_entries: list[tuple[int, str, str]],
    running_left: str,
    toc_pages: dict[str, int] | None = None,
) -> str:
    footer_text = running_left.replace("\\", "\\\\").replace('"', '\\"')
    def toc_item(level: int, anchor: str, text: str) -> str:
        label = f'<a href="#{anchor}">{html.escape(text)}</a>'
        page = (toc_pages or {}).get(anchor)
        tail = (
            f'<span class="leader"></span><span class="page-no">{page}</span>'
            if page
            else ""
        )
        return f'<li class="lvl-{level}">{label}{tail}</li>'

    toc_html = "".join(
        toc_item(level, anchor, text) for level, anchor, text in toc_entries
    )
    toc_section = f"""
<section class="toc">
  <p class="eyebrow">{html.escape(running_left)}</p>
  <h1>Contents</h1>
  <div class="toc-rule"></div>
  <ul class="toc-list">{toc_html}</ul>
</section>
"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<base href="{DOCS.as_uri()}/">
<style>{CSS.replace("__FOOTER__", footer_text)}</style>
</head>
<body class="{kind}">
{cover_html(kind, team)}
{toc_section}
{body}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Chrome helpers
# ---------------------------------------------------------------------------
def find_chrome() -> str:
    candidates = [
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError(
        "Could not find Chrome or Edge. Set CHROME_PATH to the browser executable."
    )


def run_chrome(args: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess:
    command = [find_chrome(), "--headless=new", "--disable-gpu", *args]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Browser command failed:\n"
            + " ".join(command)
            + "\n"
            + (result.stderr or result.stdout)
        )
    return result


def html_to_pdf(html_text: str, output: Path, *, quiet: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix="kv-docs-") as temp:
        temp_path = Path(temp)
        html_path = temp_path / "document.html"
        html_path.write_text(html_text, encoding="utf-8")
        profile = temp_path / "chrome-profile"
        run_chrome(
            [
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=15000",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={output}",
                html_path.as_uri(),
            ]
        )
    if not output.is_file() or output.stat().st_size < 10_000:
        raise RuntimeError(f"The PDF was not written correctly: {output}")
    if not quiet:
        print(
            f"  wrote {output.relative_to(ROOT)} "
            f"({output.stat().st_size / 1024:.0f} KiB)"
        )


def normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def heading_pages(
    pdf_path: Path, toc_entries: list[tuple[int, str, str]]
) -> dict[str, int] | None:
    """Find the PDF page on which each heading first appears.

    ``pdfminer.six`` is a development-only dependency. When it is not
    installed the table of contents simply stays without page numbers.
    """

    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
    except ImportError:
        return None

    pages: list[str] = []
    for page_layout in extract_pages(str(pdf_path)):
        text = "".join(
            element.get_text()
            for element in page_layout
            if isinstance(element, LTTextContainer)
        )
        pages.append(normalise_text(text))

    # Skip the cover and any table-of-contents pages.  A contents page is
    # recognised by containing many of the heading strings at once.
    wanted = [normalise_text(title) for _, _, title in toc_entries]
    contents_pages = [
        index
        for index, text in enumerate(pages)
        if sum(1 for title in wanted if title and title in text) >= 8
    ]
    start = max(contents_pages) + 1 if contents_pages else 1

    found: dict[str, int] = {}
    for _, anchor, title in toc_entries:
        needle = normalise_text(title)
        if not needle:
            continue
        page_number = None
        for index in range(start, len(pages)):
            if needle in pages[index]:
                page_number = index + 1
                break
        if page_number is None:
            # A long heading may be split across lines in the PDF text.
            prefix = needle[:16]
            for index in range(start, len(pages)):
                if prefix in pages[index]:
                    page_number = index + 1
                    break
        if page_number is not None:
            found[anchor] = page_number
    return found


def svg_to_png(svg_path: Path, png_path: Path, width: int, height: int) -> None:
    with tempfile.TemporaryDirectory(prefix="kv-png-") as temp:
        profile = Path(temp) / "chrome-profile"
        run_chrome(
            [
                "--allow-file-access-from-files",
                "--hide-scrollbars",
                "--force-device-scale-factor=2",
                f"--window-size={width},{height}",
                "--virtual-time-budget=5000",
                f"--user-data-dir={profile}",
                f"--screenshot={png_path}",
                svg_path.as_uri(),
            ]
        )
    if not png_path.is_file():
        raise RuntimeError(f"Could not render {svg_path.name}")


# ---------------------------------------------------------------------------
# PDF build
# ---------------------------------------------------------------------------
def build_pdf(
    markdown_path: Path,
    output: Path,
    *,
    kind: str,
    title: str,
    running_left: str,
    team: dict,
    strip_front_matter: bool = False,
    toc_offset: int = 0,
) -> None:
    text = markdown_path.read_text(encoding="utf-8")
    if strip_front_matter:
        lines = text.splitlines()
        cleaned: list[str] = []
        index = 0
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index < len(lines) and lines[index].startswith("# "):
            index += 1
        while index < len(lines) and lines[index].strip() != "---":
            index += 1
        if index < len(lines):
            index += 1
        text = "\n".join(lines[index:])

    toc_entries: list[tuple[int, str, str]] = []
    body = markdown_to_html(
        text,
        toc=toc_entries,
        toc_offset=toc_offset,
        break_h1=(kind == "report"),
    )
    if kind == "report":
        body = body.replace(TEAM_PLACEHOLDER, team_table(team))

    print(f"Building {output.name}…")
    with tempfile.TemporaryDirectory(prefix="kv-toc-") as temp:
        draft = Path(temp) / "draft.pdf"
        draft_html = page_html(
            kind=kind,
            title=title,
            team=team,
            body=body,
            toc_entries=toc_entries,
            running_left=running_left,
        )
        html_to_pdf(draft_html, draft, quiet=True)
        pages = heading_pages(draft, toc_entries)
        if not pages:
            html_to_pdf(draft_html, output)
            return

        # Adding page numbers can, in principle, make the contents longer.
        # Rebuild until the measured page numbers match the printed ones.
        html_text = draft_html
        for _ in range(3):
            html_text = page_html(
                kind=kind,
                title=title,
                team=team,
                body=body,
                toc_entries=toc_entries,
                running_left=running_left,
                toc_pages=pages,
            )
            html_to_pdf(html_text, draft, quiet=True)
            measured = heading_pages(draft, toc_entries)
            if measured == pages:
                break
            pages = measured or pages

    html_to_pdf(html_text, output)


# ---------------------------------------------------------------------------
# PowerPoint build
# ---------------------------------------------------------------------------
def build_slides(team: dict) -> None:
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Inches, Pt
        from PIL import Image
    except ImportError as exc:
        print(f"Skipping slides: {exc}. Install python-pptx and Pillow to build them.")
        return

    paper = RGBColor(0xF3, 0xEE, 0xE4)
    paper_light = RGBColor(0xFB, 0xF8, 0xF1)
    ink = RGBColor(0x29, 0x27, 0x20)
    ink_soft = RGBColor(0x3D, 0x3A, 0x31)
    muted = RGBColor(0x75, 0x6F, 0x62)
    muted_light = RGBColor(0x97, 0x8F, 0x80)
    line = RGBColor(0xD8, 0xCE, 0xBD)
    clay = RGBColor(0xAD, 0x60, 0x47)
    clay_light = RGBColor(0xF0, 0xDF, 0xD5)
    sage = RGBColor(0x59, 0x62, 0x4E)
    sage_light = RGBColor(0xE4, 0xE7, 0xDA)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    slide_number = 0

    def new_slide(*, chrome: bool = True):
        nonlocal slide_number
        slide_number += 1
        slide = prs.slides.add_slide(blank)
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = paper
        if chrome:
            rule = slide.shapes.add_connector(
                1, Inches(0.6), Inches(0.92), Inches(12.73), Inches(0.92)
            )
            rule.line.color.rgb = line
            rule.line.width = Pt(0.75)
            footer = slide.shapes.add_textbox(
                Inches(0.6), Inches(7.06), Inches(9), Inches(0.25)
            )
            footer.text_frame.margin_left = 0
            footer.text_frame.margin_right = 0
            footer.text_frame.margin_top = 0
            footer.text_frame.margin_bottom = 0
            paragraph = footer.text_frame.paragraphs[0]
            run = paragraph.add_run()
            run.text = "KV Snapshot · Systems Programming · 2026"
            run.font.name = "Segoe UI"
            run.font.size = Pt(9)
            run.font.color.rgb = muted_light
            number = slide.shapes.add_textbox(
                Inches(12.1), Inches(7.06), Inches(0.65), Inches(0.25)
            )
            number.text_frame.margin_left = 0
            number.text_frame.margin_right = 0
            number.text_frame.margin_top = 0
            number.text_frame.margin_bottom = 0
            number_paragraph = number.text_frame.paragraphs[0]
            number_paragraph.alignment = PP_ALIGN.RIGHT
            number_run = number_paragraph.add_run()
            number_run.text = f"{slide_number:02d}"
            number_run.font.name = "Segoe UI"
            number_run.font.size = Pt(9)
            number_run.font.color.rgb = muted_light
        return slide

    def text(
        slide,
        left: float,
        top: float,
        width: float,
        height: float,
        value: str,
        *,
        size: float = 18,
        font: str = "Segoe UI",
        color=ink,
        bold: bool = False,
        italic: bool = False,
        align=PP_ALIGN.LEFT,
        anchor=MSO_ANCHOR.TOP,
        line_spacing: float = 1.14,
        space_after: float = 0,
    ):
        box = slide.shapes.add_textbox(
            Inches(left), Inches(top), Inches(width), Inches(height)
        )
        frame = box.text_frame
        frame.word_wrap = True
        frame.margin_left = 0
        frame.margin_right = 0
        frame.margin_top = 0
        frame.margin_bottom = 0
        frame.vertical_anchor = anchor
        paragraph = frame.paragraphs[0]
        paragraph.alignment = align
        paragraph.line_spacing = line_spacing
        paragraph.space_after = Pt(space_after)
        run = paragraph.add_run()
        run.text = value
        run.font.name = font
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.bold = bold
        run.font.italic = italic
        return box

    def kicker(slide, value: str):
        text(
            slide,
            0.6,
            0.45,
            11,
            0.3,
            value,
            size=10,
            color=muted_light,
            bold=True,
            font="Segoe UI",
        )

    def title(slide, value: str, *, size: float = 32):
        kicker(slide, "KV SNAPSHOT · SYSTEMS PROGRAMMING")
        text(slide, 0.6, 0.7, 12.1, 0.75, value, size=size, font="Georgia", line_spacing=1.0)

    def bullets(
        slide,
        items: Iterable[str],
        left: float,
        top: float,
        width: float,
        height: float,
        *,
        size: float = 18,
    ):
        box = slide.shapes.add_textbox(
            Inches(left), Inches(top), Inches(width), Inches(height)
        )
        frame = box.text_frame
        frame.word_wrap = True
        frame.margin_left = 0
        frame.margin_right = 0
        frame.margin_top = 0
        frame.margin_bottom = 0
        first = True
        for item in items:
            paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
            first = False
            paragraph.space_after = Pt(13)
            paragraph.line_spacing = 1.12
            run = paragraph.add_run()
            run.text = "—  " + item
            run.font.name = "Segoe UI"
            run.font.size = Pt(size)
            run.font.color.rgb = ink_soft

    def card(slide, left, top, width, height, *, fill=paper_light, border=line):
        shape = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(left),
            Inches(top),
            Inches(width),
            Inches(height),
        )
        shape.adjustments[0] = 0.04
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
        shape.line.color.rgb = border
        shape.line.width = Pt(0.9)
        shape.shadow.inherit = False
        return shape

    def picture(slide, path: Path, left: float, top: float, width: float):
        with Image.open(path) as image:
            ratio = image.height / image.width
        pic = slide.shapes.add_picture(
            str(path), Inches(left), Inches(top), width=Inches(width)
        )
        pic.line.color.rgb = line
        pic.line.width = Pt(0.75)
        return pic, width * ratio

    def notes(slide, value: str):
        slide.notes_slide.notes_text_frame.text = value

    # 1. Cover -------------------------------------------------------------
    slide = new_slide(chrome=False)
    text(
        slide,
        0.7,
        0.62,
        6.4,
        0.35,
        "SYSTEMS PROGRAMMING · TEAM PROJECT",
        size=11,
        color=muted,
        bold=True,
    )
    marker = slide.shapes.add_shape(
        MSO_SHAPE.OVAL, Inches(11.68), Inches(0.6), Inches(0.95), Inches(0.95)
    )
    marker.fill.solid()
    marker.fill.fore_color.rgb = paper
    marker.line.color.rgb = ink
    marker.line.width = Pt(1)
    marker.text_frame.text = "kv"
    marker.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    marker.text_frame.paragraphs[0].runs[0].font.name = "Georgia"
    marker.text_frame.paragraphs[0].runs[0].font.size = Pt(22)
    marker.text_frame.paragraphs[0].runs[0].font.italic = True
    marker.text_frame.paragraphs[0].runs[0].font.color.rgb = ink
    text(
        slide,
        0.7,
        1.5,
        12,
        1.0,
        "A crash-safe key–value store",
        size=20,
        font="Georgia",
        italic=True,
        color=clay,
    )
    text(
        slide,
        0.66,
        2.15,
        12.1,
        1.7,
        "KV Snapshot",
        size=72,
        font="Georgia",
        line_spacing=0.95,
    )
    rule = slide.shapes.add_connector(
        1, Inches(0.72), Inches(4.12), Inches(2.2), Inches(4.12)
    )
    rule.line.color.rgb = clay
    rule.line.width = Pt(2)
    text(
        slide,
        0.7,
        4.42,
        8.4,
        1.0,
        "Atomic, checksummed snapshots for an in-memory store.",
        size=22,
        font="Georgia",
        color=ink_soft,
    )
    text(
        slide,
        0.7,
        6.1,
        7.2,
        0.8,
        f"Team lead  ·  {team.get('leader', 'Seif Eldin Mohamed')}\n"
        f"Systems Programming  ·  {team.get('semester', 'Fall 2026')}",
        size=12,
        color=muted,
        line_spacing=1.35,
    )
    text(
        slide,
        9.2,
        6.1,
        3.5,
        0.8,
        "Python standard library\n72 automated tests",
        size=11,
        color=muted,
        align=PP_ALIGN.RIGHT,
        line_spacing=1.35,
    )
    notes(
        slide,
        "Open with the one-sentence version: an in-memory key–value store whose "
        "durability comes from explicitly created, checksummed snapshots written "
        "with an atomic protocol.",
    )

    # 2. Problem -----------------------------------------------------------
    slide = new_slide()
    title(slide, "Memory is fast. A crash is unforgiving.")
    bullets(
        slide,
        [
            "The working set lives in a Python dictionary, so reads and writes are cheap.",
            "A dictionary disappears when the process exits.",
            "A naïve save writes over the only file: a crash halfway through leaves a truncated file and destroys the old copy.",
            "The hard problem is not writing bytes; it is writing them safely.",
        ],
        0.65,
        1.55,
        6.7,
        4.8,
        size=18,
    )
    card(slide, 7.7, 1.65, 4.9, 3.2, fill=clay_light, border=clay)
    text(
        slide,
        7.98,
        1.95,
        4.35,
        0.4,
        "THE NAÏVE SAVE",
        size=10,
        color=clay,
        bold=True,
    )
    text(
        slide,
        7.98,
        2.45,
        4.35,
        1.7,
        "write(file)\n  ↓ crash\nfile is half old, half new —\nand neither is usable.",
        size=18,
        font="Consolas",
        color=clay,
        line_spacing=1.45,
    )
    text(
        slide,
        7.98,
        4.25,
        4.35,
        0.8,
        "The old snapshot must survive a failed new one.",
        size=13,
        font="Georgia",
        italic=True,
        color=ink_soft,
    )
    notes(
        slide,
        "Emphasise that this is the exact failure the project is designed around. "
        "The fix is not a bigger write buffer; it is an ordering protocol.",
    )

    # 3. The idea ----------------------------------------------------------
    slide = new_slide()
    title(slide, "One core, two explicit operations.")
    cards = [
        (
            0.65,
            "In memory",
            "A thread-safe dict holds the live state. Reads and writes touch only memory.",
        ),
        (
            4.5,
            "Snapshot",
            "Copy the dict under the lock, encode it, write a temporary file, fsync, rename.",
        ),
        (
            8.35,
            "Restore",
            "Read the whole file, verify every byte, build a new dict, then swap it in.",
        ),
    ]
    for left, heading, body in cards:
        card(slide, left, 1.75, 3.55, 3.35)
        text(slide, left + 0.28, 2.05, 3.0, 0.4, heading.upper(), size=10, color=clay, bold=True)
        text(slide, left + 0.28, 2.55, 2.98, 0.55, heading, size=24, font="Georgia")
        text(slide, left + 0.28, 3.2, 2.98, 1.6, body, size=15, color=muted, line_spacing=1.25)
    text(
        slide,
        0.65,
        5.55,
        12.0,
        0.9,
        "The GUI, API, and CLI are clients of the same core. Validation is never duplicated in JavaScript.",
        size=16,
        font="Georgia",
        italic=True,
        color=muted,
    )
    notes(
        slide,
        "The three boxes are the whole mental model. Everything else is a client "
        "or a test around them.",
    )

    # 4. Architecture ------------------------------------------------------
    slide = new_slide()
    title(slide, "Five layers, one implementation of each rule.")
    with tempfile.TemporaryDirectory(prefix="kv-slides-") as temp:
        temp_path = Path(temp)
        arch_png = temp_path / "architecture.png"
        svg_to_png(IMAGES / "architecture.svg", arch_png, 1120, 640)
        picture(slide, arch_png, 1.95, 1.5, 9.45)
        notes(
            slide,
            "Walk left to right: clients, HTTP server, store, snapshot manager, "
            "disk. The CLI bypasses HTTP but still uses the same store and manager.",
        )

        # 5. Concurrency ----------------------------------------------------
        slide = new_slide()
        title(slide, "A snapshot is a point in time, not a live view.")
        bullets(
            slide,
            [
                "The store lock protects the dictionary for single operations.",
                "A snapshot copies the dictionary under the lock and releases it immediately.",
                "Encoding and disk I/O happen outside the store lock, so readers and writers continue.",
                "The snapshot-manager lock serializes create and restore.",
                "Restore validates into a new dictionary, then swaps one reference under the lock.",
            ],
            0.65,
            1.55,
            7.1,
            4.9,
            size=17,
        )
        card(slide, 8.0, 1.7, 4.6, 4.25, fill=sage, border=sage)
        text(slide, 8.3, 2.0, 4.0, 0.3, "TWO LOCKS", size=10, color=sage_light, bold=True)
        text(
            slide,
            8.3,
            2.5,
            4.0,
            1.0,
            "store lock\n  dict work only",
            size=16,
            font="Consolas",
            color=paper_light,
            line_spacing=1.4,
        )
        text(
            slide,
            8.3,
            3.6,
            4.0,
            1.0,
            "manager lock\n  create + restore",
            size=16,
            font="Consolas",
            color=paper_light,
            line_spacing=1.4,
        )
        text(
            slide,
            8.3,
            4.85,
            4.0,
            0.8,
            "Disk I/O never holds the store lock.",
            size=13,
            font="Georgia",
            italic=True,
            color=sage_light,
        )
        notes(
            slide,
            "Two locks sound like more complexity, but their jobs are different: "
            "one protects data, the other serializes a whole file operation.",
        )

        # 6. File format ----------------------------------------------------
        slide = new_slide()
        title(slide, "A small, explicit binary format.")
        format_png = temp_path / "file-format.png"
        svg_to_png(IMAGES / "file-format.svg", format_png, 1120, 620)
        picture(slide, format_png, 1.95, 1.55, 9.45)
        notes(
            slide,
            "Point at the 12-byte header and the 32-byte trailer. The checksum is "
            "verified before any entry length is trusted.",
        )

        # 7. Atomic write ---------------------------------------------------
        slide = new_slide()
        title(slide, "Publish only a complete file.")
        atomic_png = temp_path / "atomic.png"
        svg_to_png(IMAGES / "atomic-write.svg", atomic_png, 1120, 620)
        picture(slide, atomic_png, 1.95, 1.55, 9.45)
        notes(
            slide,
            "The final name is never opened for writing. The temp file is written, "
            "flushed, fsynced, renamed, and the directory is fsynced on POSIX.",
        )

        # 8. Restore --------------------------------------------------------
        slide = new_slide()
        title(slide, "A failed restore changes nothing.")
        restore_png = temp_path / "restore.png"
        svg_to_png(IMAGES / "restore-flow.svg", restore_png, 1120, 620)
        picture(slide, restore_png, 1.95, 1.55, 9.45)
        notes(
            slide,
            "Every rejection happens before the swap. The live store stays exactly "
            "as it was; the user gets a 422 and can try another snapshot.",
        )

        # 9. Failure matrix -------------------------------------------------
        slide = new_slide()
        title(slide, "Failure is part of the design.")
        failure_cards = [
            (0.65, 1.7, "Disk full", "fsync fails → temp removed, old snapshots intact, HTTP 500."),
            (6.75, 1.7, "Crash mid-write", "A .tmp may remain; startup removes it, old snapshots remain valid."),
            (0.65, 4.1, "Bad checksum", "Restore stops before parsing; live data untouched, HTTP 422."),
            (6.75, 4.1, "Rename fails", "Temp cleaned up, no partial final name, old snapshots untouched."),
        ]
        for left, top, heading, body in failure_cards:
            card(slide, left, top, 5.85, 2.08)
            text(slide, left + 0.28, top + 0.24, 5.2, 0.4, heading, size=21, font="Georgia")
            text(slide, left + 0.28, top + 0.82, 5.15, 1.0, body, size=14, color=muted, line_spacing=1.25)
        notes(
            slide,
            "These are tested, not just claimed. The failure-mode tests inject "
            "OSError at the fsync and rename boundaries.",
        )

        # 10. Testing -------------------------------------------------------
        slide = new_slide()
        title(slide, "72 tests, including the unhappy paths.")
        text(slide, 0.65, 1.55, 3.2, 1.5, "72", size=88, font="Georgia", color=clay)
        text(slide, 0.7, 2.95, 3.3, 1.0, "automated tests\npassing", size=16, color=muted, line_spacing=1.3)
        table_rows = [
            ("Store", "18", "validation, CRUD, concurrency"),
            ("Snapshot", "28", "format, corruption, path safety"),
            ("Failure modes", "8", "disk full, rename failure"),
            ("API", "18", "HTTP, errors, restore"),
        ]
        top = 1.65
        for name, count, focus in table_rows:
            text(slide, 4.2, top, 1.9, 0.4, name, size=15, bold=True)
            text(slide, 6.15, top, 0.8, 0.4, count, size=15, color=clay)
            text(slide, 7.0, top, 5.5, 0.4, focus, size=14, color=muted)
            connector = slide.shapes.add_connector(
                1, Inches(4.2), Inches(top + 0.43), Inches(12.55), Inches(top + 0.43)
            )
            connector.line.color.rgb = line
            connector.line.width = Pt(0.6)
            top += 0.72
        text(
            slide,
            4.2,
            4.75,
            8.3,
            1.0,
            "Failure injection patches os.fsync and os.replace, flips a snapshot byte, "
            "truncates a file, and runs four snapshot threads at once.",
            size=14,
            color=ink_soft,
            line_spacing=1.3,
        )
        notes(
            slide,
            "The point is not the number. The point is that the safety claims have "
            "tests that would fail if the protocol regressed.",
        )

        # 11. Dashboard -----------------------------------------------------
        slide = new_slide()
        title(slide, "The dashboard makes durability visible.")
        bullets(
            slide,
            [
                "Plain HTML, CSS, and JavaScript: no framework.",
                "Every value is rendered with textContent, not innerHTML.",
                "Status, entries, and snapshots load in parallel.",
                "Restore and delete require confirmation.",
                "The activity panel records what happened in the session.",
            ],
            0.65,
            1.6,
            5.4,
            4.9,
            size=16,
        )
        dashboard = IMAGES / "dashboard.png"
        picture(slide, dashboard, 6.55, 1.55, 5.9)
        text(
            slide,
            6.55,
            6.55,
            5.9,
            0.35,
            "The KV Snapshot dashboard, in its warm editorial theme.",
            size=10,
            color=muted,
            italic=True,
        )
        notes(
            slide,
            "Mention the explicit subtitle: changes are in memory until a snapshot "
            "is saved. The UI does not pretend durability is automatic.",
        )

        # 12. Trade-offs + Python ------------------------------------------
        slide = new_slide()
        title(slide, "Deliberate limits, deliberate language.")
        card(slide, 0.65, 1.7, 5.9, 4.5, fill=sage_light, border=line)
        text(slide, 0.95, 1.98, 5.2, 0.4, "CHOSEN", size=10, color=sage, bold=True)
        chosen = [
            "One lock per concern; small critical sections.",
            "Full-file snapshots; simple, inspectable format.",
            "Explicit saves; no hidden background writes.",
            "Standard library only; nothing to install.",
        ]
        text(
            slide,
            0.95,
            2.5,
            5.25,
            3.3,
            "\n".join("—  " + item for item in chosen),
            size=15,
            color=ink_soft,
            line_spacing=1.45,
        )
        card(slide, 6.75, 1.7, 5.95, 4.5, fill=clay_light, border=line)
        text(slide, 7.05, 1.98, 5.2, 0.4, "NOT INCLUDED", size=10, color=clay, bold=True)
        omitted = [
            "Authentication, TLS, replication.",
            "Automatic snapshot policy or retention.",
            "Append-only log and compaction.",
            "A physical power-loss test rig.",
        ]
        text(
            slide,
            7.05,
            2.5,
            5.25,
            3.3,
            "\n".join("—  " + item for item in omitted),
            size=15,
            color=ink_soft,
            line_spacing=1.45,
        )
        text(
            slide,
            0.65,
            6.35,
            12.0,
            0.5,
            "Python exposes the same OS primitives as C or Go: open, fsync, descriptors, os.replace.",
            size=13,
            font="Georgia",
            italic=True,
            color=muted,
        )
        notes(
            slide,
            "Be honest about what is not here. The project is about the snapshot "
            "protocol, not about rebuilding a production database.",
        )

        # 13. Closing -------------------------------------------------------
        slide = new_slide()
        text(slide, 0.65, 1.25, 11.8, 0.4, "QUESTIONS", size=10, color=clay, bold=True)
        text(slide, 0.62, 1.75, 11.8, 1.2, "Thank you.", size=54, font="Georgia")
        text(
            slide,
            0.65,
            3.0,
            7.0,
            1.0,
            "The code is small enough to read in one sitting, and the failure paths are tested.",
            size=19,
            font="Georgia",
            italic=True,
            color=ink_soft,
        )
        card(slide, 0.65, 4.55, 6.6, 1.55, fill=sage, border=sage)
        text(
            slide,
            0.95,
            4.78,
            6.0,
            1.1,
            "python -m kvstore --demo --open\npython -m unittest discover -s tests -v",
            size=13,
            font="Consolas",
            color=paper_light,
            line_spacing=1.6,
        )
        text(
            slide,
            7.7,
            4.65,
            4.9,
            1.4,
            "github.com/seifmomo/kv-snapshot",
            size=14,
            color=clay,
            align=PP_ALIGN.RIGHT,
        )
        notes(
            slide,
            "Offer three things: a live demo, a code walkthrough, or the failure "
            "test that proves a corrupt restore leaves live data untouched.",
        )

    prs.save(SLIDES_PPTX)
    print(f"  wrote {SLIDES_PPTX.relative_to(ROOT)} ({SLIDES_PPTX.stat().st_size / 1024:.0f} KiB)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-only", action="store_true", help="build only the PDFs")
    parser.add_argument("--slides-only", action="store_true", help="build only the slides")
    args = parser.parse_args()

    team = load_team()
    if not args.slides_only:
        build_pdf(
            REPORT_MD,
            REPORT_PDF,
            kind="report",
            title="KV Snapshot — Project Report",
            running_left="KV Snapshot · Project Report",
            team=team,
        )
        build_pdf(
            GUIDE_MD,
            GUIDE_PDF,
            kind="guide",
            title="KV Snapshot — Code Discussion & Viva Guide",
            running_left="KV Snapshot · Code Discussion Guide",
            team=team,
            strip_front_matter=True,
            toc_offset=1,
        )
    if not args.pdf_only:
        build_slides(team)
    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
