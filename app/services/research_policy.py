from __future__ import annotations

import json
from pathlib import Path


DEFAULT_RESEARCH_POLICY = {
    "page_timeout_seconds": 12.0,
    "max_pages_default": 10,
    "max_snippets_default": 30,
    "priority_link_terms": [
        "news",
        "newsroom",
        "press",
        "media",
        "about",
        "locations",
        "facility",
        "facilities",
        "expansion",
        "investor",
    ],
}


def load_research_policy(path: Path) -> dict:
    if not path.exists():
        return dict(DEFAULT_RESEARCH_POLICY)
    try:
        data = json.loads(path.read_text())
    except Exception:
        return dict(DEFAULT_RESEARCH_POLICY)
    if not isinstance(data, dict):
        return dict(DEFAULT_RESEARCH_POLICY)

    output = dict(DEFAULT_RESEARCH_POLICY)
    for key in ("page_timeout_seconds", "max_pages_default", "max_snippets_default"):
        if key in data:
            try:
                output[key] = float(data[key]) if key == "page_timeout_seconds" else int(data[key])
            except Exception:
                pass
    terms = data.get("priority_link_terms")
    if isinstance(terms, list):
        cleaned = [str(item).strip().lower() for item in terms if str(item).strip()]
        if cleaned:
            output["priority_link_terms"] = cleaned
    return output
