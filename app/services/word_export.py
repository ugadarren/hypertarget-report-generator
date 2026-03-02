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
                loc.job_creation_threshold or "NAICS Dependent",
                loc.per_job_credit_amount or "NAICS Dependent",
            ])
        parts.append(_table(headers, rows))

        parts.append(_p("Georgia Retraining Tax Credit", bold=True))
        parts.append(_p(report.narrative.get("retraining_intro", "")))
        parts.append(_p(report.narrative.get("retraining_context", "")))
        parts.append(_p("SOFTWARE SYSTEMS", bold=True))
        for item in report.sector_profile.software_systems:
            parts.append(_p(f"- {item}"))

        parts.append(_p("EQUIPMENT", bold=True))
        for item in report.sector_profile.equipment:
            parts.append(_p(f"- {item}"))

        parts.append(_p("Federal & State Research and Development Credit", bold=True))
        parts.append(_p(report.narrative.get("rd_intro", "")))
        parts.append(_p(report.narrative.get("rd_examples_intro", "")))
        rd_examples = [
            "Custom engineering and design work for project-specific technical challenges.",
            "Prefabrication and fabrication innovation to improve speed, safety, and efficiency.",
            "Modeling and coordination iteration to resolve routing and constructability constraints.",
            "New methods and process improvements with uncertain outcomes.",
            "Testing, prototyping, and troubleshooting performed to validate designs.",
        ]
        for example in rd_examples:
            parts.append(_p(f"- {example}"))

        parts.append(_p("Cost Segregation", bold=True))
        parts.append(_p(report.narrative.get("costseg_intro", "")))
        parts.append(_p(report.narrative.get("costseg_detail", "")))
        parts.append(_p(report.narrative.get("costseg_bonus", "")))

        if report.expansion_signals:
            parts.append(_p("Georgia Investment Tax Credit", bold=True))
            parts.append(
                _p(
                    "Expansion or capital investment signals were detected in researched company content. "
                    "Eligibility depends on county tier, qualified investment property, and placement-in-service timing."
                )
            )
            parts.append(_p("Detected signals: " + ", ".join(report.expansion_signals)))

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
