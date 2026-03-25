from __future__ import annotations

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.models import SectorProfile
from app.services.location import (
    _estimate_jtc_benefit,
    _investment_credit_pct_for_tier,
    _normalize_tier_value,
    load_credit_policy,
)
from app.services.opportunity_engine import _is_rd_core_sector, build_credit_assessments
from app.services.sector import keyword_sector_scores


class TierLogicTests(unittest.TestCase):
    def test_normalize_bottom_and_lower_40_to_tier_1(self):
        self.assertEqual(_normalize_tier_value("Bottom 40"), "1")
        self.assertEqual(_normalize_tier_value("Lower 40"), "1")
        self.assertEqual(_normalize_tier_value("Tier 1 Bottom 40"), "1")
        self.assertEqual(_normalize_tier_value("Tier 1 Lower 40"), "1")

    def test_ldct_threshold_is_plus_5(self):
        threshold, amount = _estimate_jtc_benefit(
            tier_value="2",
            military_zone=False,
            ldct=True,
            opportunity_zone=False,
            tier1_lower_40=False,
        )
        self.assertEqual(threshold, "+5")
        self.assertEqual(amount, "$3,500/yr for 5 years")

    def test_ldct_overrides_tier3_county_benefit(self):
        threshold, amount = _estimate_jtc_benefit(
            tier_value="3",
            military_zone=False,
            ldct=True,
            opportunity_zone=False,
            tier1_lower_40=False,
        )
        self.assertEqual(threshold, "+5")
        self.assertEqual(amount, "$3,500/yr for 5 years")

    def test_multiple_special_designations_choose_best_special_benefit(self):
        threshold, amount = _estimate_jtc_benefit(
            tier_value="4",
            military_zone=False,
            ldct=True,
            opportunity_zone=True,
            tier1_lower_40=False,
        )
        self.assertEqual(threshold, "+2")
        self.assertEqual(amount, "$3,500/yr for 5 years")

    def test_tier1_lower40_threshold_and_amount(self):
        threshold, amount = _estimate_jtc_benefit(
            tier_value="1",
            military_zone=False,
            ldct=False,
            opportunity_zone=False,
            tier1_lower_40=True,
        )
        self.assertEqual(threshold, "+2")
        self.assertEqual(amount, "$3,500/yr for 5 years")

    def test_itc_percentages_match_policy(self):
        self.assertEqual(_investment_credit_pct_for_tier("1"), "5%")
        self.assertEqual(_investment_credit_pct_for_tier("2"), "3%")
        self.assertEqual(_investment_credit_pct_for_tier("3"), "3%")
        self.assertEqual(_investment_credit_pct_for_tier("4"), "1%")

    def test_credit_policy_file_can_override_defaults(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "itc": {"pct_by_tier": {"1": "6%"}},
                        "jtc": {"base_threshold_by_tier": {"2": "+12"}},
                    }
                )
            )
            policy = load_credit_policy(path)
            self.assertEqual(policy["itc"]["pct_by_tier"]["1"], "6%")
            self.assertEqual(policy["jtc"]["base_threshold_by_tier"]["2"], "+12")
            # Ensure default values remain present for non-overridden keys.
            self.assertEqual(policy["itc"]["pct_by_tier"]["4"], "1%")


class RdConservatismTests(unittest.TestCase):
    def _sector(self, key: str, label: str, rd_feasibility: str = "possible", rd_conf: float = 0.8) -> SectorProfile:
        return SectorProfile(
            sector_key=key,
            sector=label,
            rd_feasibility=rd_feasibility,  # type: ignore[arg-type]
            rd_confidence=rd_conf,
        )

    def test_non_core_sector_is_conservative(self):
        sector = self._sector("logistics", "Logistics and Distribution", rd_feasibility="possible", rd_conf=0.9)
        credits, _, _ = build_credit_assessments(
            sector=sector,
            locations=[],
            research_text="automation prototype",
            notes=None,
        )
        federal = next(c for c in credits if c.code == "FEDERAL_RD")
        self.assertEqual(federal.status, "possible")
        self.assertLessEqual(federal.confidence, 0.49)

    def test_core_sector_can_remain_likely(self):
        sector = self._sector("manufacturing", "Manufacturing", rd_feasibility="likely", rd_conf=0.8)
        credits, _, _ = build_credit_assessments(
            sector=sector,
            locations=[],
            research_text="automation prototype engineering design development",
            notes=None,
        )
        federal = next(c for c in credits if c.code == "FEDERAL_RD")
        self.assertEqual(federal.status, "likely")
        self.assertGreaterEqual(federal.confidence, 0.68)

    def test_core_sector_detector(self):
        self.assertTrue(_is_rd_core_sector(self._sector("manufacturing", "Manufacturing")))
        self.assertTrue(_is_rd_core_sector(self._sector("software", "Software and Technology")))
        self.assertFalse(_is_rd_core_sector(self._sector("logistics", "Logistics and Distribution")))


class SectorInferenceTests(unittest.TestCase):
    def test_medical_supply_keywords_rank_healthcare_above_electrical(self):
        ranked = keyword_sector_scores(
            "ASP Global is a strategic sourcing partner for healthcare providers and distributors of medical products, blood collection, diagnostic, and exam gloves.",
            ["healthcare systems", "medical supplies", "diagnostic products"],
        )
        self.assertEqual(ranked[0][0], "healthcare")


if __name__ == "__main__":
    unittest.main()
