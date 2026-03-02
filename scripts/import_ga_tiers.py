#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

INPUT = Path("app/data/ga_county_tiers_source.csv")
OUTPUT = Path("app/data/ga_county_tiers.json")


def main() -> None:
    if not INPUT.exists():
        raise SystemExit(f"Missing source file: {INPUT}")

    mapping: dict[str, str] = {}
    with INPUT.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            county = (row.get("county") or "").lower().replace(" county", "").strip()
            tier = (row.get("tier") or "").strip()
            if county and tier:
                mapping[county] = tier

    OUTPUT.write_text(json.dumps(mapping, indent=2, sort_keys=True))
    print(f"Wrote {len(mapping)} counties to {OUTPUT}")


if __name__ == "__main__":
    main()
