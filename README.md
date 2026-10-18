# AutoFFMPEG

`autoffmpeg.py` polls one or more media directories and normalizes their video,
audio, or both to a known-good media shape. Unselected stream types and
subtitles are copied unchanged.

The output is placed beside the source as `movie.aac.mkv` (with a number added
if necessary). The original is deleted **only after** FFmpeg succeeds and the
output is moved into place. Files already matching every configured target are
skipped. For audio downmixing, FFmpeg's standard layout-aware downmix is used,
so a 5.1 centre channel (normally dialogue) contributes to stereo output.

## Generate a configuration from a working file

Use a file that already plays correctly on the target device:

```bash
python3 autoffmpeg.py --setup "file.mkv"
```

The setup asks whether to capture video settings, audio settings, or both. It
uses `ffprobe` to extract enforceable compatibility fields such as codecs,
dimensions, pixel format, frame rate, channel count, and sample rate, then
writes `autoffmpeg.cfg` and prints the command for starting the watcher.

By default, the reference file's directory becomes the watched directory. Both
paths can be overridden:

```bash
python3 autoffmpeg.py --setup "known-good.mkv" --media-dir "/media/incoming" --output tv.cfg
```

Setup refuses to overwrite an existing configuration.

## Setup

1. Install `ffmpeg` (which also supplies `ffprobe`) and make both commands
   available on `PATH`.
2. Generate a configuration as above, or copy `autoffmpeg.cfg.example` and
   adjust its paths for the stereo AAC audio-only preset.
3. First test a single scan:

   ```bash
   python3 autoffmpeg.py autoffmpeg.cfg --once
   ```

4. Start the watcher:

   ```bash
   python3 autoffmpeg.py autoffmpeg.cfg
   ```

The configuration intentionally accepts repeated `media_dir=` lines, unlike
standard INI parsers. Directories are scanned recursively. Supported source
extensions are MKV, MP4, M4V, AVI, MOV, WMV, WebM, and TS.

For a persistent Linux service, run the watcher under systemd or another
supervisor after verifying the `--once` scan.
