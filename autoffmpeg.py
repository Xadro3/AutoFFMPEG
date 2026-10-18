#!/usr/bin/env python3
"""Watch media directories and normalize media streams with FFmpeg.

No third-party Python packages are required.  FFmpeg and ffprobe must be
available on PATH (or supplied using the command-line options).
"""

from __future__ import annotations

import argparse
import json
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

VIDEO_ENCODERS = {"h264": "libx264", "hevc": "libx265", "vp9": "libvpx-vp9"}
VIDEO_CONFIG_KEYS = {
    "video_codec", "video_encoder", "video_width", "video_height",
    "video_pixel_format", "video_frame_rate",
}


@dataclass(frozen=True)
class Settings:
    media_dirs: tuple[Path, ...]
    codec: str | None = None
    audio_channels: int | None = None
    poll_seconds: int = 30
    audio_sample_rate: int | None = None
    video_codec: str | None = None
    video_encoder: str | None = None
    video_width: int | None = None
    video_height: int | None = None
    video_pixel_format: str | None = None
    video_frame_rate: str | None = None


def load_config(path: Path) -> Settings:
    """Read a deliberately small cfg format which permits repeated keys."""
    directories: list[Path] = []
    codec: str | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    video: dict[str, str] = {}
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
        elif key == "audio_channels":
            audio_channels = int(value)
        elif key == "audio_sample_rate":
            audio_sample_rate = int(value)
        elif key in VIDEO_CONFIG_KEYS:
            video[key] = value.lower()
        elif key == "poll_seconds":
            poll_seconds = int(value)
        else:
            raise ValueError(f"{path}:{line_number}: unknown key {key!r}")

    if not directories:
        raise ValueError("configuration needs at least one media_dir")
    if (codec is None) != (audio_channels is None):
        raise ValueError("audio target needs both codec=... and audio_channels=...")
    if audio_channels is not None and audio_channels < 1:
        raise ValueError("audio_channels must be at least 1")
    if audio_sample_rate is not None and audio_sample_rate < 1:
        raise ValueError("audio_sample_rate must be at least 1")
    required_video = {"video_codec", "video_width", "video_height", "video_pixel_format", "video_frame_rate"}
    if video and not required_video.issubset(video):
        missing = ", ".join(sorted(required_video - video.keys()))
        raise ValueError(f"incomplete video target; missing {missing}")
    if not codec and not video:
        raise ValueError("configuration needs an audio target, a video target, or both")
    if poll_seconds < 1:
        raise ValueError("poll_seconds must be at least 1")
    return Settings(
        tuple(directories), codec, audio_channels, poll_seconds, audio_sample_rate,
        video.get("video_codec"), video.get("video_encoder"),
        int(video["video_width"]) if video else None,
        int(video["video_height"]) if video else None,
        video.get("video_pixel_format"), video.get("video_frame_rate"),
    )


