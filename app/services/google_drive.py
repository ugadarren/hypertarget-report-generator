from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


@dataclass
class DriveUploadResult:
    file_id: str
    web_view_link: str
    web_content_link: str | None
    name: str


class GoogleDriveUploadService:
    def __init__(self):
        self.settings = get_settings()

    def is_enabled(self) -> bool:
        return self.settings.google_drive_enabled

    def _imports(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            return {
                "service_account": service_account,
                "build": build,
                "MediaFileUpload": MediaFileUpload,
            }
        except Exception as exc:
            raise RuntimeError(
                "Google Drive upload requires google-api-python-client and google-auth. Install dependencies with `pip install -r requirements.txt`."
            ) from exc

    def _credentials(self, service_account):
        scopes = ["https://www.googleapis.com/auth/drive.file"]
        raw_json = self.settings.google_drive_service_account_json.strip()
        if raw_json:
            try:
                info = json.loads(raw_json)
            except Exception as exc:
                raise RuntimeError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
            return service_account.Credentials.from_service_account_info(info, scopes=scopes)

        file_path = self.settings.google_drive_service_account_file.strip()
        if not file_path:
            raise RuntimeError("Google Drive service account credentials are not configured.")
        return service_account.Credentials.from_service_account_file(file_path, scopes=scopes)

    def upload_docx(self, file_path: Path) -> DriveUploadResult | None:
        if not self.is_enabled():
            return None
        if not file_path.exists():
            raise FileNotFoundError(f"Export file not found: {file_path}")

        deps = self._imports()
        service_account = deps["service_account"]
        build = deps["build"]
        MediaFileUpload = deps["MediaFileUpload"]

        credentials = self._credentials(service_account)
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        metadata = {
            "name": file_path.name,
            "parents": [self.settings.google_drive_folder_id],
        }
        media = MediaFileUpload(
            str(file_path),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            resumable=False,
        )
        created = (
            service.files()
            .create(
                body=metadata,
                media_body=media,
                supportsAllDrives=True,
                fields="id,name,webViewLink,webContentLink",
            )
            .execute()
        )
        file_id = str(created.get("id", "")).strip()
        if not file_id:
            raise RuntimeError("Google Drive upload did not return a file id.")
        web_view_link = str(created.get("webViewLink", "")).strip() or f"https://drive.google.com/file/d/{file_id}/view"
        web_content_link = str(created.get("webContentLink", "")).strip() or None
        return DriveUploadResult(
            file_id=file_id,
            web_view_link=web_view_link,
            web_content_link=web_content_link,
            name=str(created.get("name", file_path.name)),
        )

    def write_upload_metadata(self, metadata_path: Path, result: DriveUploadResult) -> None:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {
                    "file_id": result.file_id,
                    "web_view_link": result.web_view_link,
                    "web_content_link": result.web_content_link,
                    "name": result.name,
                },
                indent=2,
            )
        )

    def write_upload_error(self, metadata_path: Path, error: str) -> None:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps({"error": str(error).strip()}, indent=2))

    def read_upload_metadata(self, metadata_path: Path) -> dict | None:
        if not metadata_path.exists():
            return None
        try:
            return json.loads(metadata_path.read_text())
        except Exception:
            return None
