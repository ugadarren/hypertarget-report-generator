from __future__ import annotations

import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return data


def validate_data_files(data_dir: Path) -> None:
    required = [
        "ga_county_tiers.json",
        "ga_credit_policy.json",
        "policy_versions.json",
        "report_copy.json",
        "web_research_policy.json",
    ]
    missing = [name for name in required if not (data_dir / name).exists()]
    if missing:
        raise ValueError(f"Missing required data files: {', '.join(missing)}")

    versions = _load_json(data_dir / "policy_versions.json")
    if "policy_year" not in versions:
        raise ValueError("policy_versions.json missing required key: policy_year")

    credit_policy = _load_json(data_dir / "ga_credit_policy.json")
    if "jtc" not in credit_policy or "itc" not in credit_policy:
        raise ValueError("ga_credit_policy.json must include keys: jtc, itc")

    report_copy = _load_json(data_dir / "report_copy.json")
    for key in ("ga_jtc_intro", "rd_intro", "investment_intro", "costseg_intro"):
        if key not in report_copy:
            raise ValueError(f"report_copy.json missing required key: {key}")

    web_policy = _load_json(data_dir / "web_research_policy.json")
    if "priority_link_terms" not in web_policy:
        raise ValueError("web_research_policy.json missing required key: priority_link_terms")