def probe_media(path: Path, ffprobe: str) -> dict:
    command = [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def audio_streams(path: Path, ffprobe: str) -> list[tuple[str, int]]:
    command = [
        ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
        "stream=codec_name,channels", "-of", "json", str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    streams = json.loads(completed.stdout).get("streams", [])
    return [
        (stream["codec_name"].lower(), int(stream["channels"]))
        for stream in streams
        if stream.get("codec_name") and stream.get("channels") is not None
    ]


def audio_matches_target(streams: list[tuple[str, int]], codec: str, channels: int) -> bool:
    """Return whether every audio stream already has the requested format."""
    return bool(streams) and all(
        stream_codec == codec and stream_channels == channels
        for stream_codec, stream_channels in streams
    )


def media_matches_target(probe: dict, settings: Settings) -> bool:
    streams = probe.get("streams", [])
    if settings.codec:
        audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if not audio or not all(
            stream.get("codec_name", "").lower() == settings.codec
            and stream.get("channels") == settings.audio_channels
            and (settings.audio_sample_rate is None or int(stream.get("sample_rate", 0)) == settings.audio_sample_rate)
            for stream in audio
        ):
            return False
    if settings.video_codec:
        video = [stream for stream in streams if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")]
        if not video or not all(
            stream.get("codec_name", "").lower() == settings.video_codec
            and stream.get("width") == settings.video_width
            and stream.get("height") == settings.video_height
            and stream.get("pix_fmt", "").lower() == settings.video_pixel_format
            and stream.get("avg_frame_rate") == settings.video_frame_rate
            for stream in video
        ):
            return False
    return True


def choose_setup_scope(answer: str) -> str:
    choices = {"1": "video", "2": "audio", "3": "both", "video": "video", "audio": "audio", "both": "both"}
    choice = choices.get(answer.strip().lower())
    if not choice:
        raise ValueError("choose video, audio, or both")
    return choice


def config_from_probe(probe: dict, media_dir: Path, scope: str) -> tuple[str, list[str]]:
    streams = probe.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video" and not item.get("disposition", {}).get("attached_pic")), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if scope in {"video", "both"} and not video:
        raise ValueError("reference file has no usable video stream")
    if scope in {"audio", "both"} and not audio:
        raise ValueError("reference file has no audio stream")

    lines = ["# Generated from a known-working media file.", f"media_dir={media_dir}", ""]
    summary: list[str] = []
    if scope in {"video", "both"}:
        required = ("codec_name", "width", "height", "pix_fmt")
        if any(video.get(field) is None for field in required):
            raise ValueError("reference video stream lacks codec, dimensions, or pixel format")
        frame_rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
        if not frame_rate or frame_rate == "0/0":
            raise ValueError("reference video stream lacks a usable frame rate")
        codec = video["codec_name"].lower()
        lines.extend([
            f"# Reference profile: {video.get('profile', 'unknown')}; field order: {video.get('field_order', 'unknown')}",
            f"video_codec={codec}",
            f"video_encoder={VIDEO_ENCODERS.get(codec, codec)}",
            f"video_width={video['width']}",
            f"video_height={video['height']}",
            f"video_pixel_format={video['pix_fmt'].lower()}",
            f"video_frame_rate={frame_rate}", "",
        ])
        summary.append(f"video: {codec}, {video['width']}x{video['height']}, {video['pix_fmt']}, {frame_rate} fps")
    if scope in {"audio", "both"}:
        required = ("codec_name", "channels", "sample_rate")
        if any(audio.get(field) is None for field in required):
            raise ValueError("reference audio stream lacks codec, channels, or sample rate")
        lines.extend([
            f"# Reference profile: {audio.get('profile', 'unknown')}; layout: {audio.get('channel_layout', 'unknown')}",
            f"codec={audio['codec_name'].lower()}",
            f"audio_channels={audio['channels']}",
            f"audio_sample_rate={audio['sample_rate']}", "",
        ])
        summary.append(f"audio: {audio['codec_name'].lower()}, {audio['channels']} channels, {audio['sample_rate']} Hz")
    lines.extend(["# Seconds between scans.", "poll_seconds=30", ""])
    return "\n".join(lines), summary


def setup(reference: Path, destination: Path, media_dir: Path, ffprobe: str, input_fn=input) -> None:
    if not reference.is_file():
        raise ValueError(f"reference file does not exist: {reference}")
    if destination.exists():
        raise ValueError(f"refusing to overwrite existing configuration: {destination}")
    print("Which settings should be taken from the reference file?")
    print("  1) Video only\n  2) Audio only\n  3) Both video and audio")
    while True:
        try:
            scope = choose_setup_scope(input_fn("Choice: "))
            break
        except ValueError as error:
            print(f"{error}. Please try again.")
    contents, summary = config_from_probe(probe_media(reference, ffprobe), media_dir, scope)
    destination.write_text(contents, encoding="utf-8")
    print(f"\nWrote {destination}")
    for item in summary:
        print(f"  {item}")
    print(f"\nStart the watcher with:\n  python3 autoffmpeg.py \"{destination}\"")


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


def transcode(source: Path, destination: Path, settings: Settings, ffmpeg: str) -> None:
    # Keep the container extension last so FFmpeg can infer the output format.
    # For example, ``movie.aac.mkv`` is written as ``movie.aac.part.mkv``.
    temporary = temporary_output_path(destination)
    command = [
        ffmpeg, "-nostdin", "-y", "-fflags", "+genpts", "-i", str(source), "-map", "0",
        "-c", "copy",
    ]
    if settings.codec:
        # FFmpeg's layout-aware downmix includes the centre dialogue channel.
        command.extend(["-c:a", settings.codec, "-ac:a", str(settings.audio_channels)])
        if settings.audio_sample_rate:
            command.extend(["-ar:a", str(settings.audio_sample_rate)])
    if settings.video_codec:
        command.extend([
            "-c:v", settings.video_encoder or settings.video_codec,
            "-vf", f"scale={settings.video_width}:{settings.video_height}",
            "-pix_fmt", settings.video_pixel_format,
            "-r:v", settings.video_frame_rate,
        ])
    command.append(str(temporary))
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
    targets = []
    if settings.video_codec:
        targets.append(f"video {settings.video_codec} {settings.video_width}x{settings.video_height} {settings.video_pixel_format} {settings.video_frame_rate} fps")
    if settings.codec:
        sample_rate = f", {settings.audio_sample_rate} Hz" if settings.audio_sample_rate else ""
        targets.append(f"audio {settings.codec}, {settings.audio_channels} channels{sample_rate}")
    logging.info("Starting watcher for %d media director%s (%s; target: %s)", len(settings.media_dirs), "y" if len(settings.media_dirs) == 1 else "ies", mode, "; ".join(targets))
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
                logging.debug("Probing media streams: %s", source)
                probe = probe_media(source, ffprobe)
                if media_matches_target(probe, settings):
                    logging.info("Already matches configured target; skipping %s", source)
                    continue
                label = settings.codec or settings.video_codec or "converted"
                destination = output_path(source, label)
                logging.info("Converting %s -> %s", source, destination)
                transcode(source, destination, settings, ffmpeg)
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
    parser.add_argument("config", nargs="?", type=Path, help="Path to the cfg file")
    parser.add_argument("-setup", "--setup", metavar="REFERENCE", type=Path, help="Create a config from a known-working media file")
    parser.add_argument("--output", type=Path, default=Path("autoffmpeg.cfg"), help="Config path for --setup (default: autoffmpeg.cfg)")
    parser.add_argument("--media-dir", type=Path, help="Directory to watch in the generated config (default: reference file's directory)")
    parser.add_argument("--once", action="store_true", help="Scan once instead of watching")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.setup:
            if args.config:
                parser.error("config cannot be used together with --setup")
            setup(args.setup, args.output, args.media_dir or args.setup.parent, args.ffprobe)
        else:
            if not args.config:
                parser.error("provide a config file or use --setup REFERENCE")
            run(load_config(args.config), args.ffmpeg, args.ffprobe, args.once)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        logging.error("%s", error)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
