from __future__ import annotations

import re

from app.data.industry_profiles import EXPANSION_KEYWORDS, PROPERTY_KEYWORDS, RD_KEYWORDS
from app.models import CreditAssessment, LocationAssessment, SectorProfile


EXPANSION_PATTERNS = [
    r"new\s+(facility|plant|warehouse|location|headquarters|hq)",
    r"(opened|opening|opens)\s+(a\s+)?new\s+(facility|plant|warehouse|location|office)",
    r"(expanding|expanded|expansion)\s+(operations|facility|footprint|plant|campus)",
    r"ground\s*breaking|groundbreaking",
    r"capital\s+investment",
    r"square\s*foot|sq\.?\s*ft",
]


def _extract_signals(text: str, keywords: list[str]) -> list[str]:
    found = []
    for keyword in keywords:
        if keyword in text:
            found.append(keyword)
    return found


def _extract_expansion_signals(text: str) -> list[str]:
    signals = _extract_signals(text, EXPANSION_KEYWORDS)
    for pattern in EXPANSION_PATTERNS:
        if re.search(pattern, text):
            signals.append(f"pattern:{pattern}")
    return list(dict.fromkeys(signals))


def build_credit_assessments(
    sector: SectorProfile,
    locations: list[LocationAssessment],
    research_text: str,
    notes: str | None,
) -> tuple[list[CreditAssessment], list[str], list[str]]:
    combined = f"{research_text} {(notes or '').lower()}"
    rd_signals = _extract_signals(combined, RD_KEYWORDS)
    expansion_signals = _extract_expansion_signals(combined)
    property_signals = _extract_signals(combined, PROPERTY_KEYWORDS)

    ga_tiers = [loc.ga_tier for loc in locations if loc.ga_tier]
    has_target_tier = any(tier in {"1", "2", "3", "4"} for tier in ga_tiers)

    credits: list[CreditAssessment] = []

    retraining_status = "likely" if sector.software_systems else "possible"
    credits.append(
        CreditAssessment(
            code="GA_RETRAINING",
            name="Georgia Retraining Tax Credit",
            status=retraining_status,
            rationale="Sector software stack indicates periodic upskilling for systems adoption and process changes.",
            triggers=sector.software_systems[:4],
            assumptions=["Company has Georgia payroll tax liability", "Training expenses are documented"],
            confidence=0.72,
        )
    )

    rd_status = "likely" if len(rd_signals) >= 2 else "possible"
    rd_rationale = "Website/notes include engineering, integration, or process-improvement signals." if rd_signals else "No explicit R&D keywords found; potential based on sector operations."

    credits.append(
        CreditAssessment(
            code="FEDERAL_RD",
            name="Federal R&D Tax Credit",
            status=rd_status,
            rationale=rd_rationale,
            triggers=rd_signals[:6] or ["Sector benchmark indicates iterative engineering activity"],
            assumptions=["Technical uncertainty and experimentation occurred", "Qualified wage/supply data available"],
            confidence=0.68 if rd_signals else 0.45,
        )
    )

    credits.append(
        CreditAssessment(
            code="GA_RD",
            name="Georgia R&D Tax Credit",
            status=rd_status,
            rationale="Georgia credit follows similar qualified research principles with state-specific base calculations.",
            triggers=rd_signals[:6] or ["Potential qualifying development activity in operations"],
            assumptions=["Georgia nexus and qualified expenses", "Prior year spend available for base-period computation"],
            confidence=0.64 if rd_signals else 0.42,
        )
    )

    cost_seg_status = "likely" if property_signals else "possible"
    credits.append(
        CreditAssessment(
            code="COST_SEGREGATION",
            name="Cost Segregation Study",
            status=cost_seg_status,
            rationale="Property acquisition/improvement signals suggest opportunity to accelerate depreciation.",
            triggers=property_signals[:5] or ["No explicit purchase event found; recommend confirmation"],
            assumptions=["Commercial property acquired, built, or improved", "Depreciable basis is material"],
            confidence=0.74 if property_signals else 0.4,
        )
    )

    investment_status = "likely" if expansion_signals and has_target_tier else "possible"
    credits.append(
        CreditAssessment(
            code="GA_INVESTMENT",
            name="Georgia Investment Tax Credit",
            status=investment_status,
            rationale="Expansion/capital signals combined with county tier may support Georgia investment incentives.",
            triggers=expansion_signals[:5] or ["Need verification of recent capital investment in GA"],
            assumptions=["Qualified investment property placed in service", "Tier/location thresholds satisfied"],
            confidence=0.71 if expansion_signals and has_target_tier else 0.48,
        )
    )

    credits.append(
        CreditAssessment(
            code="MERP",
            name="Self-Insured Medical Reimbursement Plan (MERP)",
            status="possible",
            rationale="Most mid-size employers can evaluate MERP/HRA-style structures for payroll tax and benefit efficiency.",
            triggers=["Employer-sponsored health benefit spend", "Workforce size > 10 employees (to validate)"],
            assumptions=["Plan design meets ERISA/IRS requirements", "Third-party administration is feasible"],
            confidence=0.52,
        )
    )

    return credits, expansion_signals, property_signals
