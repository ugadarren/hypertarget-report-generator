from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 45.0
DEFAULT_ARCGIS_VIEWER_URL = "https://experience.arcgis.com/experience/e655a4ebd5e94cdd9a731822f59d2097"


@dataclass(frozen=True)
class Settings:
    app_username: str
    app_password: str
    openai_api_key: str
    openai_model: str
    openai_timeout_seconds: float
    arcgis_viewer_url: str
    enable_llm_contact_enrichment: bool

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_username and self.app_password)

    @property
    def gpt_enabled(self) -> bool:
        return bool(self.openai_api_key)


def get_settings() -> Settings:
    timeout_raw = os.getenv("OPENAI_TIMEOUT_SECONDS", str(DEFAULT_OPENAI_TIMEOUT_SECONDS)).strip()
    try:
        timeout = float(timeout_raw)
    except Exception:
        timeout = DEFAULT_OPENAI_TIMEOUT_SECONDS

    enable_contact_llm_raw = os.getenv("ENABLE_LLM_CONTACT_ENRICHMENT", "true").strip().lower()
    enable_contact_llm = enable_contact_llm_raw not in {"0", "false", "no", "off"}

    return Settings(
        app_username=os.getenv("APP_USERNAME", "").strip(),
        app_password=os.getenv("APP_PASSWORD", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
        openai_timeout_seconds=timeout,
        arcgis_viewer_url=os.getenv("ARCGIS_VIEWER_URL", DEFAULT_ARCGIS_VIEWER_URL).strip() or DEFAULT_ARCGIS_VIEWER_URL,
        enable_llm_contact_enrichment=enable_contact_llm,
    )
