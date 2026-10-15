"""Translate a `RenderGraph` into an ffmpeg filter graph and command line.

The filter graph is written to a script file and ffmpeg runs inside the render work folder, so
fonts and texts are referenced by plain relative file names: nothing user-provided is ever
escaped into the filter graph.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path

from easy_video_editor.media.render.graph import (
    ClipNode,
    EndCardNode,
    Fade,
    Reframe,
    ReframeKind,
    RenderGraph,
    TextNode,
)

VIDEO_OUT, AUDIO_OUT = "vout", "aout"
AUDIO_FORMAT = "aformat=sample_fmts=fltp:channel_layouts=stereo"
DUCKING = "sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400"


def num(value: float) -> str:
    """Compact, locale-independent decimal."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True)
class EncodeSettings:
    video_codec: str = "libx264"
    preset: str = "medium"
    crf: int = 18
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"

    @classmethod
    def draft(cls) -> EncodeSettings:
        return cls(preset="ultrafast", crf=30, audio_bitrate="128k")

    def args(self) -> list[str]:
        args = ["-c:v", self.video_codec]
        if self.video_codec == "libx264":
            args += ["-preset", self.preset, "-crf", str(self.crf), "-profile:v", "high"]
        args += ["-pix_fmt", "yuv420p", "-c:a", self.audio_codec, "-b:a", self.audio_bitrate]
        return [*args, "-movflags", "+faststart"]


# --- Video -------------------------------------------------------------------------------------


def _position_expr(reframe: Reframe) -> str:
    keyframes = reframe.keyframes
    if not keyframes:
        return num(reframe.position)
    expr = num(keyframes[-1][1])
    for (t0, p0), (t1, p1) in reversed(list(itertools.pairwise(keyframes))):
        span = max(t1 - t0, 1e-6)
        segment = f"{num(p0)}+({num(p1 - p0)})*(t-{num(t0)})/{num(span)}"
        expr = f"if(lt(t,{num(t1)}),{segment},{expr})"
    return f"if(lt(t,{num(keyframes[0][0])}),{num(keyframes[0][1])},{expr})"


def _reframe_filters(reframe: Reframe, graph: RenderGraph, label: str) -> str:
    """Filters from a source frame to an output-sized frame. May contain ``;``-separated chains."""
    w, h = graph.width, graph.height
    if reframe.kind is ReframeKind.CROP:
        position = _position_expr(reframe)
        if reframe.crop_axis == "x":
            crop = f"crop=w='trunc(ih*{w}/{h}/2)*2':h=ih:x='(iw-ow)*({position})':y=0"
        else:
            crop = f"crop=w=iw:h='trunc(iw*{h}/{w}/2)*2':x=0:y='(ih-oh)*({position})'"
        return f"{crop},scale={w}:{h}"
    if reframe.kind is ReframeKind.BLUR:
        radius = max(2, round(h / 50))
        return (
            f"split=2[{label}bg][{label}fg];"
            f"[{label}bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur={radius}:2[{label}bgb];"
            f"[{label}fg]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2"
            f"[{label}fgs];"
            f"[{label}bgb][{label}fgs]overlay=(W-w)/2:(H-h)/2"
        )
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def _exact_length(frames: int, fps: int) -> str:
    """Cut or extend (by repeating the last frame) to exactly ``frames`` frames."""
    return (
        f"fps={fps},trim=end_frame={frames},"
        f"tpad=stop_mode=clone:stop_duration={num(frames / fps + 1)},"
        f"trim=end_frame={frames},setpts=PTS-STARTPTS,settb=1/{fps},format=yuv420p,setsar=1"
    )


def _video_fades(fade_in: Fade | None, fade_out: Fade | None, frames: int) -> str:
    filters = ""
    if fade_in:
        filters += f",fade=t=in:s=0:n={fade_in.frames}:color={fade_in.color}"
    if fade_out:
        filters += (
            f",fade=t=out:s={frames - fade_out.frames}:n={fade_out.frames}:color={fade_out.color}"
        )
    return filters


def _drawtext(text: TextNode, overlay: bool) -> str:
    options = [
        f"fontfile={text.font_file}",
        f"textfile={text.text_file}",
        "expansion=none",
        f"fontsize={text.size}",
        f"fontcolor={text.color}",
        f"x='w*{num(text.x)}-text_w/2'",
        f"y='h*{num(text.y)}-text_h/2'",
    ]
    if text.border_width:
        options += [f"borderw={text.border_width}", f"bordercolor={text.border_color}"]
    if text.box_color:
        options += ["box=1", f"boxcolor={text.box_color}", f"boxborderw={max(4, text.size // 4)}"]
    start, end, fade = num(text.start_s), num(text.end_s), num(text.fade_s)
    if overlay:
        options.append(f"enable='between(t,{start},{end})'")
    options.append(
        f"alpha='if(lt(t,{start}+{fade}),max(0,(t-{start})/{fade}),"
        f"if(gt(t,{end}-{fade}),max(0,({end}-t)/{fade}),1))'"
    )
    return "drawtext=" + ":".join(options)


