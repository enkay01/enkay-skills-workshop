import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mac import MacDesktop, group_words, text_regions


class FakeApp:
    def localizedName(self):
        return "Steam"

    def processIdentifier(self):
        return 42


class FakeWorkspace:
    @staticmethod
    def sharedWorkspace():
        return FakeWorkspace()

    def frontmostApplication(self):
        return FakeApp()


class FakePyAutoGUI:
    def screenshot(self):
        return Image.new("RGB", (100, 100), "white")

    def size(self):
        return (100, 100)


class MacTests(unittest.TestCase):
    def test_uppercase_header_items_split_even_when_vision_boxes_touch(self):
        words = [("LIBRARY", 220, 335, 70, 90), ("COMMUNITY", 337, 463, 70, 90)]
        self.assertEqual([item[0] for item in text_regions("LIBRARY COMMUNITY", words)],
                         ["LIBRARY", "COMMUNITY"])

    def test_ocr_line_separates_distant_navigation_items(self):
        groups = group_words([
            ("LIBRARY", 200, 250, 50, 70),
            ("COMMUNITY", 290, 370, 50, 70),
            ("Football", 400, 460, 50, 70),
            ("Manager", 467, 530, 50, 70),
        ])
        self.assertEqual([group[0] for group in groups], ["LIBRARY", "COMMUNITY", "Football Manager"])

    def test_steam_helper_menu_does_not_hide_main_window(self):
        class HelperApp(FakeApp):
            def localizedName(self):
                return "Steam Helper"

        class HelperWorkspace(FakeWorkspace):
            def frontmostApplication(self):
                return HelperApp()

        appkit = types.SimpleNamespace(NSWorkspace=HelperWorkspace)
        quartz = types.SimpleNamespace(
            kCGWindowListOptionOnScreenOnly=1,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowLayer="layer",
            kCGWindowBounds="bounds",
            CGWindowListCopyWindowInfo=lambda *_: [
                {"pid": 42, "layer": 0, "bounds": {"X": 10, "Y": 10, "Width": 20, "Height": 20}},
                {"pid": 42, "layer": 0, "bounds": {"X": 5, "Y": 5, "Width": 90, "Height": 90}},
            ],
        )
        desktop = MacDesktop.__new__(MacDesktop)
        desktop.pyautogui = FakePyAutoGUI()
        with patch.dict(sys.modules, {"AppKit": appkit, "Quartz": quartz}):
            name, data = desktop.capture()
        picture = Image.open(io.BytesIO(data))
        self.assertEqual(name, "Steam")
        self.assertEqual(picture.getpixel((50, 50)), (255, 255, 255))

    def test_capture_masks_background_window(self):
        appkit = types.SimpleNamespace(NSWorkspace=FakeWorkspace)
        quartz = types.SimpleNamespace(
            kCGWindowListOptionOnScreenOnly=1,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowLayer="layer",
            kCGWindowBounds="bounds",
            CGWindowListCopyWindowInfo=lambda *_: [
                {"pid": 99, "layer": 0, "bounds": {"X": 0, "Y": 0, "Width": 100, "Height": 100}},
                {"pid": 42, "layer": 0, "bounds": {"X": 25, "Y": 25, "Width": 50, "Height": 50}},
            ],
        )
        desktop = MacDesktop.__new__(MacDesktop)
        desktop.pyautogui = FakePyAutoGUI()
        with patch.dict(sys.modules, {"AppKit": appkit, "Quartz": quartz}):
            name, data = desktop.capture()
        picture = Image.open(io.BytesIO(data))
        self.assertEqual(name, "Steam")
        self.assertEqual(picture.getpixel((10, 10)), (0, 0, 0))
        self.assertEqual(picture.getpixel((50, 50)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
