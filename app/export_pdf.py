import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.schema import Block, Exercise, TrainingSessionPlan
from app.storage import _slug

OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", str(Path(__file__).parent.parent / "outputs")))

# Brand palette
_ORANGE = colors.HexColor("#f97316")
_DARK = colors.HexColor("#111827")
_GRAY = colors.HexColor("#6b7280")
_LIGHT_GRAY = colors.HexColor("#f3f4f6")
_MID_GRAY = colors.HexColor("#e5e7eb")
_TEXT_DARK = colors.HexColor("#111827")
_TEXT_MID = colors.HexColor("#374151")

_BLOCK_COLORS: dict[str, object] = {
    "warmup": colors.HexColor("#3b82f6"),
    "main": colors.HexColor("#f97316"),
    "core_balance": colors.HexColor("#10b981"),
    "cooldown": colors.HexColor("#8b5cf6"),
    "finisher": colors.HexColor("#ef4444"),
    "mobility": colors.HexColor("#14b8a6"),
}

PAGE_W, PAGE_H = A4
L_MARGIN = 15 * mm
R_MARGIN = 15 * mm
CONTENT_W = PAGE_W - L_MARGIN - R_MARGIN


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _build_styles() -> dict:
    styles: dict = {}

    styles["title_white"] = ParagraphStyle(
        "title_white",
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=colors.white,
        leading=20,
        spaceAfter=1 * mm,
    )
    styles["meta_accent"] = ParagraphStyle(
        "meta_accent",
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=_ORANGE,
        leading=16,
    )
    styles["meta_text"] = ParagraphStyle(
        "meta_text",
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.white,
        leading=12,
    )
    styles["block_title"] = ParagraphStyle(
        "block_title",
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=colors.white,
        leading=14,
    )
    styles["block_subtitle"] = ParagraphStyle(
        "block_subtitle",
        fontName="Helvetica-Oblique",
        fontSize=8.5,
        textColor=colors.HexColor("#d1d5db"),
        leading=11,
    )
    styles["ex_name"] = ParagraphStyle(
        "ex_name",
        fontName="Helvetica-Bold",
        fontSize=9.5,
        textColor=_TEXT_DARK,
        leading=13,
    )
    styles["ex_detail"] = ParagraphStyle(
        "ex_detail",
        fontName="Helvetica",
        fontSize=8.5,
        textColor=_GRAY,
        leading=11,
    )
    styles["setup_note"] = ParagraphStyle(
        "setup_note",
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=_GRAY,
        leading=10,
    )
    styles["section_label"] = ParagraphStyle(
        "section_label",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        textColor=_GRAY,
        leading=10,
    )
    styles["bullet"] = ParagraphStyle(
        "bullet",
        fontName="Helvetica",
        fontSize=8.5,
        textColor=_TEXT_MID,
        leading=11,
        leftIndent=6,
    )
    styles["notes_label"] = ParagraphStyle(
        "notes_label",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        textColor=_TEXT_MID,
        leading=12,
    )
    styles["notes_text"] = ParagraphStyle(
        "notes_text",
        fontName="Helvetica",
        fontSize=8.5,
        textColor=_TEXT_MID,
        leading=11,
    )
    styles["equip_text"] = ParagraphStyle(
        "equip_text",
        fontName="Helvetica",
        fontSize=8.5,
        textColor=_GRAY,
        leading=11,
    )
    styles["section_heading"] = ParagraphStyle(
        "section_heading",
        fontName="Helvetica-Bold",
        fontSize=7,
        textColor=_GRAY,
        leading=9,
        spaceAfter=1 * mm,
    )
    # Progress report styles
    styles["h1"] = ParagraphStyle(
        "h1",
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=_TEXT_DARK,
        leading=22,
        spaceAfter=2 * mm,
    )
    styles["h2"] = ParagraphStyle(
        "h2",
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=_TEXT_DARK,
        leading=16,
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    styles["body"] = ParagraphStyle(
        "body",
        fontName="Helvetica",
        fontSize=9,
        textColor=_TEXT_MID,
        leading=12,
    )
    styles["pr_accent"] = ParagraphStyle(
        "pr_accent",
        fontName="Helvetica-Bold",
        fontSize=13,
        textColor=_ORANGE,
        leading=16,
        spaceAfter=3 * mm,
    )
    styles["table_header"] = ParagraphStyle(
        "table_header",
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=colors.white,
        leading=10,
    )
    styles["table_cell"] = ParagraphStyle(
        "table_cell",
        fontName="Helvetica",
        fontSize=8,
        textColor=_TEXT_MID,
        leading=10,
    )
    return styles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _machine_parts(ms) -> list[str]:
    parts = []
    if ms.machine_name:
        parts.append(ms.machine_name)
    if ms.seat:
        parts.append(f"Seat {ms.seat}" if not ms.seat.lower().startswith("seat") else ms.seat)
    if ms.lever:
        parts.append(f"Lever {ms.lever}" if not ms.lever.lower().startswith("lever") else ms.lever)
    if ms.pad:
        parts.append(f"Pad {ms.pad}" if not ms.pad.lower().startswith("pad") else ms.pad)
    return parts


def _output_path(plan: TrainingSessionPlan, outputs_dir: Path | None = None) -> Path:
    meta = plan.meta
    client_slug = _slug(meta.client_name or "unknown")
    session_num = meta.session_number or 0
    filename = f"{meta.session_date or 'undated'}_session_{session_num}.pdf"
    return (outputs_dir or OUTPUTS_DIR) / client_slug / filename


def _report_path(client_name: str, outputs_dir: Path | None = None) -> Path:
    client_slug = _slug(client_name or "unknown")
    filename = f"progress_report_{date.today().isoformat()}.pdf"
    return (outputs_dir or OUTPUTS_DIR) / client_slug / filename


# ---------------------------------------------------------------------------
# Session plan PDF builders
# ---------------------------------------------------------------------------

def _meta_section(plan: TrainingSessionPlan, styles: dict) -> list:
    meta = plan.meta
    col1 = CONTENT_W * 0.62
    col2 = CONTENT_W * 0.38

    rows = []

    # Title row (spans both cols)
    rows.append([Paragraph("Training Session Plan", styles["title_white"]), ""])

    # Client name + session number
    session_text = f"Session #{meta.session_number}" if meta.session_number else ""
    rows.append([
        Paragraph(meta.client_name or "—", styles["meta_accent"]),
        Paragraph(session_text, styles["meta_accent"]),
    ])

    # Date + duration
    rows.append([
        Paragraph(f"Date: {meta.session_date or '—'}", styles["meta_text"]),
        Paragraph(f"Duration: {meta.duration_minutes} min", styles["meta_text"]),
    ])

    # Focus (full width)
    rows.append([Paragraph(f"Focus: {meta.focus}", styles["meta_text"]), ""])

    # Constraints (full width, if any)
    if meta.constraints:
        rows.append([Paragraph(f"Constraints: {', '.join(meta.constraints)}", styles["meta_text"]), ""])

    # Readiness notes (full width, if any)
    if meta.readiness_notes:
        rows.append([Paragraph(f"Readiness: {'; '.join(meta.readiness_notes)}", styles["meta_text"]), ""])

    table = Table(rows, colWidths=[col1, col2])
    ts = TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _DARK),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        # Orange line under title row
        ("LINEBELOW", (0, 0), (-1, 0), 2, _ORANGE),
        # Span title row across both columns
        ("SPAN", (0, 0), (1, 0)),
        # Span focus row
        ("SPAN", (0, 3), (1, 3)),
    ])
    # Span constraints + readiness rows if present
    extra_spans = 0
    if meta.constraints:
        extra_spans += 1
        ts.add("SPAN", (0, 4), (1, 4))
    if meta.readiness_notes:
        ts.add("SPAN", (0, 4 + extra_spans), (1, 4 + extra_spans))

    table.setStyle(ts)
    return [table]


