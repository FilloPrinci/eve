"""A small, fully specified project used by the render unit tests."""

from pathlib import Path

from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.commands import (
    AddClip,
    AddSegment,
    AddText,
    CreateTimeline,
    CreateZone,
    SetEndCard,
    SetMusic,
    SetOutputPresets,
    SetTransition,
)
from easy_video_editor.core.model import (
    EndCard,
    FrameRate,
    MusicCue,
    Origin,
    OutputPreset,
    Project,
    ReframeMode,
    Transition,
    TransitionKind,
    ZoneKind,
)
from easy_video_editor.core.session import ProjectSession

S = 1_000_000
MEDIA = Path(Path.cwd().anchor) / "media"
U = Origin.USER

PRESETS = [
    OutputPreset(id="wide", name="Wide", width=320, height=180, fps=25),
    OutputPreset(
        id="tall", name="Tall", width=180, height=320, fps=25,
        reframe_mode=ReframeMode.CENTER_CROP,
    ),
    OutputPreset(
        id="feed", name="Feed", width=144, height=180, fps=25,
        reframe_mode=ReframeMode.BLURRED_BACKGROUND,
    ),
]  # fmt: skip


def counter() -> "object":
    numbers = iter(range(1, 1000))
    return lambda prefix: f"{prefix}_{next(numbers)}"


def build_project(
    transitions: tuple[Transition, Transition, Transition] | None = None,
    music: bool = True,
    end_card: bool = True,
    text: bool = True,
    media: Path = MEDIA,
) -> Project:
    """Clip ``clip`` (12 s, 320x180, 25 fps) with a red zone 4-6 s and three segments per timeline:
    1-3.5 s, 6.5-9 s at 2x, 10-11 s."""
    session = ProjectSession(Project(name="Golden"), id_factory=counter())  # type: ignore[arg-type]
    session.execute(
        AddClip(path=str(media / "coded.mp4"), duration_us=12 * S, clip_id="clip",
                frame_rate=FrameRate(num=25), width=320, height=180),
        U,
    )  # fmt: skip
    session.execute(SetOutputPresets(presets=PRESETS, enabled_preset_ids=["wide", "tall"]), U)
    session.execute(
        CreateZone(kind=ZoneKind.EXCLUDE, clip_id="clip", start_us=4 * S, end_us=6 * S), U
    )
    if transitions is None:
        transitions = (
            Transition(kind=TransitionKind.CROSSFADE, duration_us=400_000),
            Transition(kind=TransitionKind.DIP_TO_BLACK, duration_us=400_000),
            Transition(kind=TransitionKind.CROSSFADE, duration_us=400_000),
        )
    for aspect in ("16:9", "9:16", "4:5"):
        tl = f"tl_{aspect.replace(':', 'x')}"
        session.execute(CreateTimeline(aspect=aspect, timeline_id=tl), U)
        for number, (start, end, speed) in enumerate(
            [(1 * S, 3_500_000, 1.0), (6_500_000, 9 * S, 2.0), (10 * S, 11 * S, 1.0)]
        ):
            session.execute(
                AddSegment(timeline_id=tl, clip_id="clip", in_us=start, out_us=end, speed=speed,
                           segment_id=f"{tl}_s{number}"),
                U,
            )  # fmt: skip
        for number, transition in enumerate(transitions):
            session.execute(
                SetTransition(timeline_id=tl, segment_id=f"{tl}_s{number}", transition=transition),
                U,
            )
        if text:
            session.execute(
                AddText(timeline_id=tl, text="V-Core", start_us=0, end_us=2 * S, text_id=f"{tl}_t"),
                U,
            )
        session.execute(
            SetEndCard(timeline_id=tl, end_card=EndCard(enabled=end_card, duration_us=2 * S,
                                                         cta_text="Wishlist now")),
            U,
        )  # fmt: skip
        if music:
            cue = MusicCue(id=f"{tl}_m", path=str(media / "music.mp3"), fade_in_us=S,
                           fade_out_us=S, volume_db=-6)  # fmt: skip
            session.execute(SetMusic(timeline_id=tl, cues=[cue]), U)
    return session.project


def brand(media: Path = MEDIA) -> BrandKit:
    return BrandKit(
        id="brand", name="V-Core", logo_path=str(media / "logo.png"), game_title="V-CORE",
        cta_text="Play now",
    )  # fmt: skip


BRAND = brand()
