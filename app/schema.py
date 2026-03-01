from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


Intensity = Literal["L", "M", "H", "RPE6", "RPE7", "RPE8", "RPE9"]


class MachineSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    machine_name: Optional[str] = Field(default=None, description="e.g., Leg Press, Chest Press, Lat Pulldown")
    seat: Optional[str] = Field(default=None, description="e.g., Seat 5")
    lever: Optional[str] = Field(default=None, description="e.g., Lever 4")
    pad: Optional[str] = Field(default=None, description="e.g., Pad 2")
    notes: Optional[str] = Field(default=None, description="Any extra setup notes")


class Loading(BaseModel):
    model_config = ConfigDict(extra="ignore")

    load_lbs: Optional[float] = Field(default=None, description="Working load in pounds")
    prior_load_lbs: Optional[float] = Field(default=None, description="Previous session load, if known")
    reps_achieved: Optional[str] = Field(default=None, description="e.g., '12,12,10' or '12-14 each'")
    progression_target: Optional[str] = Field(default=None, description="e.g., 'Add 5 lbs next time if all sets hit 12'")


class Exercise(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    sets: Optional[int] = None
    reps: Optional[str] = None  # allow ranges "10-12", time "45 sec", etc.
    tempo: Optional[str] = Field(default=None, description="e.g., '3-1-1' or 'controlled'")
    rest_seconds: Optional[int] = Field(default=None, description="Rest after the set/exercise")
    intensity: Optional[Intensity] = Field(default=None, description="Load intensity marker")
    machine_settings: Optional[MachineSettings] = None
    loading: Optional[Loading] = None
    cues: List[str] = Field(default_factory=list)
    regressions: List[str] = Field(default_factory=list)
    progressions: List[str] = Field(default_factory=list)


class Block(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str  # e.g., "Step + Strength Circuit", "Lower Body Machines"
    block_type: Literal["warmup", "main", "core_balance", "cooldown", "finisher", "mobility"]
    time_minutes: Optional[int] = None
    format: Optional[str] = Field(default=None, description="e.g., 'Circuit x2', 'Straight sets', 'Superset'")
    exercises: List[Exercise]


class SessionMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_name: Optional[str] = None
    session_date: Optional[str] = Field(default=None, description="YYYY-MM-DD if known")
    session_number: Optional[int] = None
    duration_minutes: int = 50
    focus: str = Field(description="1-line focus statement")
    constraints: List[str] = Field(default_factory=list, description="Limitations / injuries / special notes")
    readiness_notes: List[str] = Field(default_factory=list, description="Day-of readiness or precautions")

    @field_validator("duration_minutes")
    @classmethod
    def duration_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("duration_minutes must be greater than 0")
        return v


class TrainingSessionPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meta: SessionMeta
    equipment_used: List[str] = Field(default_factory=list)
    blocks: List[Block]
    progression_notes: List[str] = Field(default_factory=list, description="How to progress next session")
    coaching_notes: List[str] = Field(default_factory=list, description="Global cues / reminders / watch-outs")
