# User guide

EVE is in early development. This guide grows as features land.

## Running EVE from source

    uv sync
    uv run eve --version
    uv run eve-gui

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

Add `-v` to any command to see detailed logs. Logs are also written to `eve.log` in your user log
folder (shown after errors).
