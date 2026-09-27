import io
import sys
import types
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controller import ChoiceResult, Controller, Subgoal, Target, visually_similar


def png(color):
    out = io.BytesIO()
    Image.new("RGB", (80, 60), color).save(out, "PNG")
    return out.getvalue()


def patched_png(size):
    out = io.BytesIO()
    image = Image.new("RGB", (128, 72), "white")
    ImageDraw.Draw(image).rectangle((0, 0, size, size), fill="black")
    image.save(out, "PNG")
    return out.getvalue()


class Desktop:
    def __init__(self, frames, labels):
        self.frames = iter(frames)
        self.labels = labels
        self.clicks = []
        self.typed = []
        self.last = None

    def capture(self):
        self.last = next(self.frames)
        return "Steam", self.last

    def targets(self, image):
        return [Target(label, 10, 10, "OCR", 0.9) for label in self.labels[image]]

    def click(self, x, y):
        self.clicks.append((x, y))

    def type_search(self, text):
        self.typed.append(text)


class Chooser:
    def __init__(self, response):
        self.response = response

    def choose(self, *_):
        if isinstance(self.response, tuple):
            choice, probability = self.response
            return ChoiceResult(choice, {choice: probability, "none": 1 - probability}, probability)
        return self.response


GOAL = Subgoal("Find game", "Steam", max_actions=1)


class ControllerTests(unittest.TestCase):
    def test_already_at_destination_finishes_without_click(self):
        screen = png("white")
        desktop = Desktop([screen], {screen: ["What's New", "Recent Games", "Home"]})
        goal = Subgoal("Open Library Home", "Steam", ("What's New", "Recent Games"))

        result = Controller(desktop, Chooser(("c3", 0.99))).run(goal)

        self.assertEqual((result.status, result.actions), ("completed", 0))
        self.assertEqual(desktop.clicks, [])

    def test_destination_markers_tolerate_ocr_suffixes(self):
        screen = png("white")
        desktop = Desktop([screen], {screen: ["What's New *", "Recent Games V"]})
        goal = Subgoal("Open Library Home", "Steam", ("What's New", "Recent Games"))

        result = Controller(desktop, Chooser(("c1", 0.99))).run(goal)

        self.assertEqual((result.status, result.actions), ("completed", 0))

    def test_clicks_current_candidate(self):
        a = png("white")
        desktop = Desktop([a, a], {a: ["Library"]})
        result = Controller(desktop, Chooser(("c1", 0.95))).run(GOAL)
        self.assertEqual((result.status, result.actions), ("needs_visual_inspection", 1))
        self.assertEqual(desktop.clicks, [(10, 10)])

    def test_stale_screen_is_not_clicked(self):
        a, b = png("white"), png("black")
        desktop = Desktop([a, b, b], {a: ["Library"], b: []})
        result = Controller(desktop, Chooser(("c1", 0.95))).run(GOAL)
        self.assertEqual(result.status, "needs_visual_inspection")
        self.assertEqual(desktop.clicks, [])

    def test_small_animation_is_tolerated_but_layout_change_is_not(self):
        self.assertTrue(visually_similar(patched_png(0), patched_png(2)))
        self.assertFalse(visually_similar(patched_png(0), patched_png(30)))

    def test_completion_requires_destination_markers_after_action(self):
        a, b = png("white"), png("black")
        labels = {a: ["Game title", "Library"], b: ["Game title", "Game details", "Play"]}
        desktop = Desktop([a, a, b], labels)
        goal = Subgoal("Open game details", "Steam", ("Game details", "Play"), max_actions=2)
        result = Controller(desktop, Chooser(("c2", 0.95))).run(goal)
        self.assertEqual((result.status, result.actions), ("completed", 1))

    def test_title_already_visible_does_not_complete(self):
        a = png("white")
        desktop = Desktop([a, a], {a: ["Game title", "Library"]})
        goal = Subgoal("Open game details", "Steam", ("Game title", "Play"), max_actions=1)
        result = Controller(desktop, Chooser(("c2", 0.95))).run(goal)
        self.assertEqual(result.status, "needs_visual_inspection")

    def test_none_escalates_without_click(self):
        a = png("white")
        desktop = Desktop([a], {a: ["Library"]})
        result = Controller(desktop, Chooser(("none", 0.99))).run(GOAL)
        self.assertEqual(result.reason, "no reliable action")
        self.assertEqual(desktop.clicks, [])

    def test_subgoal_can_lower_probability_gate_for_reversible_click(self):
        a = png("white")
        desktop = Desktop([a, a], {a: ["Recent game tile"]})
        goal = Subgoal("Open game details", "Steam", max_actions=1, min_probability=0.5)
        result = Controller(desktop, Chooser(("c1", 0.56))).run(goal)
        self.assertEqual(result.actions, 1)
        self.assertEqual(desktop.clicks, [(10, 10)])

    def test_unchanged_reversible_click_tries_next_ranked_candidate(self):
        before, destination = png("white"), png("black")

        class TwoTargetDesktop:
            def __init__(self):
                self.screen = before
                self.clicks = []

            def capture(self):
                return "Steam", self.screen

            def targets(self, image):
                if image == destination:
                    return [Target("Destination", 30, 10, "OCR", 1.0)]
                return [Target("Wrong Home", 10, 10, "OCR", 1.0),
                        Target("Menu Home", 20, 10, "OCR", 1.0)]

            def click(self, x, y):
                self.clicks.append((x, y))
                if x == 20:
                    self.screen = destination

            def type_search(self, text):
                raise AssertionError("no text entry expected")

        desktop = TwoTargetDesktop()
        ranking = types.SimpleNamespace(
            choice="c1", probabilities={"c1": 0.58, "c2": 0.40, "none": 0.02},
            confidence=0.2,
        )
        goal = Subgoal("Open destination", "Steam", ("Destination",),
                       max_actions=2, min_probability=0.4, max_unchanged_retries=1)

        result = Controller(desktop, Chooser(ranking)).run(goal)

        self.assertEqual((result.status, result.actions), ("completed", 2))
        self.assertEqual(desktop.clicks, [(10, 10), (20, 10)])

    def test_search_uses_supplied_text_only(self):
        a = png("white")
        desktop = Desktop([a, a], {a: ["Search"]})
        goal = Subgoal("Find game", "Steam", search_text="Football Manager 26", max_actions=1)
        Controller(desktop, Chooser(("c1", 0.99))).run(goal)
        self.assertEqual(desktop.typed, ["Football Manager 26"])


if __name__ == "__main__":
    unittest.main()
