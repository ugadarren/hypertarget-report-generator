from __future__ import annotations

import re
from typing import Any

from app.data.industry_profiles import INDUSTRY_KEYWORDS, SECTOR_DETAILS
from app.models import SectorProfile


SECTOR_FAMILIES = {
    "electrical_contracting": "construction_trades",
    "construction": "construction_trades",
    "manufacturing": "industrial_manufacturing",
    "automotive": "industrial_manufacturing",
    "food_processing": "industrial_manufacturing",
    "energy_utilities": "industrial_infrastructure",
    "telecommunications": "industrial_infrastructure",
    "healthcare": "healthcare_life_sciences",
    "software": "software_technology",
    "logistics": "logistics_distribution",
    "staffing_recruiting": "business_services",
}


def sector_family(sector_key: str) -> str:
    return SECTOR_FAMILIES.get(sector_key, "other")


def keyword_sector_scores(website_text: str, snippets: list[str] | None = None) -> list[tuple[str, int]]:
    haystack = f"{(website_text or '').lower()} {' '.join(snippets or []).lower()}"
    scores: dict[str, int] = {}
    for sector_key, keywords in INDUSTRY_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            needle = keyword.lower().strip()
            if not needle:
                continue
            if needle in haystack:
                # Longer, more specific phrases should outweigh generic single-word hits.
                score += 3 if " " in needle or len(needle) >= 10 else 2
        scores[sector_key] = score
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def sector_candidates(website_text: str, snippets: list[str] | None = None) -> list[dict[str, Any]]:
    ranked = keyword_sector_scores(website_text, snippets)
    candidates: list[dict[str, Any]] = []
    for sector_key, score in ranked:
        candidates.append(
            {
                "sector_key": sector_key,
                "sector_label": str(SECTOR_DETAILS.get(sector_key, {}).get("label", sector_key)),
                "family": sector_family(sector_key),
                "score": score,
            }
        )
    return candidates


def sector_needs_review(candidates: list[dict[str, Any]]) -> bool:
    if not candidates:
        return True
    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None
    top_score = int(top.get("score", 0) or 0)
    runner_score = int(runner_up.get("score", 0) or 0) if runner_up else 0
    if top_score < 6:
        return True
    if runner_up and top_score - runner_score <= 2:
        return True
    return False


