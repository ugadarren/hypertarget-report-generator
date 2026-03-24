from __future__ import annotations

import json
from pathlib import Path


DEFAULT_REPORT_COPY = {
    "ga_jtc_intro": "",
    "ga_jtc_note": "{company_name}'s Georgia location(s) are listed below with their corresponding tier designations and estimated credit benefits.",
    "retraining_intro": "",
    "retraining_intro_lead": "",
    "retraining_intro_emphasis": "",
    "retraining_intro_tail": "",
    "retraining_context": "",
    "rd_intro": "",
    "rd_examples_intro": "Below are potential qualifying activities for {company_name} that are specific to the industry in which you operate.",
    "investment_intro": "",
    "investment_note": "The County, tier, and potential investment credit percentage are listed below for {company_name}'s location(s).",
    "costseg_intro": "",
    "costseg_note": "",
    "contact_intro": "",
}


def load_report_copy(path: Path) -> dict[str, str]:
    if not path.exists():
        return dict(DEFAULT_REPORT_COPY)
    try:
        data = json.loads(path.read_text())
    except Exception:
        return dict(DEFAULT_REPORT_COPY)
    if not isinstance(data, dict):
        return dict(DEFAULT_REPORT_COPY)
    output = dict(DEFAULT_REPORT_COPY)
    for key, value in data.items():
        if isinstance(value, str):
            output[str(key)] = value.strip()
    return output


def apply_report_copy_template(text: str, *, company_name: str) -> str:
    try:
        return (text or "").format(company_name=company_name)
    except Exception:
        return text or ""