def _equipment_section(plan: TrainingSessionPlan, styles: dict) -> list:
    if not plan.equipment_used:
        return []
    return [
        Paragraph("EQUIPMENT", styles["section_heading"]),
        Paragraph(", ".join(plan.equipment_used), styles["equip_text"]),
    ]


def _exercise_table(ex: Exercise, styles: dict) -> Table:
    rows = []

    rows.append([Paragraph(ex.name, styles["ex_name"])])

    parts = []
    if ex.sets is not None:
        parts.append(f"Sets: {ex.sets}")
    if ex.reps:
        parts.append(f"Reps: {ex.reps}")
    if ex.tempo:
        parts.append(f"Tempo: {ex.tempo}")
    if ex.rest_seconds is not None:
        parts.append(f"Rest: {ex.rest_seconds}s")
    if ex.intensity:
        parts.append(f"Intensity: {ex.intensity}")
    if parts:
        rows.append([Paragraph("  ·  ".join(parts), styles["ex_detail"])])

    if ex.machine_settings:
        ms = ex.machine_settings
        machine_parts = _machine_parts(ms)
        if machine_parts:
            rows.append([Paragraph(f"Machine: {' | '.join(machine_parts)}", styles["setup_note"])])
        if ms.notes:
            rows.append([Paragraph(f"Setup: {ms.notes}", styles["setup_note"])])

    if ex.loading:
        ld = ex.loading
        load_parts = []
        if ld.load_lbs is not None:
            load_parts.append(f"Load: {ld.load_lbs} lbs")
        if ld.prior_load_lbs is not None:
            load_parts.append(f"Prior: {ld.prior_load_lbs} lbs")
        if ld.reps_achieved:
            load_parts.append(f"Reps achieved: {ld.reps_achieved}")
        if load_parts:
            rows.append([Paragraph("  ·  ".join(load_parts), styles["ex_detail"])])
        if ld.progression_target:
            rows.append([Paragraph(f"Next: {ld.progression_target}", styles["setup_note"])])

    for label, items in [("Cues", ex.cues), ("Regressions", ex.regressions), ("Progressions", ex.progressions)]:
        if items:
            rows.append([Paragraph(f"{label}:", styles["section_label"])])
            for item in items:
                rows.append([Paragraph(f"• {item}", styles["bullet"])])

    t = Table(rows, colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _LIGHT_GRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 6),
        ("BOTTOMPADDING", (-1, -1), (-1, -1), 6),
        ("BOX", (0, 0), (-1, -1), 0.5, _MID_GRAY),
    ]))
    return t


