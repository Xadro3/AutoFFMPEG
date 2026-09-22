#!/usr/bin/env python3
"""Watch media directories and transcode audio streams with FFmpeg.

No third-party Python packages are required.  FFmpeg and ffprobe must be
available on PATH (or supplied using the command-line options).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts",
}


@dataclass(frozen=True)
class Settings:
    media_dirs: tuple[Path, ...]
    codec: str
    poll_seconds: int = 30


def load_config(path: Path) -> Settings:
    """Read a deliberately small cfg format which permits repeated keys."""
    directories: list[Path] = []
    codec: str | None = None
    poll_seconds = 30

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected key=value")
        key, value = (part.strip() for part in line.split("=", 1))
        if not value:
            raise ValueError(f"{path}:{line_number}: empty value for {key}")
        if key == "media_dir":
            directories.append(Path(value).expanduser())
        elif key == "codec":
            codec = value.lower()
        elif key == "poll_seconds":
            poll_seconds = int(value)
        else:
            raise ValueError(f"{path}:{line_number}: unknown key {key!r}")

    if not directories:
        raise ValueError("configuration needs at least one media_dir")
    if not codec:
        raise ValueError("configuration needs codec=...")
    if poll_seconds < 1:
        raise ValueError("poll_seconds must be at least 1")
    return Settings(tuple(directories), codec, poll_seconds)


def audio_codecs(path: Path, ffprobe: str) -> list[str]:
    command = [
        ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
        "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return [line.strip().lower() for line in completed.stdout.splitlines() if line.strip()]


def output_path(source: Path, codec: str) -> Path:
    """Avoid overwriting the source; choose a non-conflicting destination."""
    candidate = source.with_name(f"{source.stem}.{codec}{source.suffix}")
    number = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}.{codec}.{number}{source.suffix}")
        number += 1
    return candidate


def temporary_output_path(destination: Path) -> Path:
    """Return a temporary path that retains the destination's extension."""
    return destination.with_name(f"{destination.stem}.part{destination.suffix}")


def transcode(source: Path, destination: Path, codec: str, ffmpeg: str) -> None:
    # Keep the container extension last so FFmpeg can infer the output format.
    # For example, ``movie.aac.mkv`` is written as ``movie.aac.part.mkv``.
    temporary = temporary_output_path(destination)
    command = [
        ffmpeg, "-nostdin", "-y", "-fflags", "+genpts", "-i", str(source), "-map", "0",
        "-c", "copy", "-af", "pan=stereo|c0=FR|c1=FR", "-c:a", codec, str(temporary),
    ]
    try:
        subprocess.run(command, check=True)
        # os.replace is atomic on one filesystem; source is untouched until here.
        os.replace(temporary, destination)
        source.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def is_stable(path: Path, previous_sizes: dict[Path, int]) -> bool:
    """Require a file to have the same size on two polls before processing."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    prior_size = previous_sizes.get(path)
    previous_sizes[path] = size
    return prior_size == size


def discover(settings: Settings):
    for directory in settings.media_dirs:
        if not directory.is_dir():
            logging.warning("Media directory does not exist: %s", directory)
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and not path.name.endswith(".part"):
                yield path


def run(settings: Settings, ffmpeg: str, ffprobe: str, once: bool) -> None:
    previous_sizes: dict[Path, int] = {}
    mode = "a single scan" if once else f"continuous scans every {settings.poll_seconds} seconds"
    logging.info(
        "Starting watcher for %d media director%s (%s; target audio codec: %s)",
        len(settings.media_dirs),
        "y" if len(settings.media_dirs) == 1 else "ies",
        mode,
        settings.codec,
    )
    for directory in settings.media_dirs:
        logging.info("Watching: %s", directory)

    scan_number = 0
    while True:
        scan_number += 1
        found = set(discover(settings))
        logging.info("Scan %d: found %d eligible video file%s", scan_number, len(found), "" if len(found) == 1 else "s")
        for old_path in set(previous_sizes) - found:
            previous_sizes.pop(old_path, None)
        for source in sorted(found):
            if not is_stable(source, previous_sizes):
                logging.info("Waiting for file to finish copying: %s", source)
                continue
            try:
                logging.debug("Probing audio streams: %s", source)
                codecs = audio_codecs(source, ffprobe)
                if codecs and all(found_codec == settings.codec for found_codec in codecs):
                    logging.info("Already %s; skipping %s", settings.codec, source)
                    continue
                destination = output_path(source, settings.codec)
                logging.info("Converting %s -> %s", source, destination)
                transcode(source, destination, settings.codec, ffmpeg)
                previous_sizes.pop(source, None)
                logging.info("Finished; removed original %s", source)
            except subprocess.CalledProcessError as error:
                logging.error("FFmpeg/ffprobe failed for %s (exit %s)", source, error.returncode)
            except OSError as error:
                logging.error("Cannot process %s: %s", source, error)
        if once:
            return
        time.sleep(settings.poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to the cfg file")
    parser.add_argument("--once", action="store_true", help="Scan once instead of watching")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(load_config(args.config), args.ffmpeg, args.ffprobe, args.once)
    except (OSError, ValueError) as error:
        logging.error("%s", error)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
