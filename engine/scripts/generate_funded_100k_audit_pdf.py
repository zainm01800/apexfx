#!/usr/bin/env python3
"""Render the canonical Funded-100K audit Markdown as a polished PDF."""

from __future__ import annotations

import argparse
import hashlib
import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "engine/data_store/validation/report-source.md"
DEFAULT_OUTPUT = ROOT / "output/pdf/apex_quant_funded_100k_qualification_audit_2026-09-03.pdf"

NAVY = colors.HexColor("#102A43")
INK = colors.HexColor("#243B53")
MUTED = colors.HexColor("#627D98")
PALE = colors.HexColor("#F0F4F8")
LINE = colors.HexColor("#D9E2EC")
TEAL = colors.HexColor("#0B7189")
TEAL_PALE = colors.HexColor("#E6F6F8")
RED = colors.HexColor("#B42318")
RED_PALE = colors.HexColor("#FEECEB")
AMBER = colors.HexColor("#B54708")
WHITE = colors.white


def register_fonts() -> tuple[str, str, str]:
    families = [
        (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
        ),
        (
            "/System/Library/Fonts/Supplemental/Helvetica.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica Oblique.ttf",
        ),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ),
    ]
    for regular, bold, italic in families:
        if all(Path(path).exists() for path in (regular, bold, italic)):
            pdfmetrics.registerFont(TTFont("ApexSans", regular))
            pdfmetrics.registerFont(TTFont("ApexSans-Bold", bold))
            pdfmetrics.registerFont(TTFont("ApexSans-Italic", italic))
            pdfmetrics.registerFontFamily(
                "ApexSans",
                normal="ApexSans",
                bold="ApexSans-Bold",
                italic="ApexSans-Italic",
                boldItalic="ApexSans-Bold",
            )
            return "ApexSans", "ApexSans-Bold", "ApexSans-Italic"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


FONT, FONT_BOLD, FONT_ITALIC = register_fonts()


