from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
import secrets

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.models import AddressInput, CompanyInput, GenerateResponse, SectorCorrectionInput
from app.config import get_settings
from app.services.designation_map import ArcGISDesignationService
from app.services.batch_service import BatchReportService
from app.services.feedback_service import FeedbackService
from app.services.report_service import ReportService
from app.services.word_export import WordExportService

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
service = ReportService(data_dir=BASE_DIR / "data", reports_dir=BASE_DIR.parent / "reports")
word_export = WordExportService(exports_dir=BASE_DIR.parent / "reports" / "exports")
designation_service = ArcGISDesignationService()
feedback_service = FeedbackService(reports_dir=BASE_DIR.parent / "reports")
batch_service = BatchReportService(reports_dir=BASE_DIR.parent / "reports", report_service=service, word_export=word_export)

app = FastAPI(title="HyperTarget Incentive Report Generator", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _app_version() -> str:
    env_candidates = [
        os.getenv("RENDER_GIT_COMMIT", "").strip(),
        os.getenv("GIT_COMMIT", "").strip(),
        os.getenv("COMMIT_SHA", "").strip(),
    ]
    for value in env_candidates:
        if value:
            return value[:7]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BASE_DIR.parent),
            capture_output=True,
            text=True,
            check=True,
        )
        sha = result.stdout.strip()
        if sha:
            return sha
    except Exception:
        pass
    return "unknown"


@app.middleware("http")
async def basic_auth_guard(request: Request, call_next):
    settings = get_settings()
    # Enable protection only when both values are configured.
    if not settings.auth_enabled:
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return Response(
            content="Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="HyperTarget"'},
        )

    try:
        import base64

        encoded = auth.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return Response(
            content="Invalid authentication header",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="HyperTarget"'},
        )

    if not (
        secrets.compare_digest(username, settings.app_username)
        and secrets.compare_digest(password, settings.app_password)
    ):
        return Response(
            content="Invalid credentials",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="HyperTarget"'},
        )

    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_version": _app_version()})


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/status")
async def status():
    settings = get_settings()
    return {
        "app_version": _app_version(),
        "auth_enabled": settings.auth_enabled,
        "gpt_enabled": settings.gpt_enabled,
        "llm_contact_enrichment_enabled": settings.enable_llm_contact_enrichment,
        "openai_model": settings.openai_model,
        "arcgis_viewer_url": settings.arcgis_viewer_url,
    }


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
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%m-%d-%Y %I:%M:%S %p"),
            }
        )
    return templates.TemplateResponse("admin_exports.html", {"request": request, "files": files, "app_version": _app_version()})


@app.get("/admin/batches", response_class=HTMLResponse)
async def admin_batches(request: Request):
    return templates.TemplateResponse(
        "admin_batches.html",
        {
            "request": request,
            "app_version": _app_version(),
            "batch": None,
            "error": "",
        },
    )


