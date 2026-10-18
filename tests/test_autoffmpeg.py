import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoffmpeg import (
    audio_matches_target,
    audio_streams,
    config_from_probe,
    load_config,
    output_path,
    temporary_output_path,
    transcode,
    Settings,
)


class ConfigTests(unittest.TestCase):
    def test_repeated_media_directories_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "watch.cfg"
            config.write_text("media_dir=/mnt/a\nmedia_dir=/mnt/b\ncodec=AAC\naudio_channels=2\n", encoding="utf-8")
            settings = load_config(config)
        self.assertEqual(settings.media_dirs, (Path("/mnt/a"), Path("/mnt/b")))
        self.assertEqual(settings.codec, "aac")
        self.assertEqual(settings.audio_channels, 2)

    @mock.patch("autoffmpeg.subprocess.run")
    def test_audio_streams_reports_codec_and_channel_count(self, run_mock):
        run_mock.return_value.stdout = '{"streams":[{"codec_name":"aac","channels":6}]}'
        self.assertEqual(audio_streams(Path("movie.mkv"), "ffprobe"), [("aac", 6)])
        command = run_mock.call_args.args[0]
        self.assertEqual(command[command.index("-show_entries") + 1], "stream=codec_name,channels")

    def test_aac_surround_audio_does_not_match_stereo_aac_target(self):
        self.assertFalse(audio_matches_target([("aac", 6)], "aac", 2))
        self.assertTrue(audio_matches_target([("aac", 2)], "aac", 2))

    def test_config_is_generated_from_known_working_shape(self):
        probe = {"streams": [
            {"codec_type": "video", "codec_name": "h264", "profile": "Main", "width": 1024,
             "height": 512, "pix_fmt": "yuv420p", "avg_frame_rate": "30/1", "field_order": "progressive"},
            {"codec_type": "audio", "codec_name": "aac", "profile": "LC", "channels": 2,
             "sample_rate": "44100", "channel_layout": "stereo"},
        ]}
        contents, summary = config_from_probe(probe, Path("/media/movies"), "both")
        self.assertIn("video_codec=h264", contents)
        self.assertIn("video_encoder=libx264", contents)
        self.assertIn("video_width=1024", contents)
        self.assertIn("video_frame_rate=30/1", contents)
        self.assertIn("codec=aac", contents)
        self.assertIn("audio_channels=2", contents)
        self.assertIn("audio_sample_rate=44100", contents)
        self.assertEqual(len(summary), 2)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "generated.cfg"
            config.write_text(contents, encoding="utf-8")
            settings = load_config(config)
        self.assertEqual(settings.video_encoder, "libx264")
        self.assertEqual(settings.video_width, 1024)
        self.assertEqual(settings.audio_sample_rate, 44100)

    def test_destination_never_overwrites_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "movie.mkv"
            source.touch()
            self.assertEqual(output_path(source, "aac").name, "movie.aac.mkv")

    def test_temporary_output_keeps_the_container_extension(self):
        destination = Path("/mnt/a/movie.aac.mkv")
        self.assertEqual(temporary_output_path(destination).name, "movie.aac.part.mkv")

    def test_transcode_regenerates_timestamps_without_remapping_audio_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "movie.mkv"
            destination = Path(directory) / "movie.aac.mkv"
            temporary = temporary_output_path(destination)
            source.touch()

            def fake_run(command, check):
                temporary.touch()

            with mock.patch("autoffmpeg.subprocess.run", side_effect=fake_run) as run_mock:
                transcode(source, destination, Settings((Path(directory),), "aac", 2), "ffmpeg")

            command = run_mock.call_args.args[0]
            self.assertEqual(command[3:6], ["-fflags", "+genpts", "-i"])
            self.assertNotIn("-af", command)
            self.assertNotIn("pan=stereo|c0=FR|c1=FR", command)
            self.assertEqual(command[command.index("-c:a") + 1], "aac")
            self.assertEqual(command[command.index("-ac:a") + 1], "2")
            self.assertTrue(destination.exists())
            self.assertFalse(source.exists())

    def test_transcode_applies_generated_video_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "movie.mkv"
            destination = Path(directory) / "movie.h264.mkv"
            temporary = temporary_output_path(destination)
            source.touch()
            settings = Settings(
                (Path(directory),), video_codec="h264", video_encoder="libx264",
                video_width=1024, video_height=512, video_pixel_format="yuv420p",
                video_frame_rate="30/1",
            )

            def fake_run(command, check):
                temporary.touch()

            with mock.patch("autoffmpeg.subprocess.run", side_effect=fake_run) as run_mock:
                transcode(source, destination, settings, "ffmpeg")

            command = run_mock.call_args.args[0]
            self.assertEqual(command[command.index("-c:v") + 1], "libx264")
            self.assertEqual(command[command.index("-vf") + 1], "scale=1024:512")
            self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
            self.assertEqual(command[command.index("-r:v") + 1], "30/1")
            self.assertNotIn("-c:a", command)


if __name__ == "__main__":
    unittest.main()
