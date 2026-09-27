import sys
import tempfile
import time
import unittest
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from images import ImageStore


class ImageStoreTests(unittest.TestCase):
    def test_ack_and_close_remove_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            with ImageStore(Path(temporary)) as images:
                path = images.offer(b"image")
                self.assertTrue(path.exists())
                images.ack()
                self.assertFalse(path.exists())
                path = images.offer(b"other")
                run = images.run
            self.assertFalse(path.exists())
            self.assertFalse(run.exists())

    def test_sweeps_only_old_owned_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale = root / "run-old"
            stale.mkdir()
            crashed = root / "run-99999999-crashed"
            crashed.mkdir()
            other = root / "keep-me"
            other.mkdir()
            old = time.time() - 100
            import os
            os.utime(stale, (old, old))
            with ImageStore(root, stale_after_seconds=10):
                self.assertFalse(stale.exists())
                self.assertFalse(crashed.exists())
                self.assertTrue(other.exists())

    def test_existing_root_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "images"
            root.mkdir(mode=0o755)
            with ImageStore(root):
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
