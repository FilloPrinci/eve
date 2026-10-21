# User guide

This guide covers EVE 0.1.

## Installing a release

Download the file for your system from the
[Releases page](https://github.com/FilloPrinci/eve/releases) and check it against `SHA256SUMS`.

- **Windows**: unzip `EVE-<version>-windows-x64.zip` and start `EVE\eve-gui.exe`. Windows may warn
  that the app is from an unknown publisher (the builds are not code-signed yet): choose *More info →
  Run anyway*. The command-line program is `EVE\eve.exe`.
- **Linux (Fedora and others)**: install the player library once (`sudo dnf install mpv-libs` on
  Fedora, `sudo apt install libmpv2` on Debian/Ubuntu). Then install EVE for your user, so that it
  appears in the application menu:

      tar -xzf EVE-<version>-linux-x64.tar.gz
      cd EVE-<version>-linux-x64
      ./install.sh

  Start it from the application menu, or with `eve-gui` in a terminal; the command-line program is
  `eve`. To update, run `install.sh` from the newer version. To uninstall, run
  `~/.local/share/EVE/uninstall.sh` (projects and settings are kept).

  Or use the portable AppImage, which needs no installation:

      chmod +x EVE-<version>-x86_64.AppImage
      ./EVE-<version>-x86_64.AppImage                 # the editor
      ./EVE-<version>-x86_64.AppImage cli doctor      # the command-line program

  If the AppImage does not start, install FUSE 2 (`sudo dnf install fuse fuse-libs`).

Projects, settings and logs are kept outside the program folder, so a new version can simply replace
the old one.

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
   **Brand kits…** edits your reusable logo, game title, call to action, colours, font and music
   folder, with a preview of the end card in every format. To change the end card of this project,
   click it at the end of the edit timeline: the Inspector sets its length, call to action, logo
   and background image, with a live preview. `Del` removes it.
7. Watch the result: **Edit** plays the cut instantly; **Draft** shows a real low-resolution render
   (*Render draft*, or tick *Auto*).
8. **File → Export…** (`Ctrl+E`) validates the project and renders every selected format.

Every change is saved automatically and can be undone (`Ctrl+Z` / `Ctrl+Shift+Z`, History tab).
Press `F12` for the log console.

## Letting EVE do the work (Auto mode)

Auto mode needs no AI and works offline. In the **Agent** tab (or the **Auto** menu):

1. Mark anything that must never appear with **red** zones first.
2. **Phase 1: find candidates** analyses motion, scene cuts and audio and creates **blue** zones.
3. **Phase 2: refine highlights** picks the best shot inside each blue zone (**yellow** zones). With
   music, shot lengths follow its beat.
4. **Phase 3: assemble the edit** builds one edit timeline per output format: strongest shot first,
   rising build-up, climax last, transitions and speed according to the mood.

**Run all phases** chains the three. Zones you create or edit are locked: automation works around
them. Each run is one undo step. When you change a blue zone, its yellow zones turn *stale*
(dashed); **Refresh stale** regenerates only what is affected.

From the command line:

    eve analyze  "~/Videos/V-Core teaser.eve"
    eve refine   "~/Videos/V-Core teaser.eve"
    eve assemble "~/Videos/V-Core teaser.eve"
    eve refresh  "~/Videos/V-Core teaser.eve"

## Letting the AI do the work (AI mode)

AI mode uses Claude to look at your footage (numeric summaries and a limited number of small frames,
never whole videos) and to make the same kind of edits you could make by hand. It respects the same
rules: it never touches red zones or anything inside them, and it never changes zones you created or
edited.

EVE uses **your own Claude subscription through Claude Code**, so no API key is needed. Set it up once:

1. Install Claude Code.
   - Fedora / Linux, in a terminal: `curl -fsSL https://claude.ai/install.sh | bash`
   - Windows, in PowerShell: `irm https://claude.ai/install.ps1 | iex`
2. Open a new terminal, run `claude`, and log in with your Claude account when asked. Then type
   `/exit`.
3. Check it: `eve doctor` (or **Help → System check**) shows *Claude Code (AI mode): OK*.

Then, in the **Agent** tab, choose **AI** as the mode and use the phase buttons as in Auto mode. The
activity list shows what the AI is doing; the project updates live; editing is paused until it
finishes or you press **Stop**. Each run is one undo step. Type a request such as “make the intro
shorter” and press **Ask** for a targeted change.

AI runs count against your Claude plan's usage limits. **Preferences → AI mode** sets the Claude Code
model (your plan's default unless you choose one) and the frame budget (how many frames the AI may look
at per run). If you have an Anthropic API key instead, choose *Anthropic API* there and paste the key:
it is stored in your system keyring, never in a file.

From the command line:

    eve analyze  "~/Videos/V-Core teaser.eve" --mode ai
    eve ask      "~/Videos/V-Core teaser.eve" "use more slow motion in the climax"

## Checking your system

    uv run eve doctor

Lists ffmpeg, ffprobe, libmpv, GPU encoders available in your ffmpeg build, the OS keyring and Claude Code,
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
