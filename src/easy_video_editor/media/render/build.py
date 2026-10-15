"""Compile an edit timeline into a `RenderGraph`.

Frame and sample counts come from cumulative boundaries, so rounding never accumulates: the
output lasts exactly ``EditTimeline.duration_us`` rounded to the frame grid.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.model import (
    Clip,
    EditTimeline,
    OutputPreset,
    Project,
    ReframeMode,
    Segment,
    TransitionKind,
)
from easy_video_editor.core.rules import HANDLE_TRANSITIONS, handle_us
from easy_video_editor.media.errors import MediaError
from easy_video_editor.media.render.graph import (
    ClipNode,
    EndCardNode,
    Fade,
    InputNode,
    Join,
    LogoNode,
    Loudness,
    MusicNode,
    Reframe,
    ReframeKind,
    RenderGraph,
    TextNode,
)
from easy_video_editor.media.render.styles import (
    DEFAULT_PRIMARY_COLOR,
    DEFAULT_TEXT_COLOR,
    TEXT_LOOKS,
    ffmpeg_color,
    resolve_color,
)
from easy_video_editor.resources import DEFAULT_FONT

US = 1_000_000
SAMPLE_RATE = 48_000
ASPECT_TOLERANCE = 0.01
WATERMARK_WIDTH = 0.12
"""Logo watermark width as a fraction of the output width."""
END_CARD_LOGO_WIDTH = 0.4


class RenderError(MediaError):
    """The timeline cannot be rendered."""


FollowPath = Sequence[tuple[int, float]]
"""(source time in µs, crop centre as a fraction of the cropped axis), sorted by time."""


@dataclass(frozen=True)
class RenderOptions:
    media_path: Callable[[Clip], Path] = lambda clip: clip.file_path
    """Which file to read for a clip: the source, or its proxy for drafts."""
    brand: BrandKit | None = None
    scale: float = 1.0
    """Output size factor (drafts render smaller)."""
    follow_paths: Mapping[str, FollowPath] = field(default_factory=dict)
    """Per clip id, for follow-crop reframing. Clips without a path use a centred crop."""
    music_durations_us: Mapping[str, int] = field(default_factory=dict)
    """Known durations of music files, used when a cue has no out point."""
    loudness: bool = True
    duck_game_audio: bool = True


def even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def _frames(us: int, fps: int) -> int:
    return round(us * fps / US)


def _samples(us: int) -> int:
    return round(us * SAMPLE_RATE / US)


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


CHAR_WIDTH = 0.5
"""Conservative average glyph width as a fraction of the font size (the default font is
condensed), used to keep text inside the frame."""
TEXT_MAX_WIDTH = 0.9


def fitted_font_size(text: str, relative_size: float, width: int, height: int) -> int:
    """Style size relative to the short side, reduced so the longest line fits the width."""
    size = relative_size * min(width, height)
    longest = max((len(line) for line in text.splitlines()), default=1)
    size = min(size, TEXT_MAX_WIDTH * width / (max(longest, 1) * CHAR_WIDTH))
    return max(8, round(size))


def plan_reframe(
    clip: Clip,
    segment: Segment,
    preset: OutputPreset,
    source_start_us: int,
    follow_path: FollowPath | None,
) -> Reframe:
    mode = segment.reframe.mode or preset.reframe_mode
    if not clip.width or not clip.height:
        return Reframe(ReframeKind.SCALE)
    source_aspect = clip.width / clip.height
    output_aspect = preset.width / preset.height
    if abs(source_aspect - output_aspect) / output_aspect < ASPECT_TOLERANCE:
        return Reframe(ReframeKind.SCALE)
    if mode is ReframeMode.BLURRED_BACKGROUND:
        return Reframe(ReframeKind.BLUR)

    if source_aspect > output_aspect:
        axis, center, window = "x", segment.reframe.center_x, output_aspect / source_aspect
    else:
        axis, center, window = "y", segment.reframe.center_y, source_aspect / output_aspect

    def position(fraction: float) -> float:
        return round(_clamp((fraction - window / 2) / (1 - window)), 4)

    keyframes: tuple[tuple[float, float], ...] = ()
    if mode is ReframeMode.FOLLOW_CROP and follow_path:
        keyframes = tuple(
            (round((time_us - source_start_us) / segment.speed / US, 4), position(fraction))
            for time_us, fraction in follow_path
        )
    return Reframe(ReframeKind.CROP, axis, position(center), keyframes)


def build_graph(
    project: Project, timeline: EditTimeline, preset: OutputPreset, options: RenderOptions
) -> RenderGraph:
    fps = preset.fps
    graph = RenderGraph(
        width=even(preset.width * options.scale),
        height=even(preset.height * options.scale),
        fps=fps,
        sample_rate=SAMPLE_RATE,
        duck_game_audio=options.duck_game_audio,
    )
    brand = options.brand
    font = Path(brand.font_path) if brand and brand.font_path else DEFAULT_FONT
    font_file = f"font{font.suffix.lower()}"
    graph.files[font_file] = font
    segments = timeline.segments
    end_card = timeline.end_card if timeline.end_card.enabled else None

    # Exact core lengths from cumulative boundaries.
    durations = [segment.output_duration_us for segment in segments]
    if end_card:
        durations.append(end_card.duration_us)
    frames, samples, elapsed = [], [], 0
    for duration in durations:
        frames.append(_frames(elapsed + duration, fps) - _frames(elapsed, fps))
        samples.append(_samples(elapsed + duration) - _samples(elapsed))
        elapsed += duration

    count = len(durations)
    head_frames, tail_frames = [0] * count, [0] * count
    fade_in: list[Fade | None] = [None] * count
    fade_out: list[Fade | None] = [None] * count
    for index in range(count - 1):
        transition = segments[index].transition_out
        if transition.kind in HANDLE_TRANSITIONS:
            half = max(1, _frames(transition.duration_us // 2, fps))
            half_samples = round(half * SAMPLE_RATE / fps)
            head_frames[index + 1] = tail_frames[index] = half
            graph.joins.append(Join(2 * half, 2 * half_samples))
            continue
        graph.joins.append(Join())
        if transition.kind in (TransitionKind.DIP_TO_BLACK, TransitionKind.DIP_TO_WHITE):
            color = "black" if transition.kind is TransitionKind.DIP_TO_BLACK else "white"
            half = max(1, _frames(transition.duration_us // 2, fps))
            half_samples = round(half * SAMPLE_RATE / fps)
            fade_out[index] = Fade(min(half, frames[index]), half_samples, color)
            fade_in[index + 1] = Fade(min(half, frames[index + 1]), half_samples, color)
        elif transition.kind is TransitionKind.FLASH:
            length = max(1, _frames(transition.duration_us, fps))
            fade_in[index + 1] = Fade(min(length, frames[index + 1]), 0, "white", audio=False)

    def handle_samples(handle_frames: int) -> int:
        return round(handle_frames * SAMPLE_RATE / fps)

    for index, segment in enumerate(segments):
        clip = project.clip(segment.clip_id)
        head_us = tail_us = 0
        if head_frames[index]:
            head_us = handle_us(segments[index - 1].transition_out, segment.speed)
        if tail_frames[index]:
            tail_us = handle_us(segment.transition_out, segment.speed)
        start_us = segment.in_us - head_us
        input_index = graph.add_input(
            InputNode(
                options.media_path(clip),
                start_s=start_us / US,
                duration_s=(segment.out_us + tail_us - start_us) / US,
            )
        )
        graph.items.append(
            ClipNode(
                input_index=input_index,
                frames=frames[index] + head_frames[index] + tail_frames[index],
                samples=samples[index]
                + handle_samples(head_frames[index])
                + handle_samples(tail_frames[index]),
                speed=segment.speed,
                has_audio=clip.has_audio,
                gain_db=project.settings.game_audio_db + segment.game_audio_db,
                reframe=plan_reframe(
                    clip, segment, preset, start_us, options.follow_paths.get(clip.id)
                ),
                fade_in=fade_in[index],
                fade_out=fade_out[index],
                segment_id=segment.id,
            )
        )

    def add_text(
        text: str, style_name: str, x: float, y: float, start_s: float, end_s: float
    ) -> int:
        style = TEXT_LOOKS[project.settings.text_look].get(style_name)
        if style is None:
            raise RenderError(f"Unknown text style: {style_name}")
        size = fitted_font_size(text, style.size, graph.width, graph.height)
        primary = brand.primary_color if brand else DEFAULT_PRIMARY_COLOR

        def color(value: str | None) -> str | None:
            return ffmpeg_color(resolve_color(value, primary)) if value else None

        text_file = f"text{len(graph.texts)}.txt"
        graph.files[text_file] = text
        graph.texts.append(
            TextNode(
                text_file=text_file,
                font_file=font_file,
                size=size,
                color=color(style.color or (brand.text_color if brand else DEFAULT_TEXT_COLOR))
                or "white",
                x=x,
                y=y,
                start_s=start_s,
                end_s=end_s,
                border_width=round(style.border * size),
                border_color=style.border_color,
                box_color=color(style.box),
                shadow_color=color(style.shadow),
                shadow_offset=max(1, round(style.shadow_offset * size)) if style.shadow else 0,
                band_color=color(style.band),
                band_height=round(size * 1.8) if style.band else 0,
                align=style.align,
            )
        )
        return len(graph.texts) - 1

    segments_end_s = sum(frames[: len(segments)]) / fps
    if end_card:
        index = len(segments)
        card_s = (frames[index] + head_frames[index]) / fps
        background_input = None
        if end_card.background:
            background_input = graph.add_input(
                InputNode(Path(end_card.background), duration_s=card_s, loop_image=True)
            )
        logo_input = None
        if end_card.show_logo and brand and brand.logo_path:
            logo_input = graph.add_input(InputNode(Path(brand.logo_path)))
        texts: list[int] = []
        title = brand.game_title if brand else ""
        if logo_input is None and title:
            texts.append(add_text(title, "title", 0.5, 0.42, 0.0, card_s))
        cta = end_card.cta_text or (brand.cta_text if brand else "")
        if cta:
            texts.append(
                add_text(cta, "cta", 0.5, 0.75 if logo_input or title else 0.5, 0.0, card_s)
            )
        graph.items.append(
            EndCardNode(
                frames=frames[index] + head_frames[index],
                samples=samples[index] + handle_samples(head_frames[index]),
                background_color=ffmpeg_color(brand.background_color if brand else "#000000"),
                background_input=background_input,
                logo_input=logo_input,
                logo_width=even(graph.width * END_CARD_LOGO_WIDTH),
                text_indexes=tuple(texts),
                fade_in=fade_in[index],
            )
        )

    for overlay in timeline.texts:
        graph.overlay_texts.append(
            add_text(
                overlay.text,
                overlay.style,
                overlay.x,
                overlay.y,
                overlay.start_us / US,
                overlay.end_us / US,
            )
        )

    if timeline.show_logo and brand and brand.logo_path and segments:
        graph.logo = LogoNode(
            input_index=graph.add_input(InputNode(Path(brand.logo_path))),
            width=even(graph.width * WATERMARK_WIDTH),
            margin=round(graph.height * 0.03),
            end_s=segments_end_s,
        )

    total_samples = graph.total_samples
    for cue in timeline.music:
        start = _samples(cue.start_us)
        if start >= total_samples:
            continue
        source_out = cue.source_out_us
        if source_out is None and cue.path in options.music_durations_us:
            source_out = options.music_durations_us[cue.path]
        available = _samples(source_out - cue.source_in_us) if source_out else total_samples
        length = min(available, total_samples - start)
        graph.music.append(
            MusicNode(
                input_index=graph.add_input(
                    InputNode(
                        Path(cue.path),
                        start_s=cue.source_in_us / US,
                        duration_s=length / SAMPLE_RATE,
                    )
                ),
                delay_samples=start,
                length_samples=length,
                volume_db=cue.volume_db,
                fade_in_samples=min(_samples(cue.fade_in_us), length),
                fade_out_samples=min(_samples(cue.fade_out_us), length),
            )
        )

    if options.loudness:
        graph.loudness = Loudness(integrated=project.settings.loudness_target_lufs)
    return graph
