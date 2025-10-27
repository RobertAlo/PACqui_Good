#progress
from __future__ import annotations
import sys, time
from dataclasses import dataclass
#PAcqui
@dataclass
class ProgressStats:
    files: int = 0
    bytes: int = 0
    elapsed: float = 0.0
    rate_fps: float = 0.0

class PrintLogger:
    def __init__(self):
        self._last = 0.0
    def info(self, msg: str): print(f"[INFO] {msg}")
    def warn(self, msg: str): print(f"[WARN] {msg}", file=sys.stderr)
    def error(self, msg: str): print(f"[ERROR] {msg}", file=sys.stderr)
    def progress(self, stats: ProgressStats):
        now = time.time()
        if now - self._last >= 0.5:
            self._last = now
            print(f"[PROG] {stats.files} f · {stats.bytes/1e9:.2f} GB · {stats.rate_fps:.1f} f/s · {stats.elapsed:.1f}s", end="\r")
