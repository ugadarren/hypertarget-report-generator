from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.models import AddressInput, CompanyInput, GenerateResponse, SectorCorrectionInput
from app.services.designation_map import ArcGISDesignationService
from app.services.feedback_service import FeedbackService
from app.services.report_service import ReportService
from app.services.word_export import WordExportService

BASE_DIR = Path(__file__).resolve().parent
service = ReportService(data_dir=BASE_DIR / "data", reports_dir=BASE_DIR.parent / "reports")
word_export = WordExportService(exports_dir=BASE_DIR.parent / "reports" / "exports")
designation_service = ArcGISDesignationService()
feedback_service = FeedbackService(reports_dir=BASE_DIR.parent / "reports")

app = FastAPI(title="HyperTarget Incentive Report Generator", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/admin/exports", response_class=HTMLResponse)
async def admin_exports(request: Request):
    exports_dir = BASE_DIR.parent / "reports" / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(exports_dir.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return templates.TemplateResponse("admin_exports.html", {"request": request, "files": files})


@app.get("/admin/exports/{filename}")
async def admin_download_export(filename: str):
    if "/" in filename or "\\" in filename or not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Invalid file name")
    exports_dir = BASE_DIR.parent / "reports" / "exports"
    path = exports_dir / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/designation-explorer", response_class=HTMLResponse)
async def designation_explorer(request: Request):
    return templates.TemplateResponse(
        "designation_explorer.html",
        {
            "request": request,
            "google_maps_api_key": os.getenv("GOOGLE_MAPS_API_KEY", ""),
        },
    )


@app.post("/generate", response_class=HTMLResponse)
async def generate_from_form(
    request: Request,
    company_name: str = Form(...),
    sector: str = Form(""),
    website: str = Form(""),
    addresses: str = Form(""),
    notes: str = Form(""),
):
    address_rows = [row.strip() for row in addresses.splitlines() if row.strip()]
    payload = CompanyInput(
        company_name=company_name,
        sector=sector or None,
        website=website or None,
        addresses=[AddressInput(raw=a) for a in address_rows],
        notes=notes or None,
    )
    report = service.generate(payload)
    try:
        word_export.export_report(report)
    except RuntimeError:
        pass
    return RedirectResponse(url=f"/reports/{report.id}", status_code=303)


@app.post("/api/report/generate", response_model=GenerateResponse)
async def generate_api(payload: CompanyInput):
    report = service.generate(payload)
    try:
        word_export.export_report(report)
    except RuntimeError:
        pass
    return GenerateResponse(report_id=report.id, report_url=f"/reports/{report.id}")


@app.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_page(request: Request, report_id: str):
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return templates.TemplateResponse("report.html", {"request": request, "report": report})


@app.get("/api/reports/{report_id}")
async def report_json(report_id: str):
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report.model_dump()


@app.post("/api/sector-corrections")
async def capture_sector_correction(payload: SectorCorrectionInput):
    return feedback_service.record_sector_correction(payload)


@app.get("/api/designations")
async def designations(
    ids: str = "",
    min_lat: float | None = None,
    min_lon: float | None = None,
    max_lat: float | None = None,
    max_lon: float | None = None,
):
    definitions = designation_service.get_designation_definitions()
    response = {
        "designations": [{"id": item.id, "label": item.label, "color": item.color} for item in definitions],
        "features": {},
    }

    if not ids:
        return response

    selected_ids = [item.strip() for item in ids.split(",") if item.strip()]
    if not selected_ids:
        return response

    if None in (min_lat, min_lon, max_lat, max_lon):
        raise HTTPException(status_code=400, detail="Map bounds are required when ids are provided")

    response["features"] = designation_service.query_designation_features(
        designation_ids=selected_ids,
        min_lon=float(min_lon),
        min_lat=float(min_lat),
        max_lon=float(max_lon),
        max_lat=float(max_lat),
    )
    return response


@app.get("/reports/{report_id}/download/docx")
async def download_report_docx(report_id: str):
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        output_path = word_export.export_report(report)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(
        path=output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
