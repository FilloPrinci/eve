"""The EVE project model (SPEC §2, §4.2, §6, §7.2).

All times are integer microseconds. Model instances are only mutated by commands
(`easy_video_editor.core.commands`), executed through a `ProjectSession`.
"""

from __future__ import annotations

import math
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from easy_video_editor.core.errors import NotFoundError

CURRENT_SCHEMA_VERSION = 1

NonNegativeUs = Annotated[int, Field(ge=0)]
PositiveUs = Annotated[int, Field(gt=0)]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]


class EveModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Origin(StrEnum):
    USER = "user"
    AI = "ai"
    AUTO = "auto"


class ZoneKind(StrEnum):
    EXCLUDE = "exclude"
    """Red: footage that must never be analysed or used. User only."""
    CANDIDATE = "candidate"
    """Light blue: rough interesting regions (Phase 1)."""
    HIGHLIGHT = "highlight"
    """Yellow: precisely trimmed best moments (Phase 2)."""


class TransitionKind(StrEnum):
    CUT = "cut"
    DIP_TO_BLACK = "dip_to_black"
    DIP_TO_WHITE = "dip_to_white"
    CROSSFADE = "crossfade"
    FLASH = "flash"


class TextLook(StrEnum):
    """A coordinated set of text styles (title, caption, call to action)."""

    CLASSIC = "classic"
    """White outlined title, caption on a dark box, call to action on an accent box."""
    BAND = "band"
    """Accent title on a dark full-width band, shadowed left-aligned caption."""
    BOLD = "bold"
    """Large title with an accent shadow, left-aligned caption on an accent bar."""


class ReframeMode(StrEnum):
    CENTER_CROP = "center_crop"
    FOLLOW_CROP = "follow_crop"
    BLURRED_BACKGROUND = "blurred_background"


# --- Clips -------------------------------------------------------------------------------------


class FrameRate(EveModel):
    num: int = Field(gt=0)
    den: int = Field(default=1, gt=0)

    def as_fraction(self) -> Fraction:
        return Fraction(self.num, self.den)


class Clip(EveModel):
    id: str
    path: str
    """Absolute path of the source file. The file is never modified."""
    name: str
    duration_us: PositiveUs
    frame_rate: FrameRate | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    has_audio: bool = True
    fingerprint: str | None = None

    @property
    def file_path(self) -> Path:
        return Path(self.path)


# --- Zones -------------------------------------------------------------------------------------


class Zone(EveModel):
    id: str
    kind: ZoneKind
    clip_id: str
    start_us: NonNegativeUs
    end_us: PositiveUs
    origin: Origin
    locked: bool = False
    score: Unit | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    label: str = ""
    parent_id: str | None = None
    """For highlight zones: the candidate zone they were refined from."""
    stale: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_us <= self.start_us:
            raise ValueError("zone end must be after its start")
        if self.parent_id is not None and self.kind is not ZoneKind.HIGHLIGHT:
            raise ValueError("only highlight zones can have a parent zone")
        return self


# --- Edit timeline -----------------------------------------------------------------------------


class Transition(EveModel):
    kind: TransitionKind = TransitionKind.CUT
    duration_us: NonNegativeUs = 0

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind is TransitionKind.CUT and self.duration_us != 0:
            raise ValueError("a cut has no duration")
        if self.kind is not TransitionKind.CUT and self.duration_us == 0:
            raise ValueError(f"a {self.kind.value} transition needs a duration")
        return self


class Reframe(EveModel):
    mode: ReframeMode | None = None
    """None means: use the output preset's reframing mode."""
    center_x: Unit = 0.5
    center_y: Unit = 0.5


class Segment(EveModel):
    id: str
    clip_id: str
    in_us: NonNegativeUs
    out_us: PositiveUs
    speed: float = Field(default=1.0, ge=0.1, le=10.0)
    zone_id: str | None = None
    """The highlight zone this segment was built from, if any."""
    reframe: Reframe = Field(default_factory=Reframe)
    transition_out: Transition = Field(default_factory=Transition)
    """Transition from this segment into the next one (or into the end card)."""
    game_audio_db: float = 0.0
    origin: Origin = Origin.USER
    stale: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.out_us <= self.in_us:
            raise ValueError("segment out point must be after its in point")
        return self

    @property
    def output_duration_us(self) -> int:
        return round((self.out_us - self.in_us) / self.speed)


class TextOverlay(EveModel):
    id: str
    text: str = Field(min_length=1)
    start_us: NonNegativeUs
    """Position on the edit timeline."""
    end_us: PositiveUs
    style: str = "title"
    x: Unit = 0.5
    y: Unit = 0.5
    origin: Origin = Origin.USER

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_us <= self.start_us:
            raise ValueError("text end must be after its start")
        return self


class MusicCue(EveModel):
    id: str
    path: str
    source_in_us: NonNegativeUs = 0
    source_out_us: PositiveUs | None = None
    """None means: until the end of the track (or of the timeline)."""
    start_us: NonNegativeUs = 0
    """Position on the edit timeline."""
    volume_db: float = 0.0
    fade_in_us: NonNegativeUs = 0
    fade_out_us: NonNegativeUs = 0

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.source_out_us is not None and self.source_out_us <= self.source_in_us:
            raise ValueError("music out point must be after its in point")
        return self


class EndCard(EveModel):
    enabled: bool = True
    duration_us: PositiveUs = 3_000_000
    cta_text: str = ""
    show_logo: bool = True
    background: str | None = None
    """Optional background image path; None means a plain colour from the brand kit."""