def _clip_video(node: ClipNode, index: int, graph: RenderGraph) -> str:
    label = f"c{index}"
    reframe = _reframe_filters(node.reframe, graph, label)
    return (
        f"[{node.input_index}:v]setpts=(PTS-STARTPTS)/{num(node.speed)},{reframe},"
        f"{_exact_length(node.frames, graph.fps)}"
        f"{_video_fades(node.fade_in, node.fade_out, node.frames)}[v{index}]"
    )


def _end_card_video(node: EndCardNode, index: int, graph: RenderGraph) -> str:
    w, h = graph.width, graph.height
    label = f"e{index}"
    if node.background_input is not None:
        chain = (
            f"[{node.background_input}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},{_exact_length(node.frames, graph.fps)}"
        )
    else:
        chain = (
            f"color=c={node.background_color}:s={w}x{h}:r={graph.fps},"
            f"{_exact_length(node.frames, graph.fps)}"
        )
    parts = []
    if node.logo_input is not None:
        parts.append(f"{chain}[{label}bg]")
        parts.append(f"[{node.logo_input}:v]scale={node.logo_width}:-2[{label}logo]")
        chain = f"[{label}bg][{label}logo]overlay=(W-w)/2:(H-h)/2-H*0.08,format=yuv420p"
    for text_index in node.text_indexes:
        chain += "," + _drawtext(graph.texts[text_index], overlay=False)
    parts.append(f"{chain}{_video_fades(node.fade_in, None, node.frames)}[v{index}]")
    return ";".join(parts)


# --- Audio -------------------------------------------------------------------------------------


def atempo_chain(speed: float) -> list[str]:
    """``atempo`` accepts 0.5 to 2.0 per instance; chain instances for other speeds."""
    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-9:
        filters.append(f"atempo={num(remaining)}")
    return filters


def _audio_fades(fade_in: Fade | None, fade_out: Fade | None, samples: int) -> str:
    filters = ""
    if fade_in and fade_in.audio and fade_in.samples:
        filters += f",afade=t=in:ss=0:ns={fade_in.samples}"
    if fade_out and fade_out.audio and fade_out.samples:
        filters += f",afade=t=out:ss={samples - fade_out.samples}:ns={fade_out.samples}"
    return filters


def _silence(samples: int, sample_rate: int) -> str:
    return f"anullsrc=r={sample_rate}:cl=stereo,{AUDIO_FORMAT},atrim=end_sample={samples}"


def _clip_audio(node: ClipNode, index: int, graph: RenderGraph) -> str:
    fades = _audio_fades(node.fade_in, node.fade_out, node.samples)
    if not node.has_audio:
        return f"{_silence(node.samples, graph.sample_rate)}{fades}[a{index}]"
    filters = [
        "asetpts=PTS-STARTPTS",
        *atempo_chain(node.speed),
        f"aresample={graph.sample_rate}",
        AUDIO_FORMAT,
        f"volume={num(node.gain_db)}dB",
        "apad",
        f"atrim=end_sample={node.samples}",
        "asetpts=PTS-STARTPTS",
    ]
    return f"[{node.input_index}:a]{','.join(filters)}{fades}[a{index}]"


def _audio_mix(graph: RenderGraph, game: str, statements: list[str]) -> str:
    """Music, ducking and loudness normalisation. Returns the label of the final audio."""
    total = graph.total_samples
    music_labels = []
    for number, cue in enumerate(graph.music):
        filters = [
            f"aresample={graph.sample_rate}",
            AUDIO_FORMAT,
            f"volume={num(cue.volume_db)}dB",
        ]
        if cue.fade_in_samples:
            filters.append(f"afade=t=in:ss=0:ns={cue.fade_in_samples}")
        if cue.fade_out_samples:
            filters.append(
                f"afade=t=out:ss={cue.length_samples - cue.fade_out_samples}"
                f":ns={cue.fade_out_samples}"
            )
        if cue.delay_samples:
            filters.append(f"adelay=delays={cue.delay_samples}S:all=1")
        filters += ["apad", f"atrim=end_sample={total}", "asetpts=PTS-STARTPTS"]
        statements.append(f"[{cue.input_index}:a]{','.join(filters)}[mu{number}]")
        music_labels.append(f"mu{number}")

    current = game
    if music_labels:
        if len(music_labels) > 1:
            inputs = "".join(f"[{label}]" for label in music_labels)
            statements.append(
                f"{inputs}amix=inputs={len(music_labels)}:normalize=0:duration=longest[music]"
            )
            music = "music"
        else:
            music = music_labels[0]
        if graph.duck_game_audio:
            statements.append(f"[{music}]asplit=2[mus][msc]")
            statements.append(f"[{game}][msc]{DUCKING}[gduck]")
            game, music = "gduck", "mus"
        statements.append(f"[{game}][{music}]amix=inputs=2:normalize=0:duration=first[amix]")
        current = "amix"

    loudness = graph.loudness
    filters = []
    if loudness is not None:
        options = [f"I={num(loudness.integrated)}", f"TP={num(loudness.true_peak)}",
                   f"LRA={num(loudness.lra)}"]  # fmt: skip
        if loudness.measured:
            options += [f"{key}={value}" for key, value in loudness.measured.items()]
            options.append("linear=true")
        options.append("print_format=json")
        filters += [f"loudnorm={':'.join(options)}", f"aresample={graph.sample_rate}"]
    filters += [f"atrim=end_sample={total}", "asetpts=PTS-STARTPTS"]
    statements.append(f"[{current}]{','.join(filters)}[{AUDIO_OUT}]")
    return AUDIO_OUT