def ascii_punctuation(value: str) -> str:
    replacements = {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00d7": "x",
        "\u2265": ">=",
        "\u2264": "<=",
        "\u2248": "~",
        "\u00a0": " ",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


TOKEN = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*")


def inline_markup(value: str) -> str:
    value = ascii_punctuation(value.strip())
    chunks: list[str] = []
    cursor = 0
    for match in TOKEN.finditer(value):
        chunks.append(html.escape(value[cursor : match.start()]))
        if match.group(1) is not None:
            label = html.escape(match.group(1))
            url = html.escape(match.group(2), quote=True)
            chunks.append(f'<link href="{url}" color="#0B7189"><u>{label}</u></link>')
        elif match.group(3) is not None:
            chunks.append(
                '<font name="ApexSans" color="#334E68" backColor="#F0F4F8">'
                + html.escape(match.group(3))
                + "</font>"
            )
        else:
            chunks.append(f"<b>{html.escape(match.group(4))}</b>")
        cursor = match.end()
    chunks.append(html.escape(value[cursor:]))
    return "".join(chunks)


def make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=9.25,
            leading=13.6,
            textColor=INK,
            spaceAfter=7.5,
            splitLongWords=True,
        ),
        "small": ParagraphStyle(
            "Small",
            fontName=FONT,
            fontSize=7.5,
            leading=10.2,
            textColor=MUTED,
        ),
        "h2": ParagraphStyle(
            "H2",
            fontName=FONT_BOLD,
            fontSize=16,
            leading=19,
            textColor=NAVY,
            spaceBefore=7,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3",
            fontName=FONT_BOLD,
            fontSize=11.5,
            leading=14.5,
            textColor=TEAL,
            spaceBefore=7,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            fontName=FONT,
            fontSize=9.1,
            leading=13.2,
            textColor=INK,
            leftIndent=13,
            firstLineIndent=-8,
            bulletIndent=2,
            spaceAfter=4,
        ),
        "number": ParagraphStyle(
            "Number",
            fontName=FONT,
            fontSize=9.1,
            leading=13.2,
            textColor=INK,
            leftIndent=16,
            firstLineIndent=-12,
            spaceAfter=5,
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            fontName=FONT_BOLD,
            fontSize=7.2,
            leading=9,
            textColor=WHITE,
        ),
        "table_cell": ParagraphStyle(
            "TableCell",
            fontName=FONT,
            fontSize=7.15,
            leading=9.3,
            textColor=INK,
        ),
        "cover_kicker": ParagraphStyle(
            "CoverKicker",
            fontName=FONT_BOLD,
            fontSize=9,
            leading=11,
            textColor=TEAL,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            fontName=FONT_BOLD,
            fontSize=28,
            leading=33,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            fontName=FONT,
            fontSize=12,
            leading=17,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "decision": ParagraphStyle(
            "Decision",
            fontName=FONT_BOLD,
            fontSize=13,
            leading=17,
            textColor=RED,
            alignment=TA_CENTER,
        ),
        "metric_label": ParagraphStyle(
            "MetricLabel",
            fontName=FONT,
            fontSize=7.5,
            leading=9,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "metric_value": ParagraphStyle(
            "MetricValue",
            fontName=FONT_BOLD,
            fontSize=15,
            leading=18,
            textColor=NAVY,
            alignment=TA_CENTER,
        ),
    }


STYLES = make_styles()


class AuditDocTemplate(BaseDocTemplate):
    def __init__(self, output: Path, source_sha: str):
        super().__init__(
            str(output),
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=22 * mm,
            bottomMargin=20 * mm,
            title="Apex Quant Funded-100K Qualification Audit",
            author="Apex Quant Research",
            subject="Funded-account strategy qualification and safety audit",
        )
        self.source_sha = source_sha
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(
            [
                PageTemplate(id="audit", frames=[frame], onPage=self._draw_page),
            ]
        )

    def _draw_page(self, canvas, doc) -> None:
        width, height = A4
        canvas.saveState()
        if doc.page == 1:
            canvas.setFillColor(PALE)
            canvas.rect(0, 0, width, height, fill=1, stroke=0)
            canvas.setFillColor(TEAL)
            canvas.rect(0, height - 7 * mm, width, 7 * mm, fill=1, stroke=0)
            canvas.setStrokeColor(LINE)
            canvas.line(20 * mm, 18 * mm, width - 20 * mm, 18 * mm)
            canvas.setFillColor(MUTED)
            canvas.setFont(FONT, 7)
            canvas.drawString(20 * mm, 11 * mm, f"SOURCE SHA256 {self.source_sha[:16]}")
            canvas.drawRightString(width - 20 * mm, 11 * mm, "RESEARCH AUDIT - NOT INVESTMENT ADVICE")
        else:
            canvas.setStrokeColor(LINE)
            canvas.line(20 * mm, height - 14 * mm, width - 20 * mm, height - 14 * mm)
            canvas.setFillColor(TEAL)
            canvas.setFont(FONT_BOLD, 8)
            canvas.drawString(20 * mm, height - 10 * mm, "APEX QUANT")
            canvas.setFillColor(MUTED)
            canvas.setFont(FONT, 7.5)
            canvas.drawRightString(width - 20 * mm, height - 10 * mm, "FUNDED-100K QUALIFICATION AUDIT")
            canvas.setStrokeColor(LINE)
            canvas.line(20 * mm, 13 * mm, width - 20 * mm, 13 * mm)
            canvas.setFillColor(MUTED)
            canvas.setFont(FONT, 7)
            canvas.drawString(20 * mm, 8 * mm, "3 September 2026 | Validation-only research")
            canvas.drawRightString(width - 20 * mm, 8 * mm, f"PAGE {doc.page}")
        canvas.restoreState()


def cover_story() -> list:
    metric_data = [
        [
            Paragraph("<b>1.10%</b>", STYLES["metric_value"]),
            Paragraph("<b>0.485</b>", STYLES["metric_value"]),
            Paragraph("<b>3.18%</b>", STYLES["metric_value"]),
            Paragraph("<b>1.31%</b>", STYLES["metric_value"]),
        ],
        [
            Paragraph("Synthetic eval annualized", STYLES["metric_label"]),
            Paragraph("Synthetic eval Sharpe", STYLES["metric_label"]),
            Paragraph("Synthetic eval max DD", STYLES["metric_label"]),
            Paragraph("Raw-quote peak stop risk", STYLES["metric_label"]),
        ],
    ]
    metrics = Table(metric_data, colWidths=[41 * mm] * 4, rowHeights=[15 * mm, 10 * mm])
    metrics.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), WHITE),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    decision_box = Table(
        [[Paragraph("NO FUNDED STRATEGY - DO NOT DEPLOY", STYLES["decision"])]],
        colWidths=[166 * mm],
        rowHeights=[24 * mm],
    )
    decision_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), RED_PALE),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#F4B7B2")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    evidence_box = Table(
        [[Paragraph(
            "Evidence ceiling: retrospective daily-OHLC replay with unconverted mixed quote currencies. This is not a true blind test, account-currency result, executable venue replay, or guarantee of passing, retaining, or profiting from a funded account.",
            STYLES["small"],
        )]],
        colWidths=[154 * mm],
    )
    evidence_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), TEAL_PALE),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#9BD7DE")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [
        Spacer(1, 34 * mm),
        Paragraph("INDEPENDENT STRATEGY + EXECUTION REVIEW", STYLES["cover_kicker"]),
        Paragraph("Apex Quant Funded-100K<br/>Qualification Audit", STYLES["cover_title"]),
        Paragraph("Book C / C_FUNDED_V2", STYLES["cover_subtitle"]),
        Spacer(1, 15 * mm),
        decision_box,
        Spacer(1, 12 * mm),
        metrics,
        Spacer(1, 12 * mm),
        evidence_box,
        Spacer(1, 14 * mm),
        Paragraph("Decision date: 3 September 2026", STYLES["cover_subtitle"]),
        PageBreak(),
    ]