def _default_retraining_rows(software: list[str], equipment: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    soft_chunks = [software[i : i + 4] for i in range(0, min(len(software), 12), 4)]
    equip_chunks = [equipment[i : i + 4] for i in range(0, min(len(equipment), 12), 4)]

    software_labels = [
        "Industry Management Software",
        "Operations / ERP Software",
        "Analytics / Workflow Software",
    ]
    equipment_labels = [
        "Operational Equipment",
        "Testing / Diagnostic Equipment",
        "Production / Field Equipment",
    ]

    for idx, chunk in enumerate(soft_chunks):
        rows.append(
            {
                "type": "Software",
                "category": software_labels[min(idx, len(software_labels) - 1)],
                "applicable_programs": chunk,
            }
        )
    for idx, chunk in enumerate(equip_chunks):
        rows.append(
            {
                "type": "Equipment",
                "category": equipment_labels[min(idx, len(equipment_labels) - 1)],
                "applicable_programs": chunk,
            }
        )
    return rows


def _default_rd_rows(rd_examples: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for idx, start in enumerate(range(0, min(len(rd_examples), 8), 2)):
        chunk = rd_examples[start : start + 2]
        if not chunk:
            continue
        rows.append(
            {
                "category": f"Potential R&D Activity Group {idx + 1}",
                "activities": chunk,
            }
        )
    return rows


def _default_rd_feasibility(sector_key: str) -> tuple[str, float]:
    likely_keys = {"manufacturing", "software", "automotive", "food_processing", "telecommunications"}
    lower_keys = {"logistics", "staffing_recruiting"}
    if sector_key in likely_keys:
        return "likely", 0.8
    if sector_key in lower_keys:
        return "possible", 0.45
    return "possible", 0.6


def _resolve_sector_key_from_input(sector_input: str) -> str:
    normalized = sector_input.strip().lower()
    if not normalized:
        return "software"

    # Direct key match first.
    for key in SECTOR_DETAILS.keys():
        if normalized == key.lower():
            return key

    # Label and alias matching.
    for key, details in SECTOR_DETAILS.items():
        label = str(details.get("label", "")).lower()
        if normalized == label or normalized in label or label in normalized:
            return key

    # Keyword overlap against configured sector keywords.
    best_key = "software"
    best_score = 0
    tokens = set(normalized.replace("/", " ").replace("&", " ").split())
    for key, keywords in INDUSTRY_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            kw = keyword.lower()
            if kw in normalized:
                score += 2
            if any(token and token in kw for token in tokens):
                score += 1
        if score > best_score:
            best_key = key
            best_score = score

    return best_key


def _build_sector_profile(sector_key: str, source_text: str, company_name: str | None = None) -> SectorProfile:
    details = SECTOR_DETAILS.get(sector_key, SECTOR_DETAILS["software"])
    software = list(details.get("software", []))[:12]
    equipment = list(details.get("equipment", []))[:12]
    retraining_rows = details.get("rtc_rows") or _default_retraining_rows(software, equipment)
    rd_examples = details.get("rd_examples", [])
    rd_feasibility, rd_confidence = _default_rd_feasibility(sector_key)
    company_label = company_name or "This company"
    company_description = f"{company_label} is being evaluated under the sector: {details['label']}."
    sector_summary = f"Sector logic source: {source_text}."
    return SectorProfile(
        sector_key=sector_key,
        sector=details["label"],
        sector_family=sector_family(sector_key),
        company_description=company_description,
        sector_summary=sector_summary,
        rd_focus_examples=rd_examples,
        rd_feasibility=rd_feasibility,
        rd_confidence=rd_confidence,
        rd_rationale=f"Industry-based estimate for {details['label']}.",
        rd_rows=_default_rd_rows(rd_examples),
        investment_credit_applicable=_investment_credit_applicable(sector_key, details["label"]),
        retraining_rows=retraining_rows,
        software_systems=software,
        equipment=equipment,
        evidence=[source_text, f"Resolved sector key: {sector_key}"],
    )


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _is_noise_snippet(text: str) -> bool:
    lowered = text.lower()
    noise_terms = [
        "skip to content",
        "toggle navigation",
        "privacy policy",
        "search jobs",
        "home employers services",
    ]
    return any(term in lowered for term in noise_terms)


def _pick_description_sentence(snippets: list[str] | None, website_text: str) -> str | None:
    candidates: list[str] = []
    for snippet in snippets or []:
        cleaned = _clean_text(snippet)
        if 50 <= len(cleaned) <= 280 and not _is_noise_snippet(cleaned):
            candidates.append(cleaned)

    if not candidates:
        # Fallback to parsing website text into sentence-like chunks.
        chunks = re.split(r"[.!?]", website_text or "")
        for chunk in chunks:
            cleaned = _clean_text(chunk)
            if 60 <= len(cleaned) <= 260 and not _is_noise_snippet(cleaned):
                candidates.append(cleaned)
            if len(candidates) >= 6:
                break

    preferred_verbs = ["provides", "offers", "delivers", "specializes", "develops", "manufactures", "operates"]
    for candidate in candidates:
        lowered = candidate.lower()
        if any(verb in lowered for verb in preferred_verbs):
            return candidate
    return candidates[0] if candidates else None


def _client_ready_description(
    company_name: str | None,
    sector_label: str,
    snippets: list[str] | None,
    website_text: str,
) -> str:
    name = company_name or "The company"
    primary = _pick_description_sentence(snippets, website_text)
    if primary:
        primary = primary.rstrip(".")
        if name.lower() not in primary.lower():
            return f"{name} is a {sector_label.lower()} company. {primary}."
        return f"{primary}."
    return f"{name} is a {sector_label.lower()} company operating in this industry with services and capabilities that should be validated in analyst review."


def _investment_credit_applicable(sector_key: str, sector_input: str) -> bool:
    # User policy: only manufacturing or telecommunications should default to applicable.
    normalized = sector_input.strip().lower()
    if sector_key in {"manufacturing", "telecommunications"}:
        return True
    if "manufactur" in normalized or "telecom" in normalized:
        return True
    return False


def resolve_sector_from_input(sector_input: str, company_name: str | None = None) -> SectorProfile:
    sector_key = _resolve_sector_key_from_input(sector_input)
    profile = _build_sector_profile(
        sector_key=sector_key,
        source_text=f"Manual sector input: {sector_input}",
        company_name=company_name,
    )
    profile.sector_summary = "Sector logic in this report is based on your manual industry input."
    return profile


def infer_sector_from_text(
    website_text: str,
    snippets: list[str] | None = None,
    company_name: str | None = None,
) -> SectorProfile:
    haystack = f"{(website_text or '').lower()} {' '.join(snippets or []).lower()}"
    if not haystack.strip():
        profile = _build_sector_profile("software", "No website text available; defaulted sector.", company_name=company_name)
        profile.sector_candidates = []
        profile.sector_needs_review = True
        profile.company_description = _client_ready_description(
            company_name=company_name,
            sector_label=profile.sector,
            snippets=snippets,
            website_text=website_text,
        )
        profile.sector_summary = "Industry classification could not be inferred from website text and should be manually reviewed."
        return profile

    candidates = sector_candidates(website_text, snippets)
    best = candidates[0] if candidates else {"sector_key": "software", "score": 0}
    best_key = str(best.get("sector_key", "software"))
    best_score = int(best.get("score", 0) or 0)
    source_text = f"Keyword-based website sector inference (score={best_score})"
    profile = _build_sector_profile(best_key, source_text, company_name=company_name)
    profile.company_description = _client_ready_description(
        company_name=company_name,
        sector_label=profile.sector,
        snippets=snippets,
        website_text=website_text,
    )
    if sector_needs_review(candidates):
        profile.sector_summary = "Industry classification is ambiguous based on website content and should be manually reviewed."
        profile.sector_needs_review = True
    else:
        profile.sector_summary = "Industry classification was inferred from website content and should be analyst-reviewed."
        profile.sector_needs_review = False
    profile.sector_candidates = candidates[:3]
    profile.evidence.extend(
        [
            f"{item['sector_key']}:{item['score']}:{item['family']}"
            for item in candidates[:3]
        ]
    )
    return profile
