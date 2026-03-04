from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import SectorCorrectionInput


class FeedbackService:
    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.corrections_path = self.reports_dir / "sector_corrections.jsonl"

    def record_sector_correction(self, payload: SectorCorrectionInput) -> dict[str, Any]:
        record = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **payload.model_dump(),
        }
        with self.corrections_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        return {"status": "ok", "path": str(self.corrections_path), "record": record}
