"""macOS capture, Vision OCR, and PyAutoGUI input. Imported only in live runs."""

from __future__ import annotations

from io import BytesIO
import re

from controller import Target


class MacDesktop:
    def __init__(self):
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0
        self.pyautogui = pyautogui

    def capture(self) -> tuple[str, bytes]:
        from AppKit import NSWorkspace
        from PIL import Image
        import Quartz

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            raise RuntimeError("no frontmost application")
        name = str(app.localizedName())
        if name == "Steam Helper":
            name = "Steam"
        pid = int(app.processIdentifier())
        image = self.pyautogui.screenshot()
        bounds = self._front_window_bounds(Quartz, pid)
        if bounds is None:
            raise RuntimeError("frontmost application has no visible window")
        logical_width, logical_height = self.pyautogui.size()
        scale_x = image.width / logical_width
        scale_y = image.height / logical_height
        x, y, width, height = bounds
        rectangle = (
            max(0, round(x * scale_x)),
            max(0, round(y * scale_y)),
            min(image.width, round((x + width) * scale_x)),
            min(image.height, round((y + height) * scale_y)),
        )
        if rectangle[2] <= rectangle[0] or rectangle[3] <= rectangle[1]:
            raise RuntimeError("frontmost window is outside the captured screen")
        visible = Image.new("RGB", image.size, "black")
        visible.paste(image.crop(rectangle), rectangle[:2])
        buffer = BytesIO()
        visible.save(buffer, format="PNG")
        return name, buffer.getvalue()

    @staticmethod
    def _front_window_bounds(quartz, pid: int) -> tuple[float, float, float, float] | None:
        windows = quartz.CGWindowListCopyWindowInfo(
            quartz.kCGWindowListOptionOnScreenOnly, quartz.kCGNullWindowID
        )
        largest = None
        largest_area = 0
        for window in windows:
            if window.get(quartz.kCGWindowOwnerPID) != pid or window.get(quartz.kCGWindowLayer) != 0:
                continue
            bounds = window.get(quartz.kCGWindowBounds)
            if bounds:
                width = float(bounds["Width"])
                height = float(bounds["Height"])
                if width * height > largest_area:
                    largest_area = width * height
                    largest = (float(bounds["X"]), float(bounds["Y"]), width, height)
        return largest

    def targets(self, image: bytes) -> list[Target]:
        from Foundation import NSData, NSMakeRange
        import Vision

        logical_width, logical_height = self.pyautogui.size()
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        data = NSData.dataWithBytes_length_(image, len(image))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
        performed, error = handler.performRequests_error_([request], None)
        if not performed:
            raise RuntimeError("Vision OCR failed") from error

        targets = []
        for result in request.results() or []:
            candidates = result.topCandidates_(1)
            if not candidates:
                continue
            top = candidates[0]
            label = str(top.string()).strip()
            if not label or top.confidence() < 0.5:
                continue
            words = []
            for match in re.finditer(r"\S+", label):
                start = len(label[:match.start()].encode("utf-16-le")) // 2
                length = len(match.group().encode("utf-16-le")) // 2
                word_box, error = top.boundingBoxForRange_error_(NSMakeRange(start, length), None)
                if error or not word_box:
                    words = []
                    break
                box = word_box.boundingBox()
                words.append((
                    match.group(),
                    box.origin.x * logical_width,
                    (box.origin.x + box.size.width) * logical_width,
                    (1 - box.origin.y - box.size.height) * logical_height,
                    (1 - box.origin.y) * logical_height,
                ))
            if not words:
                box = result.boundingBox()
                words = [(
                    label,
                    box.origin.x * logical_width,
                    (box.origin.x + box.size.width) * logical_width,
                    (1 - box.origin.y - box.size.height) * logical_height,
                    (1 - box.origin.y) * logical_height,
                )]
            for text, left, right, top_y, bottom in text_regions(label, words):
                x, y = round((left + right) / 2), round((top_y + bottom) / 2)
                if 0 <= x < logical_width and 0 <= y < logical_height:
                    targets.append(Target(text, x, y, "Vision OCR", float(top.confidence())))
        return targets

    def click(self, x: int, y: int) -> None:
        self.pyautogui.click(x, y)

    def type_search(self, text: str) -> None:
        self.pyautogui.write(text, interval=0)
        self.pyautogui.press("enter")


def group_words(words: list[tuple[str, float, float, float, float]]) -> list[tuple[str, float, float, float, float]]:
    """Keep ordinary spaces within a label; separate distant controls on one OCR line."""
    if not words:
        return []
    groups = []
    text, left, right, top, bottom = words[0]
    for word, word_left, word_right, word_top, word_bottom in words[1:]:
        height = max(bottom - top, word_bottom - word_top)
        if word_left - right > 1.2 * height:
            groups.append((text, left, right, top, bottom))
            text, left, right, top, bottom = word, word_left, word_right, word_top, word_bottom
        else:
            text = f"{text} {word}"
            right = max(right, word_right)
            top = min(top, word_top)
            bottom = max(bottom, word_bottom)
    groups.append((text, left, right, top, bottom))
    return groups


def text_regions(label: str, words: list[tuple[str, float, float, float, float]]) -> list[tuple[str, float, float, float, float]]:
    """Vision can join distinct uppercase menu items into one header line."""
    if len(words) > 1 and label.isupper() and words[0][3] < 120:
        return words
    return group_words(words)
