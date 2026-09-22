# AutoFFMPEG

`autoffmpeg.py` polls one or more media directories. When it finds a stable
video whose audio streams are not already in the configured codec, it copies
the video/other streams and re-encodes all audio streams with FFmpeg.

The output is placed beside the source as `movie.aac.mkv` (with a number added
if necessary). The original is deleted **only after** FFmpeg succeeds and the
output is moved into place. Videos with all audio streams already using the
configured codec are skipped.

## Setup

1. Install `ffmpeg` (which also supplies `ffprobe`) and make both commands
   available on `PATH`.
2. Copy `autoffmpeg.cfg.example` to `autoffmpeg.cfg` and adjust its paths.
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
