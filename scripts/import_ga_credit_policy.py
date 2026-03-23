#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import GA credit policy from CSV into app/data/ga_credit_policy.json.\n"
            "Expected headers: program,category,tier_or_designation,value\n"
            "Examples:\n"
            "  jtc,base_threshold,1,+2\n"
            "  jtc,base_amount,1,$3,500/yr for 5 years\n"
            "  jtc,special_amount,1,$4,000/yr for 5 years\n"
            "  jtc,special_threshold,military_zone,+2\n"
            "  jtc,tier1_lower_40_amount,, $3,500/yr for 5 years\n"
            "  itc,pct,1,5%\n"
        )
    )
    parser.add_argument("--input", default="app/data/ga_credit_policy_source.csv")
    parser.add_argument("--output", default="app/data/ga_credit_policy.json")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Missing source file: {input_path}")

    policy = {
        "jtc": {
            "base_threshold_by_tier": {},
            "base_amount_by_tier": {},
            "special_amount_by_tier": {},
            "special_threshold_by_designation": {},
            "tier1_lower_40_amount": "",
        },
        "itc": {"pct_by_tier": {}},
    }

    with input_path.open() as f:
        reader = csv.DictReader(f)
        required = {"program", "category", "tier_or_designation", "value"}
        if not required.issubset({(h or "").strip().lower() for h in (reader.fieldnames or [])}):
            raise SystemExit("CSV must include headers: program,category,tier_or_designation,value")

        for row in reader:
            program = (row.get("program", "") or "").strip().lower()
            category = (row.get("category", "") or "").strip().lower()
            key = (row.get("tier_or_designation", "") or "").strip().lower()
            value = (row.get("value", "") or "").strip()
            if not program or not category or not value:
                continue

            if program == "jtc":
                if category == "base_threshold":
                    policy["jtc"]["base_threshold_by_tier"][key] = value
                elif category == "base_amount":
                    policy["jtc"]["base_amount_by_tier"][key] = value
                elif category == "special_amount":
                    policy["jtc"]["special_amount_by_tier"][key] = value
                elif category == "special_threshold":
                    policy["jtc"]["special_threshold_by_designation"][key] = value
                elif category == "tier1_lower_40_amount":
                    policy["jtc"]["tier1_lower_40_amount"] = value
            elif program == "itc":
                if category == "pct":
                    policy["itc"]["pct_by_tier"][key] = value

    output_path = Path(args.output)
    output_path.write_text(json.dumps(policy, indent=2, sort_keys=True))
    print(f"Wrote policy JSON to {output_path}")


if __name__ == "__main__":
    main()
