from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}

@dataclass
class EventLogger:
    events_path: str
    level: str = "INFO"

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(self.events_path), exist_ok=True)

    def _emit(self, level: str, rec: dict[str, Any]) -> None:
        if LEVELS[level] < LEVELS.get(self.level, 20):
            return
        rec2 = {"ts": time.time(), "level": level, **rec}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec2, ensure_ascii=False) + "\n")

    def debug(self, **rec: Any) -> None:
        self._emit("DEBUG", rec)

    def info(self, **rec: Any) -> None:
        self._emit("INFO", rec)

    def warn(self, **rec: Any) -> None:
        self._emit("WARN", rec)

    def error(self, **rec: Any) -> None:
        self._emit("ERROR", rec)
