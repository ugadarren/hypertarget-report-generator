from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 45.0
DEFAULT_ARCGIS_VIEWER_URL = "https://experience.arcgis.com/experience/e655a4ebd5e94cdd9a731822f59d2097"


@dataclass(frozen=True)
class Settings:
    admin_username: str
    admin_password: str
    user_username: str
    user_password: str
    openai_api_key: str
    openai_model: str
    openai_timeout_seconds: float
    arcgis_viewer_url: str
    enable_llm_contact_enrichment: bool
    google_drive_folder_id: str
    google_drive_service_account_file: str
    google_drive_service_account_json: str

    @property
    def auth_enabled(self) -> bool:
        return self.admin_enabled or self.user_enabled

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_username and self.admin_password)

    @property
    def user_enabled(self) -> bool:
        return bool(self.user_username and self.user_password)

    @property
    def gpt_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def google_drive_enabled(self) -> bool:
        return bool(
            self.google_drive_folder_id
            and (self.google_drive_service_account_json or self.google_drive_service_account_file)
        )


def get_settings() -> Settings:
    timeout_raw = os.getenv("OPENAI_TIMEOUT_SECONDS", str(DEFAULT_OPENAI_TIMEOUT_SECONDS)).strip()
    try:
        timeout = float(timeout_raw)
    except Exception:
        timeout = DEFAULT_OPENAI_TIMEOUT_SECONDS

    enable_contact_llm_raw = os.getenv("ENABLE_LLM_CONTACT_ENRICHMENT", "true").strip().lower()
    enable_contact_llm = enable_contact_llm_raw not in {"0", "false", "no", "off"}

    return Settings(
        admin_username=os.getenv("APP_USERNAME", "").strip(),
        admin_password=os.getenv("APP_PASSWORD", "").strip(),
        user_username=os.getenv("USER_USERNAME", "").strip(),
        user_password=os.getenv("USER_PASSWORD", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
        openai_timeout_seconds=timeout,
        arcgis_viewer_url=os.getenv("ARCGIS_VIEWER_URL", DEFAULT_ARCGIS_VIEWER_URL).strip() or DEFAULT_ARCGIS_VIEWER_URL,
        enable_llm_contact_enrichment=enable_contact_llm,
        google_drive_folder_id=os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip(),
        google_drive_service_account_file=os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", "").strip(),
        google_drive_service_account_json=os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "").strip(),
    )
