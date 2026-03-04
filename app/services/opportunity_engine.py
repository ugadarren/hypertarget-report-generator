from __future__ import annotations

import re

from app.data.industry_profiles import EXPANSION_KEYWORDS, RD_KEYWORDS
from app.models import CreditAssessment, LocationAssessment, SectorProfile


EXPANSION_PATTERNS = [
    r"new\s+(facility|plant|warehouse|location|headquarters|hq)",
    r"(opened|opening|opens)\s+(a\s+)?new\s+(facility|plant|warehouse|location|office)",
    r"(expanding|expanded|expansion)\s+(operations|facility|footprint|plant|campus)",
    r"ground\s*breaking|groundbreaking",
    r"capital\s+investment",
    r"square\s*foot|sq\.?\s*ft",
]


def _is_rd_core_sector(sector: SectorProfile) -> bool:
    # User policy: confidence should be conservative unless the company is
    # in manufacturing or the physical/computer/engineering sciences space.
    core_keys = {"manufacturing"}
    if sector.sector_key in core_keys:
        return True

    label = (sector.sector or "").lower()
    core_terms = [
        "manufactur",
        "engineering",
        "computer",
        "software",
        "physical science",
        "scientific",
        "research",
    ]
    return any(term in label for term in core_terms)


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
    property_signals: list[str] = []

    ga_tiers = [loc.ga_tier for loc in locations if loc.ga_tier]
    has_target_tier = any(tier in {"1", "2", "3", "4"} for tier in ga_tiers)

    credits: list[CreditAssessment] = []

    retraining_rows = list(sector.retraining_rows or [])
    software_categories = len([r for r in retraining_rows if str(r.get("type", "")).lower() == "software"])
    equipment_categories = len([r for r in retraining_rows if str(r.get("type", "")).lower() == "equipment"])
    retraining_status = "likely" if software_categories >= 2 and equipment_categories >= 2 else "possible"
    retraining_conf = 0.8 if retraining_status == "likely" else 0.62
    retraining_rationale = (
        f"Identified {software_categories} software and {equipment_categories} equipment retraining categories "
        "relevant to operations."
    )
    credits.append(
        CreditAssessment(
            code="GA_RETRAINING",
            name="Georgia Retraining Tax Credit",
            status=retraining_status,
            rationale=retraining_rationale,
            triggers=sector.software_systems[:4],
            assumptions=["Company has Georgia payroll tax liability", "Training expenses are documented"],
            confidence=retraining_conf,
        )
    )

    rd_status = sector.rd_feasibility
    if rd_signals and rd_status == "possible":
        rd_status = "likely"
    rd_rationale = sector.rd_rationale or (
        "Website/notes include technical improvement and development signals."
        if rd_signals
        else f"No explicit R&D keywords found; potential based on {sector.sector.lower()} operations."
    )
    rd_confidence = max(0.0, min(1.0, float(sector.rd_confidence)))
    if rd_signals:
        rd_confidence = max(rd_confidence, 0.68)

    # Conservative guardrail for non-core R&D sectors.
    # This avoids aggressive "likely/high confidence" outcomes for industries
    # like logistics, staffing, and other service-heavy profiles.
    if not _is_rd_core_sector(sector):
        if rd_status == "likely":
            rd_status = "possible"
        # Cap confidence unless there is unusually strong technical signal density.
        if len(rd_signals) >= 5:
            rd_confidence = min(rd_confidence, 0.55)
        elif len(rd_signals) >= 2:
            rd_confidence = min(rd_confidence, 0.49)
        else:
            rd_confidence = min(rd_confidence, 0.42)
        rd_rationale = (
            f"{rd_rationale} Confidence is conservatively adjusted because {sector.sector} "
            "is not typically a core R&D-intensive sector without clear technical-science evidence."
        )

    credits.append(
        CreditAssessment(
            code="FEDERAL_RD",
            name="Federal R&D Tax Credit",
            status=rd_status,
            rationale=rd_rationale,
            triggers=rd_signals[:6] or sector.rd_focus_examples[:3] or ["Potential iterative technical activities"],
            assumptions=["Technical uncertainty and experimentation occurred", "Qualified wage/supply data available"],
            confidence=rd_confidence,
        )
    )

    credits.append(
        CreditAssessment(
            code="GA_RD",
            name="Georgia R&D Tax Credit",
            status=rd_status,
            rationale="Georgia credit follows similar qualified research principles with state-specific base calculations.",
            triggers=rd_signals[:6] or sector.rd_focus_examples[:3] or ["Potential qualifying development activity in operations"],
            assumptions=["Georgia nexus and qualified expenses", "Prior year spend available for base-period computation"],
            confidence=max(0.0, min(1.0, rd_confidence - 0.04)),
        )
    )

    if not sector.investment_credit_applicable:
        investment_status = "unlikely"
        investment_rationale = (
            "Georgia Investment Tax Credit is generally targeted to qualifying manufacturing/telecommunications "
            "investments; detected industry does not appear to meet this program scope."
        )
        investment_triggers = [f"Detected industry: {sector.sector}"]
        investment_confidence = 0.9
    else:
        investment_status = "likely" if expansion_signals and has_target_tier else "possible"
        investment_rationale = (
            "Expansion/capital signals combined with county tier may support Georgia investment incentives."
        )
        investment_triggers = expansion_signals[:5] or ["Need verification of recent capital investment in GA"]
        investment_confidence = 0.71 if expansion_signals and has_target_tier else 0.48

    credits.append(
        CreditAssessment(
            code="GA_INVESTMENT",
            name="Georgia Investment Tax Credit",
            status=investment_status,
            rationale=investment_rationale,
            triggers=investment_triggers,
            assumptions=["Qualified investment property placed in service", "Tier/location thresholds satisfied"],
            confidence=investment_confidence,
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
