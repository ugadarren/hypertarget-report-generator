#!/usr/bin/env bash
set -euo pipefail

# Usage example:
#   bash scripts/update_policy_data.sh 2026
#
# Expects:
#   app/data/ga_county_tiers_history_source.csv  (county,year,tier)
#   app/data/ga_credit_policy_source.csv         (program,category,tier_or_designation,value)

CURRENT_YEAR="${1:-}"

if [[ -z "$CURRENT_YEAR" ]]; then
  echo "Usage: bash scripts/update_policy_data.sh <current_year>"
  exit 1
fi

python3 scripts/import_ga_tier_history.py --current-year "$CURRENT_YEAR"
python3 scripts/import_ga_credit_policy.py

echo "Policy data updated for year ${CURRENT_YEAR}."
echo "Review:"
echo "  app/data/ga_county_tiers.json"
echo "  app/data/ga_county_tiers_by_year.json"
echo "  app/data/ga_credit_policy.json"