# --- Whole graph -------------------------------------------------------------------------------


def filter_script(graph: RenderGraph, video: bool = True) -> str:
    """The ``-filter_complex`` graph, one statement per line. ``video=False`` builds audio only
    (used by the loudness measuring pass)."""
    if not graph.items:
        raise ValueError("Cannot render an empty graph.")
    statements: list[str] = []
    for index, item in enumerate(graph.items):
        if isinstance(item, ClipNode):
            if video:
                statements.append(_clip_video(item, index, graph))
            statements.append(_clip_audio(item, index, graph))
        else:
            if video:
                statements.append(_end_card_video(item, index, graph))
            fades = _audio_fades(item.fade_in, None, item.samples)
            statements.append(f"{_silence(item.samples, graph.sample_rate)}{fades}[a{index}]")

    video_label, audio_label = "v0", "a0"
    offset = graph.items[0].frames
    for index, join in enumerate(graph.joins, start=1):
        if join.crossfade_frames:
            offset -= join.crossfade_frames
            if video:
                statements.append(
                    f"[{video_label}][v{index}]xfade=transition=fade"
                    f":duration={num(join.crossfade_frames / graph.fps)}"
                    f":offset={num(offset / graph.fps)}[vj{index}]"
                )
            statements.append(
                f"[{audio_label}][a{index}]acrossfade=ns={join.crossfade_samples}"
                f":c1=tri:c2=tri[aj{index}]"
            )
        else:
            if video:
                statements.append(
                    f"[{video_label}][v{index}]concat=n=2:v=1:a=0,settb=1/{graph.fps}[vj{index}]"
                )
            statements.append(f"[{audio_label}][a{index}]concat=n=2:v=0:a=1[aj{index}]")
        offset += graph.items[index].frames
        video_label, audio_label = f"vj{index}", f"aj{index}"

    if video:
        chain = [_drawtext(graph.texts[i], overlay=True) for i in graph.overlay_texts]
        if graph.logo is not None:
            logo = graph.logo
            statements.append(f"[{logo.input_index}:v]scale={logo.width}:-2[wm]")
            texts = f"{','.join(chain)}" if chain else "null"
            statements.append(f"[{video_label}]{texts}[vtxt]")
            statements.append(
                f"[vtxt][wm]overlay=W-w-{logo.margin}:{logo.margin}"
                f":enable='lt(t,{num(logo.end_s)})',format=yuv420p[{VIDEO_OUT}]"
            )
        else:
            statements.append(f"[{video_label}]{','.join(chain) or 'null'}[{VIDEO_OUT}]")
    _audio_mix(graph, audio_label, statements)
    return ";\n".join(statements) + "\n"


def input_args(graph: RenderGraph) -> list[str]:
    args: list[str] = []
    for node in graph.inputs:
        if node.loop_image:
            args += ["-loop", "1", "-framerate", str(graph.fps)]
        if node.start_s is not None:
            args += ["-ss", num(node.start_s)]
        if node.duration_s is not None:
            args += ["-t", num(node.duration_s)]
        args += ["-i", str(node.path)]
    return args


def filter_script_option(ffmpeg_major: int) -> str:
    """ffmpeg 7 replaced ``-filter_complex_script`` with ``-/filter_complex``."""
    return "-/filter_complex" if ffmpeg_major >= 7 else "-filter_complex_script"


def render_args(
    graph: RenderGraph,
    ffmpeg: Path,
    script_name: str,
    output: Path,
    encode: EncodeSettings,
    ffmpeg_major: int,
) -> list[str]:
    return [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y",
        *input_args(graph),
        filter_script_option(ffmpeg_major), script_name,
        "-map", f"[{VIDEO_OUT}]", "-map", f"[{AUDIO_OUT}]",
        "-r", str(graph.fps), *encode.args(), "-ar", str(graph.sample_rate),
        "-progress", "pipe:1", "-nostats", "-f", "mp4", str(output),
    ]  # fmt: skip


def measure_args(
    graph: RenderGraph, ffmpeg: Path, script_name: str, ffmpeg_major: int
) -> list[str]:
    """Audio-only pass that prints loudnorm measurements (JSON) on stderr."""
    return [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y",
        *input_args(graph),
        filter_script_option(ffmpeg_major), script_name,
        "-map", f"[{AUDIO_OUT}]", "-progress", "pipe:1", "-nostats", "-f", "null", "-",
    ]  # fmt: skip
