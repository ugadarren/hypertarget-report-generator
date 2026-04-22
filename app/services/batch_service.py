from __future__ import annotations

import csv
import io
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from app.models import AddressInput, CompanyInput
from app.services.report_service import ReportService
from app.services.word_export import WordExportService


@dataclass
class BatchRunResult:
    batch_id: str
    created_at: str
    input_filename: str
    total_rows: int
    success_count: int
    failure_count: int
    rows: list[dict]
    zip_filename: str | None
    summary_csv_filename: str | None


class BatchReportService:
    MAX_ADDRESSES_PER_COMPANY = 5

    def __init__(self, reports_dir: Path, report_service: ReportService, word_export: WordExportService):
        self.reports_dir = reports_dir
        self.report_service = report_service
        self.word_export = word_export
        self.batches_dir = self.reports_dir / "batches"
        self.batches_dir.mkdir(parents=True, exist_ok=True)

    def _safe_slug(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").strip())
        cleaned = "_".join(part for part in cleaned.split("_") if part)
        return cleaned[:80] or "company"

    def _normalize_header(self, value: str) -> str:
        raw = (value or "").strip().lower().lstrip("\ufeff")
        simplified = "".join(ch for ch in raw if ch.isalnum())
        if not simplified:
            return ""

        aliases = {
            "company": "company_name",
            "companyname": "company_name",
            "business": "company_name",
            "businessname": "company_name",
            "legalname": "company_name",
            "name": "company_name",
            "website": "website",
            "websiteurl": "website",
            "webaddress": "website",
            "url": "website",
            "domain": "website",
            "companywebsite": "website",
            "companyurl": "website",
            "companydomain": "website",
            "industry": "sector",
            "sector": "sector",
            "notes": "notes",
            "note": "notes",
            "comments": "notes",
            "comment": "notes",
            "address": "address",
            "streetaddress": "address",
            "mailingaddress": "address",
            "companyaddress": "address",
            "address1": "address_1",
            "streetaddress1": "address_1",
            "mailingaddress1": "address_1",
            "companyaddress1": "address_1",
            "address2": "address_2",
            "streetaddress2": "address_2",
            "mailingaddress2": "address_2",
            "companyaddress2": "address_2",
            "address3": "address_3",
            "streetaddress3": "address_3",
            "mailingaddress3": "address_3",
            "companyaddress3": "address_3",
            "address4": "address_4",
            "streetaddress4": "address_4",
            "mailingaddress4": "address_4",
            "companyaddress4": "address_4",
            "address5": "address_5",
            "streetaddress5": "address_5",
            "mailingaddress5": "address_5",
            "companyaddress5": "address_5",
        }
        if simplified in aliases:
            return aliases[simplified]
        if simplified.startswith("address") and simplified[7:].isdigit():
            return f"address_{simplified[7:]}"
        return raw.replace("-", "_").replace(" ", "_")

    def _get_value(self, row: dict[str, str], *names: str) -> str:
        normalized = {self._normalize_header(k): (v or "") for k, v in row.items()}
        for name in names:
            value = normalized.get(self._normalize_header(name), "")
            if value.strip():
                return value.strip()
        return ""

    def _extract_addresses(self, row: dict[str, str]) -> list[str]:
        normalized = {self._normalize_header(k): (v or "").strip() for k, v in row.items()}
        addresses: list[str] = []
        single = normalized.get("address", "")
        if single:
            addresses.extend([part.strip() for part in single.split("|") if part.strip()])
        for key, value in normalized.items():
            if not value:
                continue
            if key.startswith("address_") or key.startswith("address"):
                if key == "address":
                    continue
                addresses.append(value)
        deduped: list[str] = []
        for address in addresses:
            if address and address not in deduped:
                deduped.append(address)
        if len(deduped) > self.MAX_ADDRESSES_PER_COMPANY:
            raise ValueError(
                f"A maximum of {self.MAX_ADDRESSES_PER_COMPANY} addresses is supported per company row."
            )
        return deduped

    def _payload_from_row(self, row: dict[str, str]) -> CompanyInput:
        company_name = self._get_value(row, "company_name", "company", "name")
        if not company_name:
            raise ValueError("Missing required company_name column value.")

        website = self._get_value(row, "website", "url", "domain") or None
        sector = self._get_value(row, "sector", "industry") or None
        notes = self._get_value(row, "notes", "note") or None
        addresses = [AddressInput(raw=value) for value in self._extract_addresses(row)]

        return CompanyInput(
            company_name=company_name,
            website=website,
            sector=sector,
            notes=notes,
            addresses=addresses,
        )

    def _write_summary_csv(self, path: Path, rows: list[dict]) -> None:
        fieldnames = [
            "row_number",
            "company_name",
            "website",
            "status",
            "report_id",
            "report_url",
            "docx_filename",
            "error",
        ]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})

    def _build_zip(self, batch_dir: Path, zip_name: str) -> Path | None:
        docx_files = sorted(batch_dir.glob("*.docx"))
        if not docx_files:
            return None
        zip_path = batch_dir / zip_name
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
            for path in docx_files:
                zf.write(path, arcname=path.name)
        return zip_path

    def run_csv_batch(self, file_bytes: bytes, filename: str, include_confidence: bool = False) -> BatchRunResult:
        decoded = file_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
        if not reader.fieldnames:
            raise ValueError("CSV file is missing a header row.")

        batch_id = uuid4().hex[:12]
        created_at = datetime.now(timezone.utc).isoformat()
        batch_dir = self.batches_dir / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)

        rows_out: list[dict] = []
        total_rows = 0
        success_count = 0
        failure_count = 0

        for idx, row in enumerate(reader, start=1):
            if not any(str(value or "").strip() for value in row.values()):
                continue
            total_rows += 1
            try:
                payload = self._payload_from_row(row)
                report = self.report_service.generate(payload)
                exported = self.word_export.export_report(report, include_confidence=include_confidence)
                target_name = f"{idx:03d}_{self._safe_slug(payload.company_name)}_{report.id}.docx"
                target_path = batch_dir / target_name
                shutil.copy2(exported, target_path)
                rows_out.append(
                    {
                        "row_number": idx,
                        "company_name": payload.company_name,
                        "website": payload.website or "",
                        "status": "completed",
                        "report_id": report.id,
                        "report_url": f"/reports/{report.id}",
                        "docx_filename": target_name,
                        "error": "",
                    }
                )
                success_count += 1
            except Exception as exc:
                rows_out.append(
                    {
                        "row_number": idx,
                        "company_name": self._get_value(row, "company_name", "company", "name"),
                        "website": self._get_value(row, "website", "url", "domain"),
                        "status": "failed",
                        "report_id": "",
                        "report_url": "",
                        "docx_filename": "",
                        "error": str(exc),
                    }
                )
                failure_count += 1

        summary_csv_name = "batch_summary.csv"
        summary_csv_path = batch_dir / summary_csv_name
        self._write_summary_csv(summary_csv_path, rows_out)

        zip_name = "batch_reports.zip"
        zip_path = self._build_zip(batch_dir, zip_name)

        summary = {
            "batch_id": batch_id,
            "created_at": created_at,
            "input_filename": filename,
            "total_rows": total_rows,
            "success_count": success_count,
            "failure_count": failure_count,
            "rows": rows_out,
            "zip_filename": zip_path.name if zip_path else None,
            "summary_csv_filename": summary_csv_name,
        }
        (batch_dir / "summary.json").write_text(json.dumps(summary, indent=2))

        return BatchRunResult(**summary)

    def get_batch(self, batch_id: str) -> BatchRunResult | None:
        summary_path = self.batches_dir / batch_id / "summary.json"
        if not summary_path.exists():
            return None
        return BatchRunResult(**json.loads(summary_path.read_text()))

    def get_batch_file(self, batch_id: str, filename: str) -> Path | None:
        if "/" in filename or "\\" in filename:
            return None
        path = self.batches_dir / batch_id / filename
        if not path.exists() or not path.is_file():
            return None
        return path
