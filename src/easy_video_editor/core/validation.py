"""Final validation before rendering (SPEC §4.4 rule 5).

This check is independent of the command invariants and runs right before export: it also
verifies that media files exist and that the result fits the output preset.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from easy_video_editor.core.model import OutputPreset, Project
from easy_video_editor.core.rules import first_exclusion_hit, span, used_source_range
from easy_video_editor.core.timecode import format_us


class Severity(StrEnum):
    ERROR = "error"
    """Blocks the export."""
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    item_ids: tuple[str, ...] = ()
    """Offending items, so the UI can select them."""


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_for_render(
    project: Project,
    timeline_id: str,
    preset: OutputPreset,
    file_exists: Callable[[Path], bool] = Path.is_file,
) -> ValidationReport:
    report = ValidationReport()

    def error(code: str, message: str, *item_ids: str) -> None:
        report.issues.append(Issue(Severity.ERROR, code, message, item_ids))

    def warning(code: str, message: str, *item_ids: str) -> None:
        report.issues.append(Issue(Severity.WARNING, code, message, item_ids))

    timeline = project.timeline(timeline_id)
    clips = {clip.id: clip for clip in project.clips}

    if preset.aspect != timeline.aspect:
        error(
            "aspect_mismatch",
            f"Preset {preset.name} is {preset.aspect} but timeline {timeline.name} is "
            f"{timeline.aspect}.",
            timeline.id,
        )
    if not timeline.segments:
        error("empty_timeline", f"Timeline {timeline.name} has no segments.", timeline.id)

    missing_files: set[str] = set()
    for index, segment in enumerate(timeline.segments):
        name = f"Segment {index + 1} ({span(segment.in_us, segment.out_us)})"
        clip = clips.get(segment.clip_id)
        if clip is None:
            error(
                "missing_clip", f"{name} references a clip that is not in the project.", segment.id
            )
            continue
        if clip.id not in missing_files and not file_exists(clip.file_path):
            missing_files.add(clip.id)
            error("missing_media", f"The file of clip {clip.name} is missing: {clip.path}", clip.id)
        used_start, used_end = used_source_range(timeline, index)
        if used_start < 0 or used_end > clip.duration_us:
            error("outside_media", f"{name} needs frames outside clip {clip.name}.", segment.id)
        hit = first_exclusion_hit(project, clip.id, used_start, used_end)
        if hit is not None:
            error(
                "excluded_frames",
                f"{name} uses frames of the exclude zone {span(hit.start_us, hit.end_us)}.",
                segment.id,
                hit.id,
            )
        if segment.stale:
            warning("stale_segment", f"{name} is stale.", segment.id)

    for cue in timeline.music:
        if not file_exists(Path(cue.path)):
            error("missing_music", f"The music file is missing: {cue.path}", cue.id)

    if preset.max_duration_us is not None and timeline.duration_us > preset.max_duration_us:
        error(
            "too_long",
            f"Timeline {timeline.name} lasts {format_us(timeline.duration_us)}, longer than the "
            f"{format_us(preset.max_duration_us)} allowed by preset {preset.name}.",
            timeline.id,
        )
    return report