def _block_section(block: Block, styles: dict) -> list:
    block_color = _BLOCK_COLORS.get(block.block_type, _DARK)
    type_label = block.block_type.replace("_", " ").upper()
    time_text = f" · {block.time_minutes} min" if block.time_minutes else ""
    format_text = f" · {block.format}" if block.format else ""

    header_rows = [
        [Paragraph(block.title, styles["block_title"])],
        [Paragraph(f"{type_label}{time_text}{format_text}", styles["block_subtitle"])],
    ]
    header_table = Table(header_rows, colWidths=[CONTENT_W])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), block_color),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))

    story: list = [header_table]
    for i, ex in enumerate(block.exercises):
        if i > 0:
            story.append(Spacer(1, 2))
        story.append(_exercise_table(ex, styles))

    return [KeepTogether(story)]


def _notes_section(label: str, items: list[str], bg_hex: str, styles: dict) -> list:
    bg = colors.HexColor(bg_hex)
    rows = [[Paragraph(label, styles["notes_label"])]]
    for item in items:
        rows.append([Paragraph(f"• {item}", styles["notes_text"])])
    t = Table(rows, colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("BOX", (0, 0), (-1, -1), 0.5, _MID_GRAY),
    ]))
    return [t]


def _make_footer(client: str, session_date: str):
    label = "  ·  ".join(x for x in [client, session_date] if x)

    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_GRAY)
        if label:
            canvas.drawString(L_MARGIN, 10 * mm, label)
        canvas.drawRightString(PAGE_W - R_MARGIN, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    return _draw


# ---------------------------------------------------------------------------
# Public: single session plan PDF
# ---------------------------------------------------------------------------

def export(plan: TrainingSessionPlan, outputs_dir: Path | None = None) -> Path:
    path = _output_path(plan, outputs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=L_MARGIN,
        rightMargin=R_MARGIN,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = _build_styles()
    story: list = []

    story.extend(_meta_section(plan, styles))
    story.append(Spacer(1, 5 * mm))

    if plan.equipment_used:
        story.extend(_equipment_section(plan, styles))
        story.append(Spacer(1, 4 * mm))

    for block in plan.blocks:
        story.extend(_block_section(block, styles))
        story.append(Spacer(1, 3 * mm))

    if plan.progression_notes:
        story.extend(_notes_section("Progression Notes — Next Session", plan.progression_notes, "#fffde7", styles))
        story.append(Spacer(1, 2 * mm))

    if plan.coaching_notes:
        story.extend(_notes_section("Global Coaching Notes", plan.coaching_notes, "#eff6ff", styles))

    footer_fn = _make_footer(plan.meta.client_name or "", plan.meta.session_date or "")
    doc.build(story, onFirstPage=footer_fn, onLaterPages=footer_fn)
    return path


# ---------------------------------------------------------------------------
# Public: client progress report PDF
# ---------------------------------------------------------------------------

def export_history_report(
    profile: dict,
    history: list,
    outputs_dir: Path | None = None,
) -> Path:
    client_name = profile.get("client_name", "Unknown")
    path = _report_path(client_name, outputs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=L_MARGIN,
        rightMargin=R_MARGIN,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = _build_styles()
    story: list = []

    # --- Header ---
    story.append(Paragraph("Client Progress Report", styles["h1"]))
    story.append(Paragraph(client_name, styles["pr_accent"]))

    # --- Profile summary ---
    story.append(HRFlowable(width=CONTENT_W, thickness=0.5, color=_MID_GRAY, spaceAfter=3 * mm))

    constraints = profile.get("constraints", [])
    if constraints:
        story.append(Paragraph("Constraints / Injuries", styles["h2"]))
        for c in constraints:
            story.append(Paragraph(f"• {c}", styles["body"]))

    equipment = profile.get("preferred_equipment", [])
    if equipment:
        story.append(Paragraph("Preferred Equipment", styles["h2"]))
        story.append(Paragraph(", ".join(equipment), styles["body"]))

    notes = profile.get("notes", "").strip()
    if notes:
        story.append(Paragraph("Trainer Notes", styles["h2"]))
        story.append(Paragraph(notes, styles["body"]))

    # --- Session history table ---
    active = [s for s in history if not s.get("archived", False)]
    story.append(Paragraph("Session History", styles["h2"]))

    if not active:
        story.append(Paragraph("No sessions recorded.", styles["body"]))
    else:
        header = [
            Paragraph("#", styles["table_header"]),
            Paragraph("Date", styles["table_header"]),
            Paragraph("Focus", styles["table_header"]),
            Paragraph("Loads", styles["table_header"]),
            Paragraph("Progression Notes", styles["table_header"]),
        ]
        rows = [header]
        for s in reversed(active):
            sn = f"#{s['session_number']}" if s.get("session_number") else "—"
            sd = s.get("session_date") or "—"
            focus = s.get("focus") or "—"
            load_count = str(len(s.get("loads", {}))) if s.get("loads") else "0"
            prog = "; ".join(s.get("progression_notes", [])[:2])
            if len(s.get("progression_notes", [])) > 2:
                prog += f" (+{len(s['progression_notes']) - 2})"
            rows.append([
                Paragraph(sn, styles["table_cell"]),
                Paragraph(sd, styles["table_cell"]),
                Paragraph(focus, styles["table_cell"]),
                Paragraph(load_count, styles["table_cell"]),
                Paragraph(prog or "—", styles["table_cell"]),
            ])

        col_widths = [
            CONTENT_W * 0.07,
            CONTENT_W * 0.13,
            CONTENT_W * 0.28,
            CONTENT_W * 0.09,
            CONTENT_W * 0.43,
        ]
        hist_table = Table(rows, colWidths=col_widths, repeatRows=1)
        hist_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _DARK),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT_GRAY]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.3, _MID_GRAY),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(hist_table)

    # --- Load progression per exercise ---
    story.append(Paragraph("Load Progression", styles["h2"]))

    # Collect all exercises that have loads across sessions
    exercise_loads: dict[str, list[tuple[str, float]]] = {}
    for s in active:
        sd = s.get("session_date") or "?"
        for ex_name, load in (s.get("loads") or {}).items():
            exercise_loads.setdefault(ex_name, []).append((sd, load))

    if not exercise_loads:
        story.append(Paragraph("No load data recorded.", styles["body"]))
    else:
        for ex_name, entries in sorted(exercise_loads.items()):
            if len(entries) < 2:
                continue  # only show exercises tracked across multiple sessions
            header_row = [
                Paragraph("Date", styles["table_header"]),
                Paragraph("Load (lbs)", styles["table_header"]),
            ]
            data_rows = [header_row] + [
                [Paragraph(d, styles["table_cell"]), Paragraph(str(w), styles["table_cell"])]
                for d, w in entries
            ]
            ex_table = Table(data_rows, colWidths=[CONTENT_W * 0.35, CONTENT_W * 0.35])
            ex_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT_GRAY]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.3, _MID_GRAY),
            ]))
            story.append(Paragraph(ex_name, styles["body"]))
            story.append(Spacer(1, 1 * mm))
            story.append(ex_table)
            story.append(Spacer(1, 3 * mm))

    footer_fn = _make_footer(client_name, date.today().isoformat())
    doc.build(story, onFirstPage=footer_fn, onLaterPages=footer_fn)
    return path
