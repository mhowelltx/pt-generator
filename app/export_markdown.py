import os
from pathlib import Path

from app.schema import Block, Exercise, TrainingSessionPlan
from app.storage import _slug

OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", str(Path(__file__).parent.parent / "outputs")))


def _output_path(plan: TrainingSessionPlan) -> Path:
    meta = plan.meta
    client_slug = _slug(meta.client_name or "unknown")
    session_num = meta.session_number or 0
    filename = f"{meta.session_date or 'undated'}_session_{session_num}.md"
    return OUTPUTS_DIR / client_slug / filename


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


def _render_exercise(ex: Exercise) -> str:
    lines = [f"#### {ex.name}"]

    details = []
    if ex.sets is not None:
        details.append(f"Sets: {ex.sets}")
    if ex.reps:
        details.append(f"Reps: {ex.reps}")
    if ex.tempo:
        details.append(f"Tempo: {ex.tempo}")
    if ex.rest_seconds is not None:
        details.append(f"Rest: {ex.rest_seconds}s")
    if ex.intensity:
        details.append(f"Intensity: {ex.intensity}")
    if details:
        lines.append(" | ".join(details))

    if ex.machine_settings:
        ms = ex.machine_settings
        parts = _machine_parts(ms)
        if parts:
            lines.append(f"**Machine:** {' | '.join(parts)}")
        if ms.notes:
            lines.append(f"*Setup notes:* {ms.notes}")

    if ex.loading:
        ld = ex.loading
        if ld.load_lbs is not None:
            lines.append(f"**Load:** {ld.load_lbs} lbs")
        if ld.prior_load_lbs is not None:
            lines.append(f"**Prior:** {ld.prior_load_lbs} lbs")
        if ld.reps_achieved:
            lines.append(f"**Reps achieved:** {ld.reps_achieved}")
        if ld.progression_target:
            lines.append(f"**Progression target:** {ld.progression_target}")

    for label, items in [("Cues", ex.cues), ("Regressions", ex.regressions), ("Progressions", ex.progressions)]:
        if items:
            lines.append(f"**{label}:**")
            lines.extend(f"- {item}" for item in items)

    return "\n\n".join(lines)


def _render_block(block: Block) -> str:
    title = f"### {block.title} ({block.block_type})"
    if block.time_minutes:
        title += f" ~{block.time_minutes} min"

    parts = [title]
    if block.format:
        parts.append(f"*Format: {block.format}*")
    for ex in block.exercises:
        parts.append(_render_exercise(ex))

    return "\n\n".join(parts)


def export(plan: TrainingSessionPlan) -> Path:
    meta = plan.meta

    header = "\n".join([
        "# Training Session Plan",
        "",
        f"**Client:** {meta.client_name or '—'}",
        f"**Date:** {meta.session_date or '—'}",
        f"**Session #:** {meta.session_number or '—'}",
        f"**Duration:** {meta.duration_minutes} min",
        f"**Focus:** {meta.focus}",
        f"**Constraints:** {', '.join(meta.constraints) if meta.constraints else '—'}",
    ])

    sections = [header]

    if plan.equipment_used:
        eq = ["## Equipment Used"] + [f"- {item}" for item in plan.equipment_used]
        sections.append("\n".join(eq))

    for block in plan.blocks:
        sections.append(_render_block(block))

    if plan.progression_notes:
        pn = ["## Progression Notes (Next Session)"] + [f"- {n}" for n in plan.progression_notes]
        sections.append("\n".join(pn))

    if plan.coaching_notes:
        cn = ["## Global Coaching Notes"] + [f"- {n}" for n in plan.coaching_notes]
        sections.append("\n".join(cn))

    content = "\n\n---\n\n".join(sections)

    path = _output_path(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
