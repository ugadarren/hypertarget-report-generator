from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class AddressInput(BaseModel):
    raw: str


class LocationAssessment(BaseModel):
    address: str
    county: str | None = None
    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ga_tier: str | None = None
    ga_tier_label: str | None = None
    military_zone: bool | None = None
    ldct: bool | None = None
    opportunity_zone: bool | None = None
    tier1_lower_40: bool | None = None
    special_designation: str | None = None
    job_creation_threshold: str | None = None
    per_job_credit_amount: str | None = None
    investment_tax_credit_pct: str | None = None
    tier_history: list[str] = Field(default_factory=list)
    zone_details: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class CreditAssessment(BaseModel):
    code: Literal[
        "GA_RETRAINING",
        "FEDERAL_RD",
        "GA_RD",
        "COST_SEGREGATION",
        "GA_INVESTMENT",
        "MERP",
    ]
    name: str
    status: Literal["likely", "possible", "unlikely"]
    rationale: str
    triggers: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class SectorProfile(BaseModel):
    sector_key: str = "unknown"
    sector: str
    company_description: str | None = None
    sector_summary: str | None = None
    rd_focus_examples: list[str] = Field(default_factory=list)
    rd_feasibility: Literal["likely", "possible", "unlikely"] = "possible"
    rd_confidence: float = 0.5
    rd_rationale: str | None = None
    rd_rows: list[dict[str, Any]] = Field(default_factory=list)
    investment_credit_applicable: bool = False
    retraining_rows: list[dict[str, Any]] = Field(default_factory=list)
    software_systems: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ContactLead(BaseModel):
    name: str | None = None
    title: str | None = None
    email: str | None = None
    confidence: float = 0.0
    source_url: str | None = None
    notes: str | None = None


class CompanyInput(BaseModel):
    company_name: str
    sector: str | None = None
    website: str | None = None
    addresses: list[AddressInput] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("website", mode="before")
    @classmethod
    def normalize_website(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            return None
        normalized = cleaned if "://" in cleaned else f"https://{cleaned}"
        parsed = urlparse(normalized)
        if not parsed.netloc or "." not in parsed.netloc:
            raise ValueError("Enter a valid website URL or domain.")
        return normalized


class Report(BaseModel):
    id: str
    created_at: datetime
    company_name: str
    website: str | None = None
    sector_profile: SectorProfile
    locations: list[LocationAssessment]
    credits: list[CreditAssessment]
    expansion_signals: list[str] = Field(default_factory=list)
    property_signals: list[str] = Field(default_factory=list)
    contact_intelligence: list[ContactLead] = Field(default_factory=list)
    narrative: dict[str, Any] = Field(default_factory=dict)
    source_log: list[dict[str, Any]] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    report_id: str
    report_url: str


class SectorCorrectionInput(BaseModel):
    report_id: str | None = None
    company_name: str
    website: str | None = None
    predicted_sector_key: str
    predicted_sector_label: str
    corrected_sector_key: str
    corrected_sector_label: str
    notes: str | None = None
