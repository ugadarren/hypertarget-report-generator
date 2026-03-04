from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.models import CompanyInput, Report
from app.services.llm_enrichment import detect_sector_with_llm, enrich_sector_profile
from app.services.location import assess_locations, load_county_tiers
from app.services.opportunity_engine import build_credit_assessments
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

    def generate(self, payload: CompanyInput) -> Report:
        tier_map_path = self.data_dir / "ga_county_tiers.json"
        tier_map = load_county_tiers(tier_map_path)

        web = scrape_website(str(payload.website) if payload.website else None)
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
        input_addresses = payload.addresses
        if not input_addresses:
            web.source_log.append(
                {
                    "source": str(payload.website) if payload.website else "n/a",
                    "type": "manual_address_required",
                    "detail": "No addresses were entered. Address processing is manual-only.",
                }
            )

        locations = assess_locations(input_addresses, tier_map)
        credits, expansion_signals, property_signals = build_credit_assessments(
            sector=sector,
            locations=locations,
            research_text=web.text,
            notes=payload.notes,
        )
        ga_retraining = next((c for c in credits if c.code == "GA_RETRAINING"), None)
        federal_rd = next((c for c in credits if c.code == "FEDERAL_RD"), None)
        ga_investment = next((c for c in credits if c.code == "GA_INVESTMENT"), None)
        retraining_rows = _normalized_retraining_rows(sector)
        rd_rows = _normalized_rd_rows(sector)

        narrative = {
            "sector_title": sector.sector,
            "company_description": sector.company_description
            or f"{payload.company_name} appears to operate in the {sector.sector.lower()} industry.",
            "sector_summary": sector.sector_summary
            or "Industry classification is based on website language and should be analyst-reviewed.",
            "ga_jtc_intro": (
                "The GA JTC offers a dollar-for-dollar reduction of state income tax liability when creating new jobs "
                "in certain areas. Counties are designated Tier 1 through Tier 4, with Military Zones, Less Developed "
                "Census Tracts (LDCT), and Opportunity Zones often supporting lower thresholds and larger benefits."
            ),
            "ga_jtc_note": (
                f"{payload.company_name} Georgia locations with corresponding tier designations and estimated credit "
                "benefits are shown below based on county tier and special designations."
            ),
            "retraining_intro": (
                "The GA Retraining Tax Credit can provide up to 50% of eligible retraining costs, including wages of "
                "trainees, up to $1,250 per employee per year."
            ),
            "retraining_context": (
                "Below are industry-specific software and equipment categories with applicable programs. "
                "If implemented and employees were retrained to use them, these categories may support GA RTC claims."
            ),
            "retraining_feasibility": (ga_retraining.status if ga_retraining else "possible").title(),
            "retraining_confidence_pct": round(((ga_retraining.confidence if ga_retraining else 0.6) or 0) * 100),
            "retraining_rationale": (ga_retraining.rationale if ga_retraining else "Retraining potential estimated from sector systems and equipment."),
            "rd_intro": (
                "Federal and Georgia R&D credits can provide tax relief on qualified technical activities, typically "
                "as a percentage of qualified research spend including eligible wages."
            ),
            "rd_examples_intro": f"R&D feasibility and potential qualifying activities for {payload.company_name}:",
            "rd_feasibility": (federal_rd.status if federal_rd else sector.rd_feasibility).title(),
            "rd_confidence_pct": round(((federal_rd.confidence if federal_rd else sector.rd_confidence) or 0) * 100),
            "rd_rationale": (federal_rd.rationale if federal_rd else sector.rd_rationale) or "",
            "rd_rows": rd_rows,
            "rd_focus_examples": sector.rd_focus_examples,
            "retraining_rows": retraining_rows,
            "investment_status": ga_investment.status if ga_investment else "possible",
            "investment_rationale": ga_investment.rationale if ga_investment else "",
            "investment_confidence_pct": round(((ga_investment.confidence if ga_investment else 0.5) or 0) * 100),
            "investment_signals_summary": _investment_client_summary(
                payload.company_name,
                expansion_signals,
                ga_investment.status if ga_investment else "possible",
            ),
        }

        report = Report(
            id=uuid4().hex[:12],
            created_at=datetime.now(timezone.utc),
            company_name=payload.company_name,
            website=str(payload.website) if payload.website else None,
            sector_profile=sector,
            locations=locations,
            credits=credits,
            expansion_signals=expansion_signals,
            property_signals=property_signals,
            narrative=narrative,
            source_log=web.source_log,
        )

        output_path = self.reports_dir / f"{report.id}.json"
        output_path.write_text(report.model_dump_json(indent=2))
        return report

    def get_report(self, report_id: str) -> Report | None:
        path = self.reports_dir / f"{report_id}.json"
        if not path.exists():
            return None
        return Report(**json.loads(path.read_text()))
