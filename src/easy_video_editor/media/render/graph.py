"""The render graph (SPEC §7.7): an intermediate, ffmpeg-independent description of a render.

`build.py` compiles an edit timeline into a `RenderGraph`; `ffmpeg.py` translates the graph into
an ffmpeg command. Times here are output frames and audio samples, which keeps every duration
exact; seconds appear only where ffmpeg needs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True)
class InputNode:
    path: Path
    start_s: float | None = None
    """Input-level seek (accurate)."""
    duration_s: float | None = None
    loop_image: bool = False
    """Repeat a still image (at the output frame rate) for ``duration_s``."""


class ReframeKind(StrEnum):
    SCALE = "scale"
    """Same aspect ratio as the output (or unknown): scale to fit, pad if needed."""
    CROP = "crop"
    BLUR = "blur"
    """Whole frame fitted in the middle over a blurred, zoomed copy."""


@dataclass(frozen=True)
class Reframe:
    kind: ReframeKind
    crop_axis: str = "x"
    """For CROP: ``x`` when the source is wider than the output, ``y`` when it is taller."""
    position: float = 0.5
    """For CROP: crop offset as a fraction of the free space (0 = left/top, 1 = right/bottom)."""
    keyframes: tuple[tuple[float, float], ...] = ()
    """For a follow crop: (segment output seconds, position) pairs, linearly interpolated."""


@dataclass(frozen=True)
class Fade:
    frames: int
    samples: int
    color: str
    """``black`` or ``white``."""
    audio: bool = True


@dataclass(frozen=True)
class ClipNode:
    """A source segment: input-level trim, then speed, reframing and exact length."""

    input_index: int
    frames: int
    samples: int
    speed: float
    has_audio: bool
    gain_db: float
    reframe: Reframe
    fade_in: Fade | None = None
    fade_out: Fade | None = None
    segment_id: str | None = None


@dataclass(frozen=True)
class EndCardNode:
    frames: int
    samples: int
    background_color: str
    background_input: int | None = None
    logo_input: int | None = None
    logo_width: int = 0
    text_indexes: tuple[int, ...] = ()
    """Indexes into `RenderGraph.texts` of texts drawn on the card itself."""
    fade_in: Fade | None = None


@dataclass(frozen=True)
class Join:
    crossfade_frames: int = 0
    """0 means a plain concatenation."""
    crossfade_samples: int = 0


@dataclass(frozen=True)
class TextNode:
    text_file: str
    """File name in the render work folder (text is never escaped into the filter graph)."""
    font_file: str
    size: int
    color: str
    x: float
    y: float
    """Centre of the text as fractions of the frame."""
    start_s: float = 0.0
    end_s: float = 0.0
    border_width: int = 0
    border_color: str = "black"
    box_color: str | None = None
    fade_s: float = 0.2


@dataclass(frozen=True)
class LogoNode:
    input_index: int
    width: int
    margin: int
    end_s: float
    """The watermark is shown until the end card starts."""


@dataclass(frozen=True)
class MusicNode:
    input_index: int
    delay_samples: int
    length_samples: int
    volume_db: float
    fade_in_samples: int
    fade_out_samples: int


@dataclass(frozen=True)
class Loudness:
    integrated: float
    true_peak: float = -1.5
    lra: float = 11.0
    measured: dict[str, str] | None = None
    """Values from a first measuring pass (two-pass loudnorm)."""


@dataclass
class RenderGraph:
    width: int
    height: int
    fps: int
    sample_rate: int
    inputs: list[InputNode] = field(default_factory=list)
    items: list[ClipNode | EndCardNode] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)
    """``joins[k]`` joins ``items[k]`` and ``items[k + 1]``."""
    texts: list[TextNode] = field(default_factory=list)
    overlay_texts: list[int] = field(default_factory=list)
    """Indexes of texts drawn over the whole output (the others belong to the end card)."""
    logo: LogoNode | None = None
    music: list[MusicNode] = field(default_factory=list)
    duck_game_audio: bool = True
    loudness: Loudness | None = None
    files: dict[str, str | Path] = field(default_factory=dict)
    """Auxiliary files for the work folder: name → text content or path of a file to copy."""

    @property
    def total_frames(self) -> int:
        return sum(item.frames for item in self.items) - sum(
            join.crossfade_frames for join in self.joins
        )

    @property
    def total_samples(self) -> int:
        return sum(item.samples for item in self.items) - sum(
            join.crossfade_samples for join in self.joins
        )

    @property
    def duration_s(self) -> float:
        return self.total_frames / self.fps

    def add_input(self, node: InputNode) -> int:
        self.inputs.append(node)
        return len(self.inputs) - 1
