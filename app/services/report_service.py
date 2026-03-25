from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import get_settings
from app.models import CompanyInput, Report
from app.services.llm_enrichment import detect_sector_with_llm, enrich_sector_profile, extract_contacts_with_llm
from app.services.data_validation import validate_data_files
from app.services.location import assess_locations, load_county_tiers, load_county_tier_history, load_credit_policy
from app.services.opportunity_engine import build_credit_assessments
from app.services.policy_meta import load_policy_versions
from app.services.report_copy import apply_report_copy_template, load_report_copy
from app.services.sector import infer_sector_from_text, resolve_sector_from_input, sector_candidates, sector_family
from app.services.web_research import scrape_website


def _format_signal_label(signal: str) -> str:
    text = str(signal or "").strip()
    if text.startswith("pattern:"):
        return "Website language suggests expansion or facility investment activity."
    return text.replace("_", " ").strip()


def _investment_client_summary(company_name: str, signals: list[str], status: str) -> str:
    cleaned = [_format_signal_label(s) for s in signals if str(s).strip()]
    unique: list[str] = []
    for item in cleaned:
        if item not in unique:
            unique.append(item)
    if unique:
        top = ", ".join(unique[:3])
        return (
            f"We identified potential investment indicators for {company_name}, including: {top}. "
            "These signals may support further ITC evaluation, subject to project and placement-in-service details."
        )
    return (
        "We were unable to find relevant information indicating recent expansion, building purchases, new "
        "construction, or capital-equipment investment that would suggest ITC eligibility at this time."
    )


def _normalized_retraining_rows(sector) -> list[dict]:
    software_rows = [row for row in (sector.retraining_rows or []) if str(row.get("type", "")).lower() == "software"]
    equipment_rows = [row for row in (sector.retraining_rows or []) if str(row.get("type", "")).lower() == "equipment"]

    def _fallback_rows(row_type: str, categories: list[str], programs: list[str]) -> list[dict]:
        rows: list[dict] = []
        for idx in range(2):
            chunk = programs[idx * 4 : (idx + 1) * 4]
            if not chunk:
                continue
            rows.append(
                {
                    "type": row_type,
                    "category": categories[idx],
                    "applicable_programs": chunk,
                }
            )
        return rows

    if len(software_rows) < 2:
        software_rows = _fallback_rows(
            "Software",
            ["Operations Software", "Workflow / Analytics Software"],
            list(sector.software_systems or []),
        )
    if len(equipment_rows) < 2:
        equipment_rows = _fallback_rows(
            "Equipment",
            ["Operational Equipment", "Testing / Field Equipment"],
            list(sector.equipment or []),
        )

    return software_rows[:2] + equipment_rows[:2]


def _normalized_rd_rows(sector) -> list[dict]:
    rows = list(sector.rd_rows or [])
    if len(rows) >= 3:
        return rows[:4]

    examples = list(sector.rd_focus_examples or [])
    generated: list[dict] = []
    while len(generated) < 4 and examples:
        idx = len(generated)
        chunk = examples[idx * 2 : (idx + 1) * 2] or examples[idx : idx + 1]
        if not chunk:
            break
        generated.append(
            {
                "category": f"Potential R&D Activity Group {idx + 1}",
                "activities": chunk[:5],
            }
        )
        if len(examples) <= (idx + 1) * 2:
            break

    if len(generated) < 3:
        defaults = [
            "Process optimization experiments to improve quality, throughput, or reliability.",
            "Prototype, pilot, or technical validation work to resolve uncertainty.",
            "System integration and technical testing of new tools, methods, or workflows.",
        ]
        while len(generated) < 3:
            i = len(generated)
            generated.append(
                {
                    "category": f"Potential R&D Activity Group {i + 1}",
                    "activities": [defaults[i]],
                }
            )

    return generated[:4]


