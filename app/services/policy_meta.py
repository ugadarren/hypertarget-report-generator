from __future__ import annotations

import json
from pathlib import Path


DEFAULT_POLICY_VERSIONS = {
    "policy_year": "unspecified",
    "county_tiers": {
        "effective_year": "unspecified",
        "source": "app/data/ga_county_tiers.json",
        "source_date": "unspecified",
        "notes": "",
    },
    "credit_policy": {
        "effective_year": "unspecified",
        "source": "app/data/ga_credit_policy.json",
        "source_date": "unspecified",
        "notes": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    output = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(output.get(key), dict):
            output[key] = _deep_merge(output[key], value)
        else:
            output[key] = value
    return output


def load_policy_versions(path: Path) -> dict:
    if not path.exists():
        return dict(DEFAULT_POLICY_VERSIONS)
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return dict(DEFAULT_POLICY_VERSIONS)
        return _deep_merge(DEFAULT_POLICY_VERSIONS, data)
    except Exception:
        return dict(DEFAULT_POLICY_VERSIONS)
