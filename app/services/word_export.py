from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from app.models import Report


CONTENT_TYPES_XML = """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
  <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>
  <Default Extension=\"xml\" ContentType=\"application/xml\"/>
  <Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>
</Types>
"""

ROOT_RELS_XML = """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>
</Relationships>
"""

DOC_RELS_XML = """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>
"""


def _p(text: str, bold: bool = False) -> str:
    text = escape(text)
    run_pr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return (
        "<w:p><w:r>"
        f"{run_pr}<w:t xml:space=\"preserve\">{text}</w:t>"
        "</w:r></w:p>"
    )


def _row(cells: list[str], header: bool = False) -> str:
    parts = []
    for cell in cells:
        parts.append("<w:tc><w:p><w:r>" + ("<w:rPr><w:b/></w:rPr>" if header else "") + f"<w:t>{escape(cell)}</w:t></w:r></w:p></w:tc>")
    return "<w:tr>" + "".join(parts) + "</w:tr>"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    body = [_row(headers, header=True)] + [_row(r) for r in rows]
    return "<w:tbl>" + "".join(body) + "</w:tbl>"


class WordExportService:
    def __init__(self, exports_dir: Path):
        self.exports_dir = exports_dir
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def _safe_slug(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
        return cleaned[:80] or "report"

    def _build_document_xml(self, report: Report) -> str:
        parts: list[str] = []

        parts.append(_p(f"{report.company_name} Tax Incentive Summary", bold=True))
        parts.append(_p("Company Sector and Industry Analysis", bold=True))
        parts.append(_p(f"Detected Sector: {report.narrative.get('sector_title', report.sector_profile.sector)}"))
        parts.append(_p(f"Company Description: {report.narrative.get('company_description', '')}"))
        parts.append(_p(report.narrative.get("sector_summary", "")))

        parts.append(_p("GA Job Tax Credit", bold=True))
        parts.append(_p(report.narrative.get("ga_jtc_intro", "")))
        parts.append(_p(report.narrative.get("ga_jtc_note", "")))

        headers = [
            "GA Location",
            "County",
            "County Tier",
            "Special Designation",
            "Job Creation Threshold",
            "Per Job Credit Amount",
        ]
        rows = []
        for loc in report.locations:
            rows.append([
                loc.address,
                loc.county or "-",
                f"Tier {loc.ga_tier}" if loc.ga_tier else "Unmapped",
                loc.special_designation or "None",
                loc.job_creation_threshold or "Unavailable",
                loc.per_job_credit_amount or "Unavailable",
            ])
        parts.append(_table(headers, rows))

        parts.append(_p("Georgia Retraining Tax Credit", bold=True))
        parts.append(_p(report.narrative.get("retraining_intro", "")))
        parts.append(_p(report.narrative.get("retraining_context", "")))
        retraining_summary_headers = ["Retraining Feasibility", "Confidence Score", "Rationale"]
        retraining_summary_rows = [
            [
                str(report.narrative.get("retraining_feasibility", "Possible")),
                f"{report.narrative.get('retraining_confidence_pct', 0)}%",
                str(report.narrative.get("retraining_rationale", "")),
            ]
        ]
        parts.append(_table(retraining_summary_headers, retraining_summary_rows))
        tech_headers = [
            "Type",
            "Category",
            "Applicable Programs / Systems",
        ]
        tech_rows: list[list[str]] = []
        for item in report.narrative.get("retraining_rows", []):
            tech_rows.append(
                [
                    item.get("type", ""),
                    item.get("category", ""),
                    ", ".join(item.get("applicable_programs", [])),
                ]
            )
        parts.append(_table(tech_headers, tech_rows))

        parts.append(_p("Federal & State Research and Development Credit", bold=True))
        parts.append(_p(report.narrative.get("rd_intro", "")))
        parts.append(_p(report.narrative.get("rd_examples_intro", "")))
        rd_summary_headers = ["R&D Feasibility", "Confidence Score", "Rationale"]
        rd_summary_rows = [
            [
                str(report.narrative.get("rd_feasibility", "Possible")),
                f"{report.narrative.get('rd_confidence_pct', 0)}%",
                str(report.narrative.get("rd_rationale", "")),
            ]
        ]
        parts.append(_table(rd_summary_headers, rd_summary_rows))

        rd_headers = ["Type", "Category", "Potential Qualifying Activities"]
        rd_rows: list[list[str]] = []
        for row in report.narrative.get("rd_rows", []):
            rd_rows.append(
                [
                    "R&D Activity",
                    str(row.get("category", "")),
                    ", ".join(row.get("activities", [])),
                ]
            )
        if not rd_rows:
            for example in report.narrative.get("rd_focus_examples", []):
                rd_rows.append(["R&D Activity", "Potential Activity", str(example)])
        parts.append(_table(rd_headers, rd_rows))

        parts.append(_p("Georgia Investment Tax Credit", bold=True))
        investment_summary_headers = ["ITC Feasibility", "Confidence Score", "Rationale"]
        investment_summary_rows = [
            [
                str(report.narrative.get("investment_status", "possible")).title(),
                f"{report.narrative.get('investment_confidence_pct', 0)}%",
                str(report.narrative.get("investment_rationale", "")),
            ]
        ]
        parts.append(_table(investment_summary_headers, investment_summary_rows))
        parts.append(_p(str(report.narrative.get("investment_signals_summary", ""))))
        inv_headers = ["County", "Tier", "Investment Tax Credit %"]
        inv_rows: list[list[str]] = []
        for loc in report.locations:
            inv_rows.append(
                [
                    loc.county or "-",
                    f"Tier {loc.ga_tier}" if loc.ga_tier else "Unmapped",
                    loc.investment_tax_credit_pct or "Needs verification",
                ]
            )
        parts.append(_table(inv_headers, inv_rows))

        parts.append(_p("Automation Evidence Log", bold=True))
        for src in report.source_log:
            detail = src.get("detail", "")
            source = src.get("source", "")
            parts.append(_p(f"- {source} - {detail}"))

        body = "".join(parts) + "<w:sectPr/>"
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
            f"<w:body>{body}</w:body></w:document>"
        )

    def export_report(self, report: Report) -> Path:
        filename = f"{self._safe_slug(report.company_name)}_{report.id}.docx"
        output_path = self.exports_dir / filename
        document_xml = self._build_document_xml(report)

        with ZipFile(output_path, "w", ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
            zf.writestr("_rels/.rels", ROOT_RELS_XML)
            zf.writestr("word/document.xml", document_xml)
            zf.writestr("word/_rels/document.xml.rels", DOC_RELS_XML)

        return output_path