def _build_prior_tier_matrix(locations: list) -> tuple[list[str], list[dict]]:
    year_values: set[int] = set()
    address_maps: list[dict] = []

    for loc in locations or []:
        year_map: dict[str, str] = {}
        for entry in list(loc.tier_history or [])[1:]:
            parts = str(entry).split(":", 1)
            if len(parts) != 2:
                continue
            year = parts[0].strip()
            tier = parts[1].strip()
            if not year.isdigit():
                continue
            year_values.add(int(year))
            year_map[year] = tier
        address_maps.append(
            {
                "address": loc.address,
                "tiers_by_year": year_map,
            }
        )

    years = [str(y) for y in sorted(year_values, reverse=True)]
    rows: list[dict] = []
    for item in address_maps:
        rows.append(
            {
                "address": item["address"],
                "tiers": [item["tiers_by_year"].get(year, "-") for year in years],
            }
        )
    return years, rows


class ReportService:
    def __init__(self, data_dir: Path, reports_dir: Path):
        self.data_dir = data_dir
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        validate_data_files(self.data_dir)

    def _load_tier_map(self) -> dict[str, str]:
        tier_map_path = self.data_dir / "ga_county_tiers.json"
        return load_county_tiers(tier_map_path)

    def _load_tier_history_map(self) -> dict[str, dict[str, str]]:
        history_path = self.data_dir / "ga_county_tiers_by_year.json"
        return load_county_tier_history(history_path)

    def _load_credit_policy(self) -> dict:
        policy_path = self.data_dir / "ga_credit_policy.json"
        return load_credit_policy(policy_path)

    def _load_policy_versions(self) -> dict:
        versions_path = self.data_dir / "policy_versions.json"
        return load_policy_versions(versions_path)

    def _load_report_copy(self) -> dict[str, str]:
        copy_path = self.data_dir / "report_copy.json"
        return load_report_copy(copy_path)

    def _collect_web_context(self, payload: CompanyInput):
        web = scrape_website(str(payload.website) if payload.website else None)
        return web

    def _resolve_sector(self, payload: CompanyInput, web):
        if payload.sector and payload.sector.strip():
            sector = resolve_sector_from_input(sector_input=payload.sector, company_name=payload.company_name)
            web.source_log.append(
                {
                    "source": "sector",
                    "type": "manual",
                    "detail": f"Used manually entered sector: {payload.sector}",
                }
            )
        else:
            candidates = sector_candidates(web.text, web.snippets, web.weighted_texts)
            detected, detect_log = detect_sector_with_llm(
                company_name=payload.company_name,
                website=str(payload.website) if payload.website else None,
                snippets=web.snippets,
                weighted_texts=web.weighted_texts,
                research_text=web.text,
            )
            web.source_log.append(detect_log)
            if detected:
                sector = resolve_sector_from_input(
                    sector_input=detected["sector_key"],
                    company_name=payload.company_name,
                )
                sector.sector_family = sector_family(detected["sector_key"])
                sector.sector_candidates = candidates[:3]
                sector.sector_needs_review = False
                sector.evidence.append(f"LLM detection reason: {detected.get('reason', '')}")
            else:
                sector = infer_sector_from_text(
                    website_text=web.text,
                    snippets=web.snippets,
                    weighted_texts=web.weighted_texts,
                    company_name=payload.company_name,
                )
                web.source_log.append(
                    {
                        "source": "sector",
                        "type": "fallback",
                        "detail": f"Applied keyword-based sector inference: {sector.sector}",
                    }
                )
        return sector

    def _enrich_sector(self, payload: CompanyInput, web, sector):
        enriched_sector, enrichment_log = enrich_sector_profile(
            company_name=payload.company_name,
            website=str(payload.website) if payload.website else None,
            sector_context=sector.sector,
            base_sector=sector,
            snippets=web.snippets,
            research_text=web.text,
        )
        if enriched_sector:
            sector = enriched_sector
        web.source_log.append(enrichment_log)
        return sector

    def _enrich_contacts(self, payload: CompanyInput, web) -> list[dict]:
        base_contacts = list(web.contact_leads or [])
        settings = get_settings()
        if not settings.enable_llm_contact_enrichment:
            web.source_log.append(
                {
                    "source": "config",
                    "type": "feature_flag",
                    "detail": "GPT contact enrichment disabled by ENABLE_LLM_CONTACT_ENRICHMENT.",
                }
            )
            return base_contacts
        llm_contacts, llm_log = extract_contacts_with_llm(
            company_name=payload.company_name,
            website=str(payload.website) if payload.website else None,
            snippets=web.snippets,
            research_text=web.text,
            deterministic_contacts=base_contacts,
        )
        web.source_log.append(llm_log)
        merged: dict[str, dict] = {}
        for contact in base_contacts + list(llm_contacts or []):
            key = f"{str(contact.get('name') or '').strip().lower()}|{str(contact.get('email') or '').strip().lower()}"
            existing = merged.get(key)
            if not existing or float(contact.get("confidence") or 0) > float(existing.get("confidence") or 0):
                merged[key] = contact
        return sorted(merged.values(), key=lambda c: float(c.get("confidence") or 0), reverse=True)[:8]

    def _resolve_locations(
        self,
        payload: CompanyInput,
        web,
        tier_map: dict[str, str],
        credit_policy: dict,
        tier_history_by_year: dict[str, dict[str, str]],
        reference_year: int | None,
    ):
        input_addresses = payload.addresses
        if not input_addresses:
            web.source_log.append(
                {
                    "source": str(payload.website) if payload.website else "n/a",
                    "type": "manual_address_required",
                    "detail": "No addresses were entered. Address processing is manual-only.",
                }
            )
        return assess_locations(
            input_addresses,
            tier_map,
            credit_policy=credit_policy,
            tier_history_by_year=tier_history_by_year,
            reference_year=reference_year,
        )

    def _assess_credits(self, sector, locations, payload: CompanyInput, web):
        credits, expansion_signals, property_signals = build_credit_assessments(
            sector=sector,
            locations=locations,
            research_text=web.text,
            notes=payload.notes,
        )
        return credits, expansion_signals, property_signals

    def _build_narrative(
        self,
        payload: CompanyInput,
        sector,
        credits,
        expansion_signals: list[str],
        locations,
        report_copy: dict[str, str],
    ) -> dict:
        ga_retraining = next((c for c in credits if c.code == "GA_RETRAINING"), None)
        federal_rd = next((c for c in credits if c.code == "FEDERAL_RD"), None)
        ga_investment = next((c for c in credits if c.code == "GA_INVESTMENT"), None)
        retraining_rows = _normalized_retraining_rows(sector)
        rd_rows = _normalized_rd_rows(sector)
        prior_years, prior_rows = _build_prior_tier_matrix(locations)

        return {
            "sector_title": sector.sector,
            "sector_family": sector.sector_family or "",
            "sector_candidates": sector.sector_candidates,
            "sector_needs_review": sector.sector_needs_review,
            "company_description": sector.company_description
            or f"{payload.company_name} appears to operate in the {sector.sector.lower()} industry.",
            "sector_summary": sector.sector_summary
            or "Industry classification is based on website language and should be analyst-reviewed.",
            "ga_jtc_intro": apply_report_copy_template(report_copy.get("ga_jtc_intro", ""), company_name=payload.company_name),
            "ga_jtc_note": apply_report_copy_template(report_copy.get("ga_jtc_note", ""), company_name=payload.company_name),
            "ga_jtc_prior_years": prior_years,
            "ga_jtc_prior_rows": prior_rows,
            "retraining_intro": apply_report_copy_template(report_copy.get("retraining_intro", ""), company_name=payload.company_name),
            "retraining_intro_lead": apply_report_copy_template(report_copy.get("retraining_intro_lead", ""), company_name=payload.company_name),
            "retraining_intro_emphasis": apply_report_copy_template(report_copy.get("retraining_intro_emphasis", ""), company_name=payload.company_name),
            "retraining_intro_tail": apply_report_copy_template(report_copy.get("retraining_intro_tail", ""), company_name=payload.company_name),
            "retraining_context": apply_report_copy_template(report_copy.get("retraining_context", ""), company_name=payload.company_name),
            "retraining_feasibility": (ga_retraining.status if ga_retraining else "possible").title(),
            "retraining_confidence_pct": round(((ga_retraining.confidence if ga_retraining else 0.6) or 0) * 100),
            "retraining_rationale": (ga_retraining.rationale if ga_retraining else "Retraining potential estimated from sector systems and equipment."),
            "rd_intro": apply_report_copy_template(report_copy.get("rd_intro", ""), company_name=payload.company_name),
            "rd_examples_intro": apply_report_copy_template(report_copy.get("rd_examples_intro", ""), company_name=payload.company_name),
            "rd_feasibility": (federal_rd.status if federal_rd else sector.rd_feasibility).title(),
            "rd_confidence_pct": round(((federal_rd.confidence if federal_rd else sector.rd_confidence) or 0) * 100),
            "rd_rationale": (federal_rd.rationale if federal_rd else sector.rd_rationale) or "",
            "rd_rows": rd_rows,
            "rd_focus_examples": sector.rd_focus_examples,
            "retraining_rows": retraining_rows,
            "investment_status": ga_investment.status if ga_investment else "possible",
            "investment_rationale": ga_investment.rationale if ga_investment else "",
            "investment_confidence_pct": round(((ga_investment.confidence if ga_investment else 0.5) or 0) * 100),
            "investment_intro": apply_report_copy_template(report_copy.get("investment_intro", ""), company_name=payload.company_name),
            "investment_note": apply_report_copy_template(report_copy.get("investment_note", ""), company_name=payload.company_name),
            "investment_signals_summary": _investment_client_summary(
                payload.company_name,
                expansion_signals,
                ga_investment.status if ga_investment else "possible",
            ),
            "costseg_intro": apply_report_copy_template(report_copy.get("costseg_intro", ""), company_name=payload.company_name),
            "costseg_note": apply_report_copy_template(report_copy.get("costseg_note", ""), company_name=payload.company_name),
            "contact_intro": apply_report_copy_template(report_copy.get("contact_intro", ""), company_name=payload.company_name),
        }

    def _build_report(
        self,
        payload: CompanyInput,
        sector,
        locations,
        credits,
        expansion_signals,
        property_signals,
        contact_intelligence,
        narrative: dict,
        source_log: list[dict],
    ) -> Report:
        return Report(
            id=uuid4().hex[:12],
            created_at=datetime.now(timezone.utc),
            company_name=payload.company_name,
            website=str(payload.website) if payload.website else None,
            sector_profile=sector,
            locations=locations,
            credits=credits,
            expansion_signals=expansion_signals,
            property_signals=property_signals,
            contact_intelligence=contact_intelligence,
            narrative=narrative,
            source_log=source_log,
        )

    def _persist_report(self, report: Report) -> None:
        output_path = self.reports_dir / f"{report.id}.json"
        output_path.write_text(report.model_dump_json(indent=2))

    def generate(self, payload: CompanyInput) -> Report:
        tier_map = self._load_tier_map()
        tier_history_map = self._load_tier_history_map()
        credit_policy = self._load_credit_policy()
        policy_versions = self._load_policy_versions()
        report_copy = self._load_report_copy()
        policy_year_value = str(policy_versions.get("policy_year", "")).strip()
        reference_year = int(policy_year_value) if policy_year_value.isdigit() else None
        web = self._collect_web_context(payload)
        sector = self._resolve_sector(payload, web)
        sector = self._enrich_sector(payload, web, sector)
        contact_intelligence = self._enrich_contacts(payload, web)
        locations = self._resolve_locations(
            payload,
            web,
            tier_map,
            credit_policy,
            tier_history_map,
            reference_year,
        )
        credits, expansion_signals, property_signals = self._assess_credits(sector, locations, payload, web)
        narrative = self._build_narrative(payload, sector, credits, expansion_signals, locations, report_copy)
        narrative["policy_year"] = policy_versions.get("policy_year", "unspecified")
        narrative["policy_versions"] = policy_versions
        web.source_log.append(
            {
                "source": "policy",
                "type": "version",
                "detail": (
                    f"Policy year {policy_versions.get('policy_year', 'unspecified')}; "
                    f"County tiers: {policy_versions.get('county_tiers', {}).get('effective_year', 'unspecified')}; "
                    f"Credit policy: {policy_versions.get('credit_policy', {}).get('effective_year', 'unspecified')}"
                ),
            }
        )
        report = self._build_report(
            payload=payload,
            sector=sector,
            locations=locations,
            credits=credits,
            expansion_signals=expansion_signals,
            property_signals=property_signals,
            contact_intelligence=contact_intelligence,
            narrative=narrative,
            source_log=web.source_log,
        )
        self._persist_report(report)
        return report

    def get_report(self, report_id: str) -> Report | None:
        path = self.reports_dir / f"{report_id}.json"
        if not path.exists():
            return None
        return Report(**json.loads(path.read_text()))
