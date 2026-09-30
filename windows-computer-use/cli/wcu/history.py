"""Bounded in-memory operation history.

Records what the tool did, never what the user typed and never image
contents. An entry carries the operation name, an outcome, the observation
identity where applicable, and an error code when something was refused or
failed. The list is capped; the oldest entries are dropped first.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

MAX_ENTRIES = 200


class History:
    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._entries: List[Dict[str, Any]] = []
        self._max = max_entries
        self._lock = threading.Lock()

    def record(
        self,
        op: str,
        outcome: str,
        observation_id: Optional[int] = None,
        error_code: Optional[str] = None,
        **meta: Any,
    ) -> None:
        entry: Dict[str, Any] = {
            "ts": time.time(),
            "op": op,
            "outcome": outcome,
        }
        if observation_id is not None:
            entry["observation_id"] = int(observation_id)
        if error_code is not None:
            entry["error_code"] = error_code
        for key, value in meta.items():
            if value is not None:
                entry[key] = value
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max:
                del self._entries[: len(self._entries) - self._max]

    def entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._entries)
