#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def normalize_county(value: str) -> str:
    county = (value or "").lower().replace(" county", "").strip()
    return " ".join(county.split())


def normalize_tier(value: str) -> str:
    return (value or "").strip()


def _normalize_header(name: str) -> str:
    return (name or "").strip().lower().lstrip("\ufeff")


def _detect_wide_year_columns(fieldnames: list[str]) -> dict[str, str]:
    # Supports columns like "2021 Tier", "2022 Tier", etc.
    year_cols: dict[str, str] = {}
    for raw in fieldnames:
        normalized = _normalize_header(raw)
        parts = normalized.split()
        if len(parts) >= 1 and parts[0].isdigit() and len(parts[0]) == 4:
            year = parts[0]
            year_cols[year] = raw
    return year_cols


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import multi-year GA county tiers from CSV and generate current + history JSON files."
    )
    parser.add_argument(
        "--input",
        default="app/data/ga_county_tiers_history_source.csv",
        help="CSV input path with columns: county,year,tier",
    )
    parser.add_argument(
        "--history-output",
        default="app/data/ga_county_tiers_by_year.json",
        help="Output JSON path for multi-year tier map",
    )
    parser.add_argument(
        "--current-output",
        default="app/data/ga_county_tiers.json",
        help="Output JSON path for current-year county tier map",
    )
    parser.add_argument(
        "--current-year",
        default="",
        help="Optional explicit current year. If omitted, latest year in input is used.",
    )
    parser.add_argument(
        "--policy-versions",
        default="app/data/policy_versions.json",
        help="Policy versions JSON file to auto-update with current year metadata.",
    )
    parser.add_argument(
        "--skip-policy-update",
        action="store_true",
        help="Skip updating policy_versions.json metadata.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Missing source file: {input_path}")

    by_year: dict[str, dict[str, str]] = defaultdict(dict)
    with input_path.open() as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        normalized_headers = {_normalize_header(h) for h in fieldnames}
        long_required = {"county", "year", "tier"}
        if long_required.issubset(normalized_headers):
            # Long format rows: county,year,tier
            for row in reader:
                county = normalize_county(row.get("county", "") or row.get("County", ""))
                year = (row.get("year", "") or row.get("Year", "")).strip()
                tier = normalize_tier(row.get("tier", "") or row.get("Tier", ""))
                if not county or not year or not tier:
                    continue
                by_year[year][county] = tier
        else:
            # Wide format rows: County, 2021 Tier, 2022 Tier, ...
            county_col = next((h for h in fieldnames if _normalize_header(h) == "county"), "")
            year_cols = _detect_wide_year_columns(fieldnames)
            if not county_col or not year_cols:
                raise SystemExit(
                    "CSV must be either long format (county,year,tier) or wide format "
                    "(County, 2021 Tier, 2022 Tier, ...)."
                )
            for row in reader:
                county = normalize_county(row.get(county_col, ""))
                if not county:
                    continue
                for year, col in year_cols.items():
                    tier = normalize_tier(row.get(col, ""))
                    if tier:
                        by_year[year][county] = tier

    if not by_year:
        raise SystemExit("No valid county/year/tier rows found.")

    years_numeric = sorted([int(y) for y in by_year.keys() if y.isdigit()])
    if not years_numeric:
        raise SystemExit("Year values must be numeric.")
    current_year = args.current_year.strip() if args.current_year else str(max(years_numeric))
    if current_year not in by_year:
        raise SystemExit(f"Current year {current_year} not found in input data.")

    history_output = Path(args.history_output)
    history_output.write_text(json.dumps({k: by_year[k] for k in sorted(by_year.keys())}, indent=2, sort_keys=True))

    current_output = Path(args.current_output)
    current_output.write_text(json.dumps(by_year[current_year], indent=2, sort_keys=True))

    if not args.skip_policy_update:
        policy_path = Path(args.policy_versions)
        policy = {}
        if policy_path.exists():
            try:
                loaded = json.loads(policy_path.read_text())
                if isinstance(loaded, dict):
                    policy = loaded
            except Exception:
                policy = {}
        if "county_tiers" not in policy or not isinstance(policy.get("county_tiers"), dict):
            policy["county_tiers"] = {}
        policy["policy_year"] = str(current_year)
        policy["county_tiers"]["effective_year"] = str(current_year)
        policy["county_tiers"]["source"] = str(input_path)
        policy["county_tiers"].setdefault("source_date", "")
        policy["county_tiers"].setdefault("notes", "Update annually when DCA releases revised county tier rankings.")
        policy_path.write_text(json.dumps(policy, indent=2))

    print(f"Wrote {len(by_year)} year(s) to {history_output}")
    print(f"Wrote {len(by_year[current_year])} counties for {current_year} to {current_output}")
    if not args.skip_policy_update:
        print(f"Updated policy metadata year in {args.policy_versions}")


if __name__ == "__main__":
    main()
