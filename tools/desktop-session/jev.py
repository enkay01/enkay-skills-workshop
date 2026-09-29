"""Small HTTP adapter for TypeSafe's documented Choice endpoint."""

from __future__ import annotations

import json
import os
from math import hypot
from urllib.request import Request, urlopen

from controller import Candidate, ChoiceResult, Observation, Subgoal


def describe_candidate(candidate: Candidate, candidates: tuple[Candidate, ...]) -> str:
    target = candidate.target
    neighbors = sorted(
        (other for other in candidates if other.id != candidate.id
         and other.target.label.casefold() != target.label.casefold()),
        key=lambda other: hypot(other.target.x - target.x, other.target.y - target.y),
    )
    nearby = ", ".join(
        repr(other.target.label) for other in neighbors[:2]
        if hypot(other.target.x - target.x, other.target.y - target.y) <= 200
    )
    context = f"; near {nearby}" if nearby else ""
    return (
        f"Click visible text {target.label!r} at logical x={target.x}, y={target.y}"
        f" ({target.source}, recognition confidence {target.confidence:.2f};"
        f" interactivity unverified{context})"
    )


def parse_choice_answer(answer: dict, options: dict[str, str]) -> ChoiceResult:
    choice = answer["choice"]
    if answer.get("type") != "choice" or choice not in options:
        raise ValueError("invalid Jev choice")
    probabilities = {name: float(value) for name, value in answer["probabilities"].items()}
    if choice not in probabilities or any(
        name not in options or not 0 <= value <= 1 for name, value in probabilities.items()
    ):
        raise ValueError("invalid Jev probabilities")
    confidence = float(answer["confidence"])
    if not 0 <= confidence <= 1:
        raise ValueError("invalid Jev confidence")
    return ChoiceResult(choice, probabilities, confidence)


class JevChooser:
    def __init__(self, api_key: str | None = None, *, timeout: float = 8.0):
        self._api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self._api_key:
            raise ValueError("TYPESAFE_API_KEY is required")
        self.timeout = timeout

    def choose(self, subgoal: Subgoal, observation: Observation) -> ChoiceResult:
        visible = observation.candidates[:254]
        criteria = {
            c.id: describe_candidate(c, visible)
            for c in visible
        }
        criteria["none"] = "None of these clicks safely advances the subgoal"
        payload = {
            "model": "jev-latest",
            "state": {
                "subgoal": subgoal.instruction,
                "frontmost_app": observation.window,
                "visible_text": [c.target.label for c in visible],
            },
            "questions": {
                "next_action": {
                    "type": "choice",
                    "instructions": "Which listed visible text should be clicked next to advance `subgoal`? Choose none if no click is justified by the visible text. The candidate descriptions refer to the current screen only.",
                    "criteria": criteria,
                }
            },
        }
        request = Request(
            "https://api.typesafe.ai/v1/systemone",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            answer = json.load(response)["answers"]["next_action"]
        return parse_choice_answer(answer, criteria)
