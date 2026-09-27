"""A bounded, resident observation/decision/action loop.

The controller knows screen coordinates. Jev only sees numbered descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from time import monotonic, sleep
from typing import Callable, Protocol


@dataclass(frozen=True)
class Target:
    label: str
    x: int
    y: int
    source: str
    confidence: float


@dataclass(frozen=True)
class Candidate:
    id: str
    target: Target


@dataclass(frozen=True)
class Observation:
    version: int
    window: str
    fingerprint: str
    image: bytes
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class Subgoal:
    instruction: str
    expected_window: str
    completion_labels: tuple[str, ...] = ()
    search_text: str | None = None
    search_field_text: str = "Search"
    max_actions: int = 5
    max_seconds: float = 30.0
    min_probability: float | None = None
    max_unchanged_retries: int = 0


@dataclass(frozen=True)
class Result:
    status: str
    reason: str
    actions: int
    observation: Observation | None


@dataclass(frozen=True)
class ChoiceResult:
    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass
class ActionPlan:
    screen: Observation
    choice: ChoiceResult
    attempted: set[str] = field(default_factory=set)

    def matches(self, observation: Observation) -> bool:
        return visually_similar(self.screen.image, observation.image)

    def exhausted(self, subgoal: Subgoal) -> bool:
        return bool(self.attempted) and (
            bool(subgoal.search_text) or len(self.attempted) > subgoal.max_unchanged_retries
        )

    def next_candidate(self, observation: Observation, threshold: float) -> Candidate | None:
        if self.choice.choice == "none":
            return None
        none_probability = self.choice.probabilities.get("none", 0.0)
        ranked = sorted(observation.candidates,
                        key=lambda candidate: self.choice.probabilities.get(candidate.id, 0.0),
                        reverse=True)
        return next((candidate for candidate in ranked
                     if candidate.id not in self.attempted
                     and self.choice.probabilities.get(candidate.id, 0.0) >= threshold
                     and self.choice.probabilities.get(candidate.id, 0.0) > none_probability), None)


class Desktop(Protocol):
    def capture(self) -> tuple[str, bytes]: ...
    def targets(self, image: bytes) -> list[Target]: ...
    def click(self, x: int, y: int) -> None: ...
    def type_search(self, text: str) -> None: ...


class Chooser(Protocol):
    def choose(self, subgoal: Subgoal, observation: Observation) -> ChoiceResult: ...


def fingerprint(image: bytes) -> str:
    """Compact screen state for loop detection, not the pre-click safety check."""
    from PIL import Image
    from hashlib import sha256

    picture = Image.open(BytesIO(image)).convert("L").resize((64, 36))
    return sha256(picture.tobytes()).hexdigest()


class Controller:
    def __init__(self, desktop: Desktop, chooser: Chooser, *, min_probability: float = 0.7):
        self.desktop = desktop
        self.chooser = chooser
        self.min_probability = min_probability
        self._version = 0
        self.timings: dict[str, float] = {"capture_s": 0.0, "ocr_s": 0.0, "jev_s": 0.0,
                                          "action_s": 0.0, "captures": 0, "ocr_passes": 0,
                                          "jev_calls": 0}

    def observe(self) -> Observation:
        started = monotonic()
        window, image = self.desktop.capture()
        self.timings["capture_s"] += monotonic() - started
        self.timings["captures"] += 1
        self._version += 1
        started = monotonic()
        targets = self.desktop.targets(image)
        self.timings["ocr_s"] += monotonic() - started
        self.timings["ocr_passes"] += 1
        candidates = tuple(Candidate(f"c{i}", target) for i, target in enumerate(targets, 1))
        return Observation(self._version, window, fingerprint(image), image, candidates)

    def _fresh(self, observation: Observation, selected: Candidate) -> bool:
        started = monotonic()
        window, image = self.desktop.capture()
        self.timings["capture_s"] += monotonic() - started
        self.timings["captures"] += 1
        if window != observation.window or not visually_similar(observation.image, image):
            return False
        started = monotonic()
        fresh_targets = self.desktop.targets(image)
        self.timings["ocr_s"] += monotonic() - started
        self.timings["ocr_passes"] += 1
        return any(
            target.label.casefold() == selected.target.label.casefold()
            and abs(target.x - selected.target.x) <= 5
            and abs(target.y - selected.target.y) <= 5
            for target in fresh_targets
        )

    def _terminal_result(self, subgoal: Subgoal, observation: Observation, actions: int) -> Result | None:
        if observation.window != subgoal.expected_window:
            return Result("needs_visual_inspection", "unexpected frontmost app", actions, observation)
        labels = {candidate.target.label.casefold() for candidate in observation.candidates}
        if subgoal.completion_labels and all(
            any(marker.casefold() in label for label in labels)
            for marker in subgoal.completion_labels
        ):
            return Result("completed", "destination markers visible", actions, observation)
        if actions >= subgoal.max_actions:
            return Result("needs_visual_inspection", "action limit", actions, observation)
        if not observation.candidates:
            return Result("needs_visual_inspection", "no text candidates", actions, observation)
        return None

    def _plan_for(self, subgoal: Subgoal, observation: Observation,
                  previous: ActionPlan | None) -> ActionPlan:
        if previous is not None and previous.matches(observation):
            return previous
        started = monotonic()
        choice = self.chooser.choose(subgoal, observation)
        self.timings["jev_s"] += monotonic() - started
        self.timings["jev_calls"] += 1
        return ActionPlan(observation, choice)

    def _perform(self, subgoal: Subgoal, observation: Observation, selected: Candidate) -> bool:
        if not self._fresh(observation, selected):
            return False
        started = monotonic()
        self.desktop.click(selected.target.x, selected.target.y)
        if subgoal.search_text and subgoal.search_field_text.casefold() in selected.target.label.casefold():
            self.desktop.type_search(subgoal.search_text)
        self.timings["action_s"] += monotonic() - started
        return True

    def _select(self, subgoal: Subgoal, observation: Observation,
                previous: ActionPlan | None, actions: int) -> tuple[ActionPlan, Candidate] | Result:
        plan = self._plan_for(subgoal, observation, previous)
        if plan.exhausted(subgoal):
            return Result("needs_visual_inspection", "screen did not progress", actions, observation)
        threshold = self.min_probability if subgoal.min_probability is None else subgoal.min_probability
        selected = plan.next_candidate(observation, threshold)
        if selected is None:
            return Result("needs_visual_inspection", "no reliable action", actions, observation)
        return plan, selected

    def run(self, subgoal: Subgoal, on_observation: Callable[[Observation], None] | None = None) -> Result:
        start = monotonic()
        self.timings = {"capture_s": 0.0, "ocr_s": 0.0, "jev_s": 0.0,
                        "action_s": 0.0, "captures": 0, "ocr_passes": 0, "jev_calls": 0}
        actions = 0
        observations = 0
        last: Observation | None = None
        plan: ActionPlan | None = None

        while monotonic() - start < subgoal.max_seconds:
            observations += 1
            if observations > subgoal.max_actions * 4 + 1:
                return Result("needs_visual_inspection", "screen changed too often", actions, last)
            try:
                current = self.observe()
            except Exception as exc:
                return Result("needs_visual_inspection", f"observation failed: {type(exc).__name__}", actions, last)
            last = current
            if on_observation:
                on_observation(current)
            terminal = self._terminal_result(subgoal, current, actions)
            if terminal is not None:
                return terminal
            try:
                selection = self._select(subgoal, current, plan, actions)
            except Exception as exc:
                return Result("needs_visual_inspection", f"Jev failed: {type(exc).__name__}", actions, current)
            if isinstance(selection, Result):
                return selection
            plan, selected = selection
            try:
                clicked = self._perform(subgoal, current, selected)
            except Exception as exc:
                return Result("needs_visual_inspection", f"action failed: {type(exc).__name__}", actions, current)
            if not clicked:
                plan = None
                continue
            plan.attempted.add(selected.id)
            actions += 1
            sleep(0.15)

        return Result("needs_visual_inspection", "time limit", actions, last)


def visually_similar(previous: bytes, current: bytes) -> bool:
    """Allow small animation while rejecting substantial layout changes."""
    from PIL import Image, ImageChops

    a = Image.open(BytesIO(previous)).convert("L").resize((128, 72))
    b = Image.open(BytesIO(current)).convert("L").resize((128, 72))
    differences = ImageChops.difference(a, b).histogram()
    changed = sum(differences[25:]) / (128 * 72)
    return changed <= 0.02
