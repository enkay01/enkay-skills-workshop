"""Small OpenAI-compatible vision adapter for the local HTTP model proxy."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any


class ModelError(RuntimeError):
    pass


class VisionModel:
    def __init__(self, endpoint: str, model: str, token: str, timeout_sec: float = 45.0):
        if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("The first model adapter requires a local HTTP proxy")
        if not model or not token:
            raise ValueError("A model alias and proxy token are required")
        self.endpoint = endpoint
        self.model = model
        self.token = token
        self.timeout_sec = timeout_sec

    def _ask(self, instruction: str, images: list[tuple[str, bytes]], width: int, height: int) -> dict[str, Any]:
        content = [{"type": "text", "text": f"Image dimensions: {width}x{height} pixels. {instruction}"}]
        for label, png in images:
            encoded = base64.b64encode(png).decode("ascii")
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        request_body = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": content,
            }],
            "max_tokens": 1200,
            "temperature": 0,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                reply = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ModelError(f"Model request failed: {type(exc).__name__}") from exc
        try:
            content = reply["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Model response content is not a string")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise TypeError("Model response is not a JSON object")
            return result
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError("Model returned an invalid JSON response") from exc

    def decide(self, task: str, png: bytes, width: int, height: int) -> dict[str, Any]:
        instruction = (
            "You control this Windows application using only its image. "
            f"Task: {task}. "
            "Return only one JSON object: "
            '{"intent":"click","bbox":[x,y,width,height]} for one visible target; '
            '{"intent":"done"} if already complete; or '
            '{"intent":"cannot_decide"} if uncertain. '
            "The rectangle must use the image pixel coordinates. Do not invent hidden controls."
        )
        return self._ask(instruction, [("Current image", png)], width, height)

    def verify(self, task: str, before_png: bytes, after_png: bytes, width: int, height: int) -> dict[str, Any]:
        instruction = (
            f"Compare the labeled images before and after one action for this task: {task}. "
            "Judge whether the requested visible change happened. Return only one JSON object: "
            '{"result":"success"}, {"result":"failure"}, or {"result":"uncertain"}. '
            "Choose uncertain when the image alone cannot establish the outcome."
        )
        return self._ask(instruction, [("Before action", before_png), ("After action", after_png)], width, height)