@app.post("/admin/batches", response_class=HTMLResponse)
async def run_admin_batch(
    request: Request,
    file: UploadFile = File(...),
    include_confidence: bool = Form(False),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return templates.TemplateResponse(
            "admin_batches.html",
            {
                "request": request,
                "app_version": _app_version(),
                "batch": None,
                "error": "Upload a CSV file.",
            },
            status_code=400,
        )

    try:
        file_bytes = await file.read()
        batch = batch_service.run_csv_batch(
            file_bytes=file_bytes,
            filename=file.filename,
            include_confidence=include_confidence,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "admin_batches.html",
            {
                "request": request,
                "app_version": _app_version(),
                "batch": None,
                "error": str(exc),
            },
            status_code=400,
        )

    return RedirectResponse(url=f"/admin/batches/{batch.batch_id}", status_code=303)


@app.get("/admin/batches/{batch_id}", response_class=HTMLResponse)
async def admin_batch_detail(request: Request, batch_id: str):
    batch = batch_service.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return templates.TemplateResponse(
        "admin_batches.html",
        {
            "request": request,
            "app_version": _app_version(),
            "batch": batch,
            "error": "",
        },
    )


@app.get("/admin/batches/{batch_id}/files/{filename}")
async def admin_batch_download(batch_id: str, filename: str):
    path = batch_service.get_batch_file(batch_id, filename)
    if not path:
        raise HTTPException(status_code=404, detail="Batch file not found")
    media_type = "application/octet-stream"
    if path.suffix.lower() == ".zip":
        media_type = "application/zip"
    elif path.suffix.lower() == ".csv":
        media_type = "text/csv"
    elif path.suffix.lower() == ".docx":
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(path=path, filename=path.name, media_type=media_type)


@app.get("/admin/config", response_class=HTMLResponse)
async def admin_config(request: Request):
    report_copy_path = DATA_DIR / "report_copy.json"
    web_policy_path = DATA_DIR / "web_research_policy.json"
    return templates.TemplateResponse(
        "admin_config.html",
        {
            "request": request,
            "app_version": _app_version(),
            "report_copy_json": json.dumps(json.loads(report_copy_path.read_text()), indent=2),
            "web_research_policy_json": json.dumps(json.loads(web_policy_path.read_text()), indent=2),
            "saved": request.query_params.get("saved") == "1",
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/admin/config", response_class=HTMLResponse)
async def save_admin_config(
    request: Request,
    report_copy_json: str = Form(...),
    web_research_policy_json: str = Form(...),
):
    try:
        parsed_report_copy = json.loads(report_copy_json)
        parsed_web_policy = json.loads(web_research_policy_json)
    except Exception as exc:
        return templates.TemplateResponse(
            "admin_config.html",
            {
                "request": request,
                "app_version": _app_version(),
                "report_copy_json": report_copy_json,
                "web_research_policy_json": web_research_policy_json,
                "saved": False,
                "error": f"Invalid JSON: {exc}",
            },
            status_code=400,
        )

    if not isinstance(parsed_report_copy, dict):
        return templates.TemplateResponse(
            "admin_config.html",
            {
                "request": request,
                "app_version": _app_version(),
                "report_copy_json": report_copy_json,
                "web_research_policy_json": web_research_policy_json,
                "saved": False,
                "error": "report_copy.json must be a JSON object.",
            },
            status_code=400,
        )
    if not isinstance(parsed_web_policy, dict):
        return templates.TemplateResponse(
            "admin_config.html",
            {
                "request": request,
                "app_version": _app_version(),
                "report_copy_json": report_copy_json,
                "web_research_policy_json": web_research_policy_json,
                "saved": False,
                "error": "web_research_policy.json must be a JSON object.",
            },
            status_code=400,
        )

    required_copy_keys = {"ga_jtc_intro", "rd_intro", "investment_intro", "costseg_intro"}
    missing_copy = sorted([k for k in required_copy_keys if k not in parsed_report_copy])
    if missing_copy:
        return templates.TemplateResponse(
            "admin_config.html",
            {
                "request": request,
                "app_version": _app_version(),
                "report_copy_json": report_copy_json,
                "web_research_policy_json": web_research_policy_json,
                "saved": False,
                "error": f"report_copy.json is missing required keys: {', '.join(missing_copy)}",
            },
            status_code=400,
        )

    if "priority_link_terms" not in parsed_web_policy or not isinstance(parsed_web_policy.get("priority_link_terms"), list):
        return templates.TemplateResponse(
            "admin_config.html",
            {
                "request": request,
                "report_copy_json": report_copy_json,
                "web_research_policy_json": web_research_policy_json,
                "saved": False,
                "error": "web_research_policy.json must include a list key: priority_link_terms",
            },
            status_code=400,
        )

    (DATA_DIR / "report_copy.json").write_text(json.dumps(parsed_report_copy, indent=2))
    (DATA_DIR / "web_research_policy.json").write_text(json.dumps(parsed_web_policy, indent=2))
    return RedirectResponse(url="/admin/config?saved=1", status_code=303)


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
            "app_version": _app_version(),
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
    return templates.TemplateResponse("report.html", {"request": request, "report": report, "app_version": _app_version()})


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
async def download_report_docx(report_id: str, include_confidence: bool = False):
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        output_path = word_export.export_report(report, include_confidence=include_confidence)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(
        path=output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
