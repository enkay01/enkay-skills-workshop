import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controller import Candidate, Observation, Subgoal, Target
from jev import JevChooser


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class JevTests(unittest.TestCase):
    def test_choice_describes_position_without_key(self):
        observation = Observation(1, "Steam", "hash", b"image", (
            Candidate("c1", Target("Library", 711, 523, "OCR", 0.9)),
        ))
        subgoal = Subgoal("Open Library", "Steam")
        captured = {}

        def fake_open(request, timeout):
            captured["request"] = request
            answer = {"answers": {"next_action": {
                "type": "choice", "choice": "c1", "probabilities": {"c1": 0.9, "none": 0.1}, "confidence": 0.8
            }}}
            return Response(json.dumps(answer).encode())

        with patch("jev.urlopen", fake_open):
            choice = JevChooser("private-test-key").choose(subgoal, observation)
        body = json.loads(captured["request"].data)
        self.assertEqual((choice.choice, choice.probabilities["c1"], choice.confidence),
                         ("c1", 0.9, 0.8))
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(set(body["questions"]["next_action"]["criteria"]), {"c1", "none"})
        self.assertIn("711", body["questions"]["next_action"]["criteria"]["c1"])
        self.assertNotIn("private-test-key", captured["request"].data.decode())

    def test_duplicate_text_candidates_have_distinct_spatial_context(self):
        observation = Observation(1, "Steam", "hash", b"image", (
            Candidate("c1", Target("Home", 122, 129, "OCR", 1.0)),
            Candidate("c2", Target("Home", 272, 113, "OCR", 1.0)),
            Candidate("c3", Target("LIBRARY", 282, 78, "OCR", 1.0)),
            Candidate("c4", Target("Collections", 288, 147, "OCR", 1.0)),
            Candidate("c5", Target("Games", 135, 175, "OCR", 1.0)),
        ))
        captured = {}

        def fake_open(request, timeout):
            captured["body"] = json.loads(request.data)
            answer = {"answers": {"next_action": {
                "type": "choice", "choice": "c2",
                "probabilities": {"c1": 0.4, "c2": 0.5, "c3": 0.03,
                                  "c4": 0.02, "c5": 0.02, "none": 0.03},
                "confidence": 0.2,
            }}}
            return Response(json.dumps(answer).encode())

        with patch("jev.urlopen", fake_open):
            JevChooser("private-test-key").choose(Subgoal("Open Library Home", "Steam"), observation)

        criteria = captured["body"]["questions"]["next_action"]["criteria"]
        self.assertIn("Games", criteria["c1"])
        self.assertIn("LIBRARY", criteria["c2"])
        self.assertNotEqual(criteria["c1"], criteria["c2"])


if __name__ == "__main__":
    unittest.main()