def parse_table(lines: list[str], start: int) -> tuple[Table, int]:
    rows: list[list[str]] = []
    index = start
    while index < len(lines) and lines[index].lstrip().startswith("|"):
        raw = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in raw):
            rows.append(raw)
        index += 1
    if not rows:
        raise ValueError("empty Markdown table")
    width = 170 * mm
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    weights = []
    for col in range(column_count):
        longest = max(len(ascii_punctuation(row[col])) for row in normalized)
        weights.append(max(0.8, min(2.4, longest / 16)))
    if column_count >= 7:
        weights[0] = max(weights[0], 1.6)
    total = sum(weights)
    col_widths = [width * weight / total for weight in weights]
    cells = []
    for row_index, row in enumerate(normalized):
        style = STYLES["table_head"] if row_index == 0 else STYLES["table_cell"]
        cells.append([Paragraph(inline_markup(cell), style) for cell in row])
    table = Table(cells, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("BACKGROUND", (0, 1), (-1, -1), WHITE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE]),
                ("GRID", (0, 0), (-1, -1), 0.45, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table, index


def markdown_story(source_text: str) -> list:
    lines = source_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("## Executive decision")), 0)
    lines = lines[start:]
    story: list = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and lines[index + 1].lstrip().startswith("|"):
            table, index = parse_table(lines, index)
            story.extend([table, Spacer(1, 7)])
            continue
        if stripped.startswith("## "):
            heading = ascii_punctuation(stripped[3:])
            story.extend(
                [
                    HRFlowable(width="100%", thickness=2, color=TEAL, spaceBefore=2, spaceAfter=6),
                    Paragraph(html.escape(heading), STYLES["h2"]),
                ]
            )
        elif stripped.startswith("### "):
            story.append(Paragraph(html.escape(ascii_punctuation(stripped[4:])), STYLES["h3"]))
        elif re.match(r"^\d+\.\s+", stripped):
            match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
            assert match
            story.append(
                Paragraph(f'<font color="#0B7189"><b>{match.group(1)}.</b></font> {inline_markup(match.group(2))}', STYLES["number"])
            )
        elif stripped.startswith("- "):
            story.append(Paragraph(f'<font color="#0B7189"><b>-</b></font> {inline_markup(stripped[2:])}', STYLES["bullet"]))
        else:
            story.append(Paragraph(inline_markup(stripped), STYLES["body"]))
        index += 1
    return story


def build_pdf(source: Path, output: Path) -> None:
    source_bytes = source.read_bytes()
    source_text = source_bytes.decode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = AuditDocTemplate(output, source_sha)
    story = cover_story() + markdown_story(source_text)
    doc.build(story)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build_pdf(args.source.resolve(), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
