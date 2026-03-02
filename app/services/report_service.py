from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.models import AddressInput, CompanyInput, Report
from app.services.location import assess_locations, load_county_tiers
from app.services.opportunity_engine import build_credit_assessments
from app.services.sector import infer_sector
from app.services.web_research import scrape_website


class ReportService:
    def __init__(self, data_dir: Path, reports_dir: Path):
        self.data_dir = data_dir
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, payload: CompanyInput) -> Report:
        tier_map_path = self.data_dir / "ga_county_tiers.json"
        tier_map = load_county_tiers(tier_map_path)

        web = scrape_website(str(payload.website) if payload.website else None)
        sector = infer_sector(web.text, payload.notes)
        input_addresses = payload.addresses
        if not input_addresses and web.discovered_addresses:
            input_addresses = [AddressInput(raw=addr) for addr in web.discovered_addresses]
            web.source_log.append(
                {
                    "source": str(payload.website) if payload.website else "n/a",
                    "type": "autofill",
                    "detail": f"Used {len(input_addresses)} addresses auto-detected from website content",
                }
            )
        elif not input_addresses:
            web.source_log.append(
                {
                    "source": str(payload.website) if payload.website else "n/a",
                    "type": "autofill",
                    "detail": "No addresses were entered and none were detected from website content",
                }
            )

        locations = assess_locations(input_addresses, tier_map)
        credits, expansion_signals, property_signals = build_credit_assessments(
            sector=sector,
            locations=locations,
            research_text=web.text,
            notes=payload.notes,
        )

        narrative = {
            "ga_jtc_intro": (
                "The GA JTC offers a dollar-for-dollar reduction of state income tax liability when creating new jobs "
                "in certain areas. Counties are designated Tier 1 through Tier 4, with Military Zones, Less Developed "
                "Census Tracts (LDCT), and Opportunity Zones often supporting lower thresholds and larger benefits."
            ),
            "ga_jtc_note": (
                f"{payload.company_name} Georgia locations with corresponding tier designations and estimated credit "
                "benefits are shown below. Final thresholds may be NAICS dependent and should be validated."
            ),
            "retraining_intro": (
                "The GA Retraining Tax Credit can provide up to 50% of eligible retraining costs, including wages of "
                "trainees, up to $1,250 per employee per year."
            ),
            "retraining_context": (
                f"There are many software systems and equipment platforms in the {sector.sector.lower()} space that "
                f"could qualify. If {payload.company_name} implemented new systems or equipment and retrained existing "
                "employees, there may be meaningful savings available."
            ),
            "rd_intro": (
                "Federal and Georgia R&D credits can provide tax relief on qualified technical activities, typically "
                "as a percentage of qualified research spend including eligible wages."
            ),
            "rd_examples_intro": f"Examples of how {payload.company_name} may qualify include:",
            "costseg_intro": (
                f"{payload.company_name} may be able to reduce taxes through cost segregation by accelerating "
                "depreciation on newly constructed, expanded, or purchased facilities."
            ),
            "costseg_detail": (
                "For operations-heavy facilities, portions such as site improvements, electrical systems, specialized "
                "power, fabrication areas, and certain interior improvements may qualify for shorter depreciation lives."
            ),
            "costseg_bonus": (
                "A substantial portion of commercial building basis (net of land) may be accelerated in year one, "
                "subject to current bonus depreciation rules and project facts."
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
