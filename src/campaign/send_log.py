"""
Structured, human-readable send log, one line per event, written to
logs/YYYY-MM-DD.log (rotated daily by filename). Format matches the
brief exactly:

[2025-05-13 14:22:01] SENT     john@acme.com (John Smith)  | campaign: c_20250513_001
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_LOG_DIR = Path.home() / ".maillaunch" / "logs"

VALID_STATUSES = {"SENT", "FAILED", "RETRY", "SKIPPED", "LIMIT_REACHED"}


class SendLogger:
    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, when: datetime) -> Path:
        return self.log_dir / f"{when.strftime('%Y-%m-%d')}.log"

    def log(
        self,
        status: str,
        email: str,
        campaign_id: str,
        name: str = "",
        error: Optional[str] = None,
        when: Optional[datetime] = None,
    ) -> str:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid log status: {status}")
        when = when or datetime.now()
        ts = when.strftime("%Y-%m-%d %H:%M:%S")

        recipient = f"{email} ({name})" if name else email
        line = f"[{ts}] {status:<8} {recipient}  | campaign: {campaign_id}"
        if error:
            line += f" | error: {error}"

        path = self._path_for(when)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return line

    def read_today(self) -> str:
        path = self._path_for(datetime.now())
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def read_for_date(self, when: datetime) -> str:
        path = self._path_for(when)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
