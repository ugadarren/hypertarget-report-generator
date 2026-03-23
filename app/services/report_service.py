from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.models import CompanyInput, Report
from app.services.llm_enrichment import detect_sector_with_llm, enrich_sector_profile, extract_contacts_with_llm
from app.services.location import assess_locations, load_county_tiers, load_county_tier_history, load_credit_policy
from app.services.opportunity_engine import build_credit_assessments
from app.services.policy_meta import load_policy_versions
from app.services.sector import infer_sector_from_text, resolve_sector_from_input
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


class ReportService:
    def __init__(self, data_dir: Path, reports_dir: Path):
        self.data_dir = data_dir
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

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
            detected, detect_log = detect_sector_with_llm(
                company_name=payload.company_name,
                website=str(payload.website) if payload.website else None,
                snippets=web.snippets,
                research_text=web.text,
            )
            web.source_log.append(detect_log)
            if detected:
                sector = resolve_sector_from_input(
                    sector_input=detected["sector_key"],
                    company_name=payload.company_name,
                )
                sector.evidence.append(f"LLM detection reason: {detected.get('reason', '')}")
            else:
                sector = infer_sector_from_text(
                    website_text=web.text,
                    snippets=web.snippets,
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

    def _build_narrative(self, payload: CompanyInput, sector, credits, expansion_signals: list[str]) -> dict:
        ga_retraining = next((c for c in credits if c.code == "GA_RETRAINING"), None)
        federal_rd = next((c for c in credits if c.code == "FEDERAL_RD"), None)
        ga_investment = next((c for c in credits if c.code == "GA_INVESTMENT"), None)
        retraining_rows = _normalized_retraining_rows(sector)
        rd_rows = _normalized_rd_rows(sector)

        return {
            "sector_title": sector.sector,
            "company_description": sector.company_description
            or f"{payload.company_name} appears to operate in the {sector.sector.lower()} industry.",
            "sector_summary": sector.sector_summary
            or "Industry classification is based on website language and should be analyst-reviewed.",
            "ga_jtc_intro": (
                "The Georgia Job Tax Credit rewards businesses that create new full-time jobs in the state by "
                "providing a tax credit ranging from $1,250 to $4,000 per job per year for up to five years, "
                "depending on the county. For business owners, this can significantly reduce Georgia income tax "
                "liability and, in some cases, offset payroll withholding, lowering the cost of expanding their workforce."
            ),
            "ga_jtc_note": (
                f"{payload.company_name}'s Georgia location(s) are listed below with their corresponding tier "
                "designations and estimated credit benefits."
            ),
            "retraining_intro": (
                "The Georgia RTC provides businesses with a tax credit of up to 50% of eligible training costs for "
                "retraining existing employees to upgrade skills, adopt new technologies, or improve productivity. "
                "For business owners, it reduces the cost of workforce development, by up to $1,250 per employee per "
                "year, while helping employees stay competitive and efficient as the company grows."
            ),
            "retraining_intro_lead": (
                "The Georgia RTC provides businesses with a tax credit of up to 50% of eligible training costs for "
                "retraining existing employees to upgrade skills, adopt new technologies, or improve productivity. "
                "For business owners, it reduces the cost of workforce development, "
            ),
            "retraining_intro_emphasis": "by up to $1,250 per employee per year",
            "retraining_intro_tail": (
                ", while helping employees stay competitive and efficient as the company grows."
            ),
            "retraining_context": (
                "Below are some examples of software systems and equipment relevant to your industry that may qualify "
                "for the GA RTC."
            ),
            "retraining_feasibility": (ga_retraining.status if ga_retraining else "possible").title(),
            "retraining_confidence_pct": round(((ga_retraining.confidence if ga_retraining else 0.6) or 0) * 100),
            "retraining_rationale": (ga_retraining.rationale if ga_retraining else "Retraining potential estimated from sector systems and equipment."),
            "rd_intro": (
                "The Federal and Georgia R&D Tax Credits reward businesses that invest in developing or improving "
                "products, processes, or software, typically providing a combined tax benefit of roughly 10-20% of "
                "qualified research expenses. For business owners, this can significantly reduce federal and state tax "
                "liability-or offset payroll taxes in some cases-freeing up cash to reinvest in innovation, hiring, and growth."
            ),
            "rd_examples_intro": (
                f"Below are potential qualifying activities for {payload.company_name} that are specific to the "
                "industry in which you operate."
            ),
            "rd_feasibility": (federal_rd.status if federal_rd else sector.rd_feasibility).title(),
            "rd_confidence_pct": round(((federal_rd.confidence if federal_rd else sector.rd_confidence) or 0) * 100),
            "rd_rationale": (federal_rd.rationale if federal_rd else sector.rd_rationale) or "",
            "rd_rows": rd_rows,
            "rd_focus_examples": sector.rd_focus_examples,
            "retraining_rows": retraining_rows,
            "investment_status": ga_investment.status if ga_investment else "possible",
            "investment_rationale": ga_investment.rationale if ga_investment else "",
            "investment_confidence_pct": round(((ga_investment.confidence if ga_investment else 0.5) or 0) * 100),
            "investment_intro": (
                "The Georgia Investment Tax Credit rewards manufacturing, warehousing, and telecom businesses that "
                "invest in new equipment or facilities by providing a state tax credit of 1% to 8% of qualified "
                "capital investment, depending on the county and industry. For business owners, this credit helps "
                "offset Georgia income tax liability and lowers the overall cost of expanding operations, upgrading "
                "equipment, or increasing production capacity."
            ),
            "investment_note": (
                f"The County, tier, and potential investment credit percentage are listed below for "
                f"{payload.company_name}'s location(s)."
            ),
            "investment_signals_summary": _investment_client_summary(
                payload.company_name,
                expansion_signals,
                ga_investment.status if ga_investment else "possible",
            ),
            "costseg_intro": (
                "Cost Segregation is a tax strategy that allows commercial property owners to accelerate depreciation "
                "on certain building components, often enabling 20-40% of the property's value to be depreciated "
                "within the first 5-15 years instead of 39 years. For business owners, this acceleration can create "
                "significant upfront tax savings and improved cash flow that can be reinvested back into the business."
            ),
            "costseg_note": (
                "If you've purchased, built, or renovated commercial real estate in the last five years, a Cost "
                "Segregation study may allow you to accelerate depreciation and unlock significant tax savings. It's "
                "often worth taking a look to see how much additional cash flow you could generate by reclassifying "
                "parts of the property into shorter depreciation schedules."
            ),
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
        narrative = self._build_narrative(payload, sector, credits, expansion_signals)
        narrative["contact_intro"] = (
            "The contacts below were identified from publicly available website content and may include owner, "
            "founder, executive, or general decision-maker signals. Please verify title and email accuracy before outreach."
        )
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
