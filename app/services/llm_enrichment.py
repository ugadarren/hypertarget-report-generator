from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.data.industry_profiles import SECTOR_DETAILS
from app.models import SectorProfile
from app.services.sector import keyword_sector_scores, sector_candidates, sector_family, sector_needs_review


def _clip(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _extract_json(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        pass
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(body[start : end + 1])
    except Exception:
        return None


def _normalize_retraining_row(row: dict[str, Any]) -> dict[str, Any] | None:
    row_type = str(row.get("type", "")).strip()
    category = str(row.get("category", "")).strip()
    programs = row.get("applicable_programs", [])
    if not row_type or not category or not isinstance(programs, list):
        return None
    cleaned_programs = [str(item).strip() for item in programs if str(item).strip()]
    if not cleaned_programs:
        return None
    return {
        "type": "Software" if row_type.lower().startswith("soft") else "Equipment",
        "category": category,
        "applicable_programs": cleaned_programs[:6],
    }


def _normalize_rd_row(row: dict[str, Any]) -> dict[str, Any] | None:
    category = str(row.get("category", "")).strip()
    activities = row.get("activities", [])
    if not category or not isinstance(activities, list):
        return None
    cleaned_activities = [str(item).strip() for item in activities if str(item).strip()]
    if not cleaned_activities:
        return None
    return {
        "category": category,
        "activities": cleaned_activities[:5],
    }


def _normalize_contact_row(row: dict[str, Any]) -> dict[str, Any] | None:
    name = str(row.get("name", "") or "").strip() or None
    title = str(row.get("title", "") or "").strip() or None
    email = str(row.get("email", "") or "").strip() or None
    source_url = str(row.get("source_url", "") or "").strip() or None
    notes = str(row.get("notes", "") or "").strip() or None
    try:
        confidence = float(row.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    if not name and not email:
        return None
    if email and "@" not in email:
        email = None
    return {
        "name": name,
        "title": title,
        "email": email,
        "confidence": confidence,
        "source_url": source_url,
        "notes": notes,
    }


def _build_rd_rows(examples: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, chunk_start in enumerate(range(0, min(len(examples), 8), 2)):
        chunk = examples[chunk_start : chunk_start + 2]
        if not chunk:
            continue
        rows.append(
            {
                "category": f"Potential R&D Activity Group {idx + 1}",
                "activities": chunk,
            }
        )
    return rows


def _rd_default_by_sector(sector_key: str) -> tuple[str, float]:
    likely = {"manufacturing", "software", "automotive", "telecommunications", "food_processing"}
    unlikely = {"logistics", "staffing_recruiting"}
    if sector_key in likely:
        return "likely", 0.8
    if sector_key in unlikely:
        return "possible", 0.45
    return "possible", 0.6


def _response_output_text(data: dict[str, Any]) -> str:
    text = data.get("output_text") or ""
    if text:
        return str(text)
    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            body = content.get("text")
            if body:
                chunks.append(str(body))
    return "\n".join(chunks)


def _post_openai(prompt: str, system_prompt: str, model: str, api_key: str, timeout_seconds: float) -> tuple[dict[str, Any] | None, str | None]:
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:
        return None, str(exc)


def detect_sector_with_llm(
    *,
    company_name: str,
    website: str | None,
    snippets: list[str],
    weighted_texts: dict[str, str] | None,
    research_text: str,
) -> tuple[dict[str, str] | None, dict[str, str]]:
    settings = get_settings()
    curated_evidence = {
        "title": (weighted_texts or {}).get("title", ""),
        "meta": (weighted_texts or {}).get("meta", ""),
        "headings": (weighted_texts or {}).get("headings", ""),
        "paragraphs": (weighted_texts or {}).get("paragraphs", ""),
    }
    if not settings.gpt_enabled:
        return None, {
            "source": "openai",
            "type": "llm",
            "detail": "OPENAI_API_KEY not configured; used keyword-based sector inference.",
        }

    choices = [{"key": key, "label": str(value.get("label", key))} for key, value in SECTOR_DETAILS.items()]
    prompt = f"""
Classify the company's primary industry sector from the allowed sector options.
Focus on what the company actually sells, manufactures, distributes, or delivers.
Do not classify based on generic internal technology, software tools, or digital transformation language unless the company itself is fundamentally a software/technology business.

Company: {company_name}
Website: {website or "Not provided"}
Allowed sectors JSON:
{json.dumps(choices)}

Curated company-description evidence:
{json.dumps(curated_evidence, ensure_ascii=True)}

Website snippets:
{_clip(chr(10).join(snippets[:10]), 8000)}

Return JSON only:
{{
  "sector_key": "one of allowed keys",
  "sector_label": "matching label",
  "reason": "1-2 sentence rationale"
}}
"""
    data, err = _post_openai(
        prompt,
        "You are an industry classification analyst. Output valid JSON only.",
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    if err or not data:
        return None, {"source": "openai", "type": "llm_error", "detail": f"LLM sector detection failed: {err or 'unknown'}"}

    parsed = _extract_json(_response_output_text(data))
    if not parsed:
        return None, {"source": "openai", "type": "llm_error", "detail": "LLM sector detection returned non-JSON output."}

    sector_key = str(parsed.get("sector_key", "")).strip()
    if sector_key not in SECTOR_DETAILS:
        return None, {"source": "openai", "type": "llm_error", "detail": f"LLM sector key not recognized: {sector_key or 'empty'}"}

    sector_label = str(parsed.get("sector_label", "")).strip() or str(SECTOR_DETAILS[sector_key].get("label", sector_key))
    reason = str(parsed.get("reason", "")).strip() or "Sector selected from website evidence."
    keyword_ranked = keyword_sector_scores(research_text, snippets, weighted_texts)
    top_keyword_key, top_keyword_score = keyword_ranked[0] if keyword_ranked else ("", 0)
    detected_keyword_score = next((score for key, score in keyword_ranked if key == sector_key), 0)
    if top_keyword_key and top_keyword_key != sector_key and top_keyword_score >= max(12, detected_keyword_score + 10):
        top_label = str(SECTOR_DETAILS.get(top_keyword_key, {}).get("label", top_keyword_key))
        return None, {
            "source": "openai",
            "type": "llm_error",
            "detail": (
                f"LLM sector detection rejected by keyword sanity check. "
                f"LLM={sector_label} ({sector_key}); keyword evidence favored {top_label} ({top_keyword_key})."
            ),
        }
    top_family = sector_family(top_keyword_key) if top_keyword_key else ""
    detected_family = sector_family(sector_key)
    if top_family and detected_family != top_family and top_keyword_score >= 16:
        return None, {
            "source": "openai",
            "type": "llm_error",
            "detail": (
                f"LLM sector detection rejected by family guardrail. "
                f"LLM={sector_label} ({detected_family}); keyword family favored {top_family}."
            ),
        }
    return (
        {"sector_key": sector_key, "sector_label": sector_label, "reason": reason},
        {"source": "openai", "type": "llm", "detail": f"Detected sector {sector_label} ({sector_key}). {reason}"},
    )


def enrich_sector_profile(
    *,
    company_name: str,
    website: str | None,
    sector_context: str,
    base_sector: SectorProfile,
    snippets: list[str],
    research_text: str,
) -> tuple[SectorProfile | None, dict[str, str]]:
    settings = get_settings()
    default_rd_feasibility, default_rd_conf = _rd_default_by_sector(base_sector.sector_key)

    if not settings.gpt_enabled:
        fallback_data = base_sector.model_dump()
        fallback_data["rd_feasibility"] = base_sector.rd_feasibility or default_rd_feasibility
        fallback_data["rd_confidence"] = base_sector.rd_confidence or default_rd_conf
        fallback_data["rd_rationale"] = (
            base_sector.rd_rationale
            or f"Industry-based fallback estimate for {base_sector.sector.lower()} without LLM enrichment."
        )
        fallback_data["rd_rows"] = base_sector.rd_rows or _build_rd_rows(base_sector.rd_focus_examples)
        fallback = SectorProfile(**fallback_data)
        return fallback, {
            "source": "openai",
            "type": "llm",
            "detail": "OPENAI_API_KEY not configured; used deterministic sector profile.",
        }

    context = "\n".join(snippets[:15])
    prompt = f"""
You are building tax-credit intelligence for a Georgia incentive report.
Use this selected sector as authoritative and do not reclassify it.

Company: {company_name}
Website: {website or "Not provided"}
Selected industry/sector: {sector_context}

Website evidence snippets:
{_clip(context, 9000)}

Additional page text:
{_clip(research_text, 8000)}

Return JSON only with this exact schema:
{{
  "company_description": "1-2 sentence client-ready description of what the company does",
  "sector_summary": "1-2 sentence industry context tailored to this company",
  "software_systems": ["10-14 specific systems/tools used in this industry"],
  "equipment": ["8-12 specific equipment/technology items used in this industry"],
  "retraining_rows": [
    {{
      "type": "Software or Equipment",
      "category": "category name",
      "applicable_programs": ["3-5 concrete examples"]
    }}
  ],
  "rd_feasibility": "likely|possible|unlikely",
  "rd_confidence": 0.0,
  "rd_rationale": "1-2 sentence rationale for feasibility",
  "rd_rows": [
    {{
      "category": "R&D activity category",
      "activities": ["3-5 likely activities for this company"]
    }}
  ],
  "rd_focus_examples": ["6-10 examples of likely qualifying R&D activities for this company"]
}}

Rules:
- For retraining_rows, include exactly 2 software categories and 2 equipment categories.
- For rd_rows, include at least 3 and at most 4 categories.
- Keep descriptions client-facing and specific to the company.
"""
    data, err = _post_openai(
        prompt,
        "You are a precise tax-credit analyst. Output valid JSON only.",
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    if err or not data:
        return None, {"source": "openai", "type": "llm_error", "detail": f"LLM enrichment failed: {err or 'unknown'}"}

    parsed = _extract_json(_response_output_text(data))
    if not parsed:
        return None, {
            "source": "openai",
            "type": "llm_error",
            "detail": "LLM enrichment returned non-JSON output; used deterministic sector profile.",
        }

    software = [str(s).strip() for s in parsed.get("software_systems", []) if str(s).strip()][:14]
    equipment = [str(s).strip() for s in parsed.get("equipment", []) if str(s).strip()][:12]
    rd_examples = [str(s).strip() for s in parsed.get("rd_focus_examples", []) if str(s).strip()][:10]

    retraining_rows: list[dict[str, Any]] = []
    for raw_row in parsed.get("retraining_rows", []):
        if isinstance(raw_row, dict):
            normalized = _normalize_retraining_row(raw_row)
            if normalized:
                retraining_rows.append(normalized)

    rd_rows: list[dict[str, Any]] = []
    for raw_row in parsed.get("rd_rows", []):
        if isinstance(raw_row, dict):
            normalized = _normalize_rd_row(raw_row)
            if normalized:
                rd_rows.append(normalized)

    rd_feasibility_raw = str(parsed.get("rd_feasibility", "")).strip().lower()
    rd_feasibility = rd_feasibility_raw if rd_feasibility_raw in {"likely", "possible", "unlikely"} else default_rd_feasibility
    try:
        rd_confidence = float(parsed.get("rd_confidence", default_rd_conf))
    except Exception:
        rd_confidence = default_rd_conf
    rd_confidence = max(0.0, min(1.0, rd_confidence))

    enriched = SectorProfile(
        sector_key=base_sector.sector_key,
        sector=base_sector.sector,
        company_description=_clip(str(parsed.get("company_description", "")).strip(), 1200) or base_sector.company_description,
        sector_summary=_clip(str(parsed.get("sector_summary", "")).strip(), 1200) or base_sector.sector_summary,
        rd_focus_examples=rd_examples or base_sector.rd_focus_examples,
        rd_feasibility=rd_feasibility,
        rd_confidence=rd_confidence,
        rd_rationale=_clip(str(parsed.get("rd_rationale", "")).strip(), 1200)
        or base_sector.rd_rationale
        or f"Estimated feasibility for {base_sector.sector.lower()} industry.",
        rd_rows=rd_rows or base_sector.rd_rows or _build_rd_rows(rd_examples or base_sector.rd_focus_examples),
        investment_credit_applicable=base_sector.investment_credit_applicable,
        retraining_rows=retraining_rows or base_sector.retraining_rows,
        software_systems=software or base_sector.software_systems,
        equipment=equipment or base_sector.equipment,
        evidence=list(base_sector.evidence)
        + [
            f"LLM-enriched using model {settings.openai_model}",
            f"Website: {website or 'n/a'}",
            f"Selected sector: {sector_context}",
        ],
    )

    return enriched, {
        "source": "openai",
        "type": "llm",
        "detail": (
            f"Applied GPT enrichment ({settings.openai_model}) for sector narrative, retraining, "
            "and R&D feasibility scoring."
        ),
    }


def extract_contacts_with_llm(
    *,
    company_name: str,
    website: str | None,
    snippets: list[str],
    research_text: str,
    deterministic_contacts: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, str]]:
    settings = get_settings()
    if not settings.gpt_enabled:
        return None, {
            "source": "openai",
            "type": "llm",
            "detail": "OPENAI_API_KEY not configured; used deterministic contact extraction.",
        }

    prompt = f"""
Identify likely owner/decision-maker contact leads from public website evidence.
Do not invent people or emails. Use only evidence in provided text/snippets.

Company: {company_name}
Website: {website or "Not provided"}

Deterministic contact candidates:
{json.dumps(deterministic_contacts or [], ensure_ascii=True)}

Website snippets:
{_clip(chr(10).join(snippets[:25]), 12000)}

Additional text:
{_clip(research_text, 12000)}

Return JSON only:
{{
  "contacts": [
    {{
      "name": "full name or null",
      "title": "role/title or null",
      "email": "public email or null",
      "confidence": 0.0,
      "source_url": "best source page url if known, else null",
      "notes": "short verification note"
    }}
  ]
}}

Rules:
- Return 0 to 5 contacts.
- Prefer owner/founder/CEO/president/managing partner.
- If no leadership contact exists, you may include one general contact email.
- Confidence should be conservative; avoid values above 0.85 unless evidence is explicit.
"""
    data, err = _post_openai(
        prompt,
        "You are a cautious B2B contact researcher. Output valid JSON only and never hallucinate contact details.",
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    if err or not data:
        return None, {
            "source": "openai",
            "type": "llm_error",
            "detail": f"LLM contact extraction failed: {err or 'unknown'}",
        }

    parsed = _extract_json(_response_output_text(data))
    if not parsed:
        return None, {
            "source": "openai",
            "type": "llm_error",
            "detail": "LLM contact extraction returned non-JSON output.",
        }

    contacts: list[dict[str, Any]] = []
    for raw in parsed.get("contacts", []):
        if isinstance(raw, dict):
            normalized = _normalize_contact_row(raw)
            if normalized:
                contacts.append(normalized)
    contacts = contacts[:5]
    return contacts, {
        "source": "openai",
        "type": "llm",
        "detail": f"Applied GPT contact extraction ({settings.openai_model}) from public website evidence.",
    }
