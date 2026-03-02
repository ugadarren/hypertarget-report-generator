from __future__ import annotations

from app.data.industry_profiles import INDUSTRY_KEYWORDS, SECTOR_DETAILS
from app.models import SectorProfile


def infer_sector(website_text: str, notes: str | None = None) -> SectorProfile:
    haystack = f"{website_text} {(notes or '').lower()}"
    scores: dict[str, int] = {}

    for sector, keywords in INDUSTRY_KEYWORDS.items():
        scores[sector] = sum(1 for keyword in keywords if keyword in haystack)

    best_sector = max(scores, key=scores.get) if scores else "construction"
    best_score = scores.get(best_sector, 0)

    details = SECTOR_DETAILS.get(best_sector, SECTOR_DETAILS["construction"])
    confidence = min(0.95, 0.35 + (0.1 * best_score))

    evidence = [f"Keyword matches for {details['label']}: {best_score}"]

    return SectorProfile(
        sector=details["label"],
        confidence=confidence,
        software_systems=details["software"],
        equipment=details["equipment"],
        evidence=evidence,
    )
