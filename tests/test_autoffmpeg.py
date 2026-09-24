import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoffmpeg import load_config, output_path, temporary_output_path, transcode


class ConfigTests(unittest.TestCase):
    def test_repeated_media_directories_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "watch.cfg"
            config.write_text("media_dir=/mnt/a\nmedia_dir=/mnt/b\ncodec=AAC\n", encoding="utf-8")
            settings = load_config(config)
        self.assertEqual(settings.media_dirs, (Path("/mnt/a"), Path("/mnt/b")))
        self.assertEqual(settings.codec, "aac")

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
                transcode(source, destination, "aac", "ffmpeg")

            command = run_mock.call_args.args[0]
            self.assertEqual(command[3:6], ["-fflags", "+genpts", "-i"])
            self.assertNotIn("-af", command)
            self.assertNotIn("pan=stereo|c0=FR|c1=FR", command)
            self.assertEqual(command[command.index("-c:a") + 1], "aac")
            self.assertTrue(destination.exists())
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
