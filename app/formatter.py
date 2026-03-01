from rich.console import Console
from rich.panel import Panel

from app.schema import Block, Exercise, SessionMeta, TrainingSessionPlan


def format_meta(meta: SessionMeta) -> str:
    return (
        f"[bold]Client:[/bold] {meta.client_name or '—'}\n"
        f"[bold]Date:[/bold] {meta.session_date or '—'}   "
        f"[bold]Session #:[/bold] {meta.session_number or '—'}   "
        f"[bold]Duration:[/bold] {meta.duration_minutes} min\n"
        f"[bold]Focus:[/bold] {meta.focus}\n"
        f"[bold]Constraints:[/bold] {', '.join(meta.constraints) if meta.constraints else '—'}"
    )


def _format_exercise(ex: Exercise, console: Console) -> None:
    console.print(f"\n[bold]{ex.name}[/bold]")

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
        console.print("  " + " | ".join(details))

    if ex.machine_settings:
        ms = ex.machine_settings
        if ms.machine_name or ms.seat or ms.lever or ms.pad:
            ms_parts = []
            if ms.machine_name:
                ms_parts.append(ms.machine_name)
            if ms.seat:
                ms_parts.append(f"Seat {ms.seat}" if not ms.seat.lower().startswith("seat") else ms.seat)
            if ms.lever:
                ms_parts.append(f"Lever {ms.lever}" if not ms.lever.lower().startswith("lever") else ms.lever)
            if ms.pad:
                ms_parts.append(f"Pad {ms.pad}" if not ms.pad.lower().startswith("pad") else ms.pad)
            console.print("  [bold]Machine:[/bold] " + " | ".join(ms_parts))
            if ms.notes:
                console.print(f"  [dim]Setup notes:[/dim] {ms.notes}")

    if ex.loading:
        ld = ex.loading
        if ld.load_lbs is not None or ld.prior_load_lbs is not None or ld.progression_target:
            if ld.load_lbs is not None:
                console.print(f"  [bold]Load:[/bold] {ld.load_lbs} lbs")
            if ld.prior_load_lbs is not None:
                console.print(f"  [bold]Prior:[/bold] {ld.prior_load_lbs} lbs")
            if ld.reps_achieved:
                console.print(f"  [bold]Reps achieved:[/bold] {ld.reps_achieved}")
            if ld.progression_target:
                console.print(f"  [bold]Progression target:[/bold] {ld.progression_target}")

    if ex.cues:
        console.print("  [bold]Cues:[/bold]")
        for c in ex.cues:
            console.print(f"   - {c}")

    if ex.regressions:
        console.print("  [bold]Regressions:[/bold]")
        for r in ex.regressions:
            console.print(f"   - {r}")

    if ex.progressions:
        console.print("  [bold]Progressions:[/bold]")
        for p in ex.progressions:
            console.print(f"   - {p}")


def _format_block(block: Block, console: Console) -> None:
    title = f"{block.title}  ({block.block_type})"
    if block.time_minutes:
        title += f" ~{block.time_minutes} min"
    console.print(Panel.fit(title))

    if block.format:
        console.print(f"[italic]Format:[/italic] {block.format}")

    for ex in block.exercises:
        _format_exercise(ex, console)

    console.print("")


def print_plan(plan: TrainingSessionPlan) -> None:
    console = Console()
    console.print(Panel(format_meta(plan.meta), title="Training Session Plan", expand=False))

    if plan.equipment_used:
        console.print("[bold]Equipment Used[/bold]")
        for item in plan.equipment_used:
            console.print(f"• {item}")
        console.print("")

    for block in plan.blocks:
        _format_block(block, console)

    if plan.progression_notes:
        console.print("[bold]Progression Notes (Next Session)[/bold]")
        for n in plan.progression_notes:
            console.print(f"• {n}")
        console.print("")

    if plan.coaching_notes:
        console.print("[bold]Global Coaching Notes[/bold]")
        for n in plan.coaching_notes:
            console.print(f"• {n}")
        console.print("")
