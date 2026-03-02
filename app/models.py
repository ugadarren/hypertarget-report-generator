from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class AddressInput(BaseModel):
    raw: str


class LocationAssessment(BaseModel):
    address: str
    county: str | None = None
    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ga_tier: str | None = None
    military_zone: bool | None = None
    ldct: bool | None = None
    opportunity_zone: bool | None = None
    special_designation: str | None = None
    job_creation_threshold: str | None = None
    per_job_credit_amount: str | None = None
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
    sector: str
    confidence: float
    software_systems: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CompanyInput(BaseModel):
    company_name: str
    website: HttpUrl | None = None
    addresses: list[AddressInput] = Field(default_factory=list)
    notes: str | None = None


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
    narrative: dict[str, str] = Field(default_factory=dict)
    source_log: list[dict[str, Any]] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    report_id: str
    report_url: str