ASPECT_PATTERN = r"^[1-9][0-9]*:[1-9][0-9]*$"


class EditTimeline(EveModel):
    id: str
    name: str
    aspect: str = Field(pattern=ASPECT_PATTERN)
    """Aspect-ratio family, e.g. ``16:9``. Output presets of the same family share a timeline."""
    segments: list[Segment] = Field(default_factory=list)
    texts: list[TextOverlay] = Field(default_factory=list)
    music: list[MusicCue] = Field(default_factory=list)
    end_card: EndCard = Field(default_factory=EndCard)
    show_logo: bool = True

    def segment_index(self, segment_id: str) -> int:
        for index, segment in enumerate(self.segments):
            if segment.id == segment_id:
                return index
        raise NotFoundError(f"Segment {segment_id} does not exist in timeline {self.id}.")

    def segment(self, segment_id: str) -> Segment:
        return self.segments[self.segment_index(segment_id)]

    def text(self, text_id: str) -> TextOverlay:
        for text in self.texts:
            if text.id == text_id:
                return text
        raise NotFoundError(f"Text {text_id} does not exist in timeline {self.id}.")

    @property
    def duration_us(self) -> int:
        """Output duration. Crossfade handles are centred on the cut, so they do not change it."""
        total = sum(segment.output_duration_us for segment in self.segments)
        if self.end_card.enabled:
            total += self.end_card.duration_us
        return total


# --- Settings ----------------------------------------------------------------------------------


def aspect_of(width: int, height: int) -> str:
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


class OutputPreset(EveModel):
    id: str
    name: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(default=30, gt=0)
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    max_duration_us: PositiveUs | None = None
    """Platform limit. Limits change over time: keep them editable, never hardcode them."""
    reframe_mode: ReframeMode = ReframeMode.CENTER_CROP

    @property
    def aspect(self) -> str:
        return aspect_of(self.width, self.height)


def default_output_presets() -> list[OutputPreset]:
    return [
        OutputPreset(id="youtube", name="YouTube", width=1920, height=1080),
        OutputPreset(
            id="shorts",
            name="Shorts / Reels / TikTok",
            width=1080,
            height=1920,
            max_duration_us=180_000_000,
            reframe_mode=ReframeMode.FOLLOW_CROP,
        ),
        OutputPreset(
            id="instagram_feed",
            name="Instagram feed",
            width=1080,
            height=1350,
            reframe_mode=ReframeMode.BLURRED_BACKGROUND,
        ),
    ]


class ProjectSettings(EveModel):
    output_presets: list[OutputPreset] = Field(default_factory=default_output_presets)
    enabled_preset_ids: list[str] = Field(default_factory=lambda: ["youtube"])
    target_duration_us: PositiveUs = 30_000_000
    mood: str = "adrenaline"
    yellow_tolerance_us: NonNegativeUs = 2_000_000
    shot_length_min_us: PositiveUs = 1_000_000
    shot_length_max_us: PositiveUs = 4_000_000
    transition_style: TransitionKind = TransitionKind.CUT
    text_look: TextLook = TextLook.CLASSIC
    loudness_target_lufs: float = Field(default=-14.0, le=0.0)
    game_audio_db: float = -6.0
    brand_kit_id: str | None = None
    prompt: str = ""
    """Optional free-text instructions for the AI."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.shot_length_max_us < self.shot_length_min_us:
            raise ValueError("maximum shot length must not be shorter than the minimum")
        preset_ids = [preset.id for preset in self.output_presets]
        if len(set(preset_ids)) != len(preset_ids):
            raise ValueError("output preset ids must be unique")
        unknown = sorted(set(self.enabled_preset_ids) - set(preset_ids))
        if unknown:
            raise ValueError(f"enabled output presets do not exist: {', '.join(unknown)}")
        return self

    def preset(self, preset_id: str) -> OutputPreset:
        for preset in self.output_presets:
            if preset.id == preset_id:
                return preset
        raise NotFoundError(f"Output preset {preset_id} does not exist.")


# --- Project -----------------------------------------------------------------------------------


class Project(EveModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    name: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)
    """Incremented on every change (including undo/redo); used to detect concurrent edits."""
    clips: list[Clip] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    timelines: list[EditTimeline] = Field(default_factory=list)
    settings: ProjectSettings = Field(default_factory=ProjectSettings)

    def clip(self, clip_id: str) -> Clip:
        for clip in self.clips:
            if clip.id == clip_id:
                return clip
        raise NotFoundError(f"Clip {clip_id} does not exist.")

    def zone(self, zone_id: str) -> Zone:
        for zone in self.zones:
            if zone.id == zone_id:
                return zone
        raise NotFoundError(f"Zone {zone_id} does not exist.")

    def timeline(self, timeline_id: str) -> EditTimeline:
        for timeline in self.timelines:
            if timeline.id == timeline_id:
                return timeline
        raise NotFoundError(f"Timeline {timeline_id} does not exist.")

    def zones_of(self, clip_id: str, kind: ZoneKind | None = None) -> list[Zone]:
        return [
            zone
            for zone in self.zones
            if zone.clip_id == clip_id and (kind is None or zone.kind is kind)
        ]

    def children_of(self, zone_id: str) -> list[Zone]:
        return [zone for zone in self.zones if zone.parent_id == zone_id]

    def segments_using(self, zone_id: str) -> list[Segment]:
        return [
            segment
            for timeline in self.timelines
            for segment in timeline.segments
            if segment.zone_id == zone_id
        ]
