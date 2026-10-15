# User guide

EVE is in early development. This guide grows as features land.

## Running EVE from source

    uv sync
    uv run eve --version
    uv run eve-gui

## Making a trailer by hand (GUI)

1. **File → New project…**, choose a name and a folder. EVE creates `<name>.eve`.
2. **Import** clips (button in the Media panel, drag and drop, or `Ctrl+I`). Proxies, thumbnails and
   waveforms are generated in the background (see the *Jobs* tab).
3. Select a clip. In the source view, drag on a lane to create a zone:
   - **red (Exclude)**: never used, by you or by the AI;
   - **blue (Candidates)**: interesting regions;
   - **yellow (Highlights)**: the moments to use.
   Drag a zone to move it, drag its edges to resize it, `Del` deletes it. While playing, `I`/`O` set
   marks and `R`/`B`/`Y` create a zone from them. A red zone trims any blue/yellow zone it covers.
4. **Timeline → New timeline…** for an aspect ratio (16:9, 9:16, 4:5). Select a yellow zone and click
   *Add to edit timeline* in the Inspector (or press `A` to add the marked range).
5. In the edit timeline, drag segments to reorder them and drag their edges to trim them. Select a
   segment to set speed, transition and reframing in the Inspector. `T` adds a text.
6. In **Settings**: output formats, text look (A classic, B band, C bold), brand kit, music, end card.
7. Watch the result: **Edit** plays the cut instantly; **Draft** shows a real low-resolution render
   (*Render draft*, or tick *Auto*).
8. **File → Export…** (`Ctrl+E`) validates the project and renders every selected format.

Every change is saved automatically and can be undone (`Ctrl+Z` / `Ctrl+Shift+Z`, History tab).
Press `F12` for the log console.

## Checking your system

    uv run eve doctor

Lists ffmpeg, ffprobe, libmpv, GPU encoders available in your ffmpeg build and the OS keyring,
with a command to fix anything that is missing.

## Working from the command line

    eve new ~/Videos --name "V-Core teaser"          # creates ~/Videos/V-Core teaser.eve
    eve import "~/Videos/V-Core teaser.eve" lap1.mkv lap2.mkv

`import` probes each clip, records its fingerprint and generates a low-resolution proxy, a thumbnail
strip and a waveform in the project folder. Use `--no-media` to skip generation. Clips already in the
project are skipped. Press Ctrl+C to cancel.

If you move or rename source files, find them again by content:

    eve relink "~/Videos/V-Core teaser.eve" --search ~/Videos/archive

Check that a project can be exported, then render every enabled output preset:

    eve validate "~/Videos/V-Core teaser.eve"
    eve render "~/Videos/V-Core teaser.eve"                 # final quality
    eve render "~/Videos/V-Core teaser.eve" --draft         # fast, low resolution, from proxies
    eve render "~/Videos/V-Core teaser.eve" --preset shorts --output ~/Desktop

Renders go to the project's `exports` folder. Export is refused if any output frame would come from
an excluded (red) range, if a source file is missing or if the result is longer than the preset allows.

Add `-v` to any command to see detailed logs. Logs are also written to `eve.log` in your user log
folder (shown after errors).
