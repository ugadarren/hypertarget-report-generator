from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from app.models import AddressInput, LocationAssessment

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ARCGIS_GEOCODE_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
US_CENSUS_GEOCODE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
US_CENSUS_GEOGRAPHY_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
US_CENSUS_COUNTY_NAME_API = "https://api.census.gov/data/2020/dec/pl"
USER_AGENT = "HyperTargetReportBot/1.0 (+https://localhost)"
ARCGIS_VIEWER_URL = os.getenv(
    "ARCGIS_VIEWER_URL",
    "https://experience.arcgis.com/experience/e655a4ebd5e94cdd9a731822f59d2097",
)
_COUNTY_FIPS_CACHE: dict[str, str] = {}

DEFAULT_CREDIT_POLICY = {
    "jtc": {
        "base_threshold_by_tier": {"1": "+2", "2": "+10", "3": "+15", "4": "+25"},
        "base_amount_by_tier": {
            "1": "$3,500/yr for 5 years",
            "2": "$3,000/yr for 5 years",
            "3": "$1,250/yr for 5 years",
            "4": "$750/yr for 5 years",
        },
        "special_threshold_by_designation": {
            "military_zone": "+2",
            "opportunity_zone": "+2",
            "tier1_lower_40": "+2",
            "ldct": "+5",
        },
        "special_amount_by_designation": {
            "military_zone": "$3,500/yr for 5 years",
            "opportunity_zone": "$3,500/yr for 5 years",
            "tier1_lower_40": "$3,500/yr for 5 years",
            "ldct": "$3,500/yr for 5 years"
        }
    },
    "itc": {
        "pct_by_tier": {
            "1": "5%",
            "2": "3%",
            "3": "3%",
            "4": "1%",
        }
    },
}


def load_county_tiers(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    return {_normalize_county_key(k): str(v) for k, v in data.items()}


def load_county_tier_history(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    output: dict[str, dict[str, str]] = {}
    for year, mapping in data.items():
        if not isinstance(mapping, dict):
            continue
        normalized_year = str(year).strip()
        output[normalized_year] = {}
        for county, tier in mapping.items():
            county_key = _normalize_county_key(str(county))
            if county_key:
                output[normalized_year][county_key] = str(tier).strip()
    return output


def _deep_merge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_credit_policy(path: Path) -> dict:
    if not path.exists():
        return deepcopy(DEFAULT_CREDIT_POLICY)
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return deepcopy(DEFAULT_CREDIT_POLICY)
        return _deep_merge(DEFAULT_CREDIT_POLICY, data)
    except Exception:
        return deepcopy(DEFAULT_CREDIT_POLICY)


def _normalize_county_key(value: str) -> str:
    cleaned = (value or "").lower().replace(" county", "").strip()
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch == " ")
    cleaned = " ".join(cleaned.split())
    return cleaned


def _geocode_with_nominatim(address: str) -> dict | None:
    params = {
        "q": address,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 1,
    }
    try:
        response = requests.get(NOMINATIM_URL, params=params, timeout=12, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        data = response.json()
        return data[0] if data else None
    except Exception:
        return None


def _geocode_with_arcgis(address: str) -> dict | None:
    params = {
        "SingleLine": address,
        "f": "json",
        "outFields": "Match_addr,Addr_type,City,Region,Postal,Country",
        "maxLocations": 1,
    }
    try:
        response = requests.get(ARCGIS_GEOCODE_URL, params=params, timeout=12, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        top = candidates[0]
        location = top.get("location", {})
        attrs = top.get("attributes", {})
        if location.get("y") is None or location.get("x") is None:
            return None
        county = attrs.get("Subregion") or attrs.get("RegionAbbr")
        return {
            "lat": str(location.get("y")),
            "lon": str(location.get("x")),
            "address": {
                "county": county,
                "state": attrs.get("Region"),
                "city": attrs.get("City"),
            },
            "_provider": "arcgis_world_geocoder",
            "_raw": attrs,
        }
    except Exception:
        return None


def _geocode_with_census(address: str) -> dict | None:
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    try:
        response = requests.get(US_CENSUS_GEOCODE_URL, params=params, timeout=12, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        data = response.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        top = matches[0]
        coords = top.get("coordinates", {})
        if coords.get("y") is None or coords.get("x") is None:
            return None
        matched = top.get("matchedAddress", "")
        return {
            "lat": str(coords.get("y")),
            "lon": str(coords.get("x")),
            "address": {
                "state": "Georgia" if " GA " in f" {matched} " or matched.endswith(", GA") else None,
            },
            "_provider": "us_census_geocoder",
            "_raw": top,
        }
    except Exception:
        return None


def geocode_address(address: str) -> dict | None:
    geo = _geocode_with_nominatim(address)
    if geo:
        geo["_provider"] = "nominatim"
        return geo
    geo = _geocode_with_arcgis(address)
    if geo:
        return geo
    geo = _geocode_with_census(address)
    if geo:
        return geo
    return None


class ArcGISIncentiveLookup:
    def __init__(self, viewer_url: str = ARCGIS_VIEWER_URL):
        parsed = urlparse(viewer_url)
        query = parse_qs(parsed.query)
        self.webmap_id = (query.get("webmap") or [""])[0]
        path_parts = [part for part in parsed.path.split("/") if part]
        self.experience_id = path_parts[1] if len(path_parts) >= 2 and path_parts[0] == "experience" else ""
        self.app_id = (query.get("appid") or [""])[0]
        self.portal_base = f"{parsed.scheme}://{parsed.netloc}"
        self.layer_urls: dict[str, str] = {}

    def _json_get(self, url: str, params: dict | None = None) -> dict:
        response = requests.get(url, params=params, timeout=12, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response.json()

    def _match_layer_type(self, title: str) -> str | None:
        name = title.lower()
        if "tier" in name and ("job" in name or "tax" in name or "county" in name):
            return "tier"
        if "lower 40" in name or "lower40" in name:
            return "tier1_lower_40"
        if "military" in name:
            return "military_zone"
        if "ldct" in name or "less developed" in name:
            return "ldct"
        if "opportunity zone" in name or "opzone" in name:
            return "opportunity_zone"
        return None

    def discover_layers(self) -> dict[str, str]:
        if self.layer_urls:
            return self.layer_urls

        if not self.webmap_id and self.experience_id:
            self._resolve_webmap_from_experience()

        if not self.webmap_id:
            return {}

        webmap_data_url = f"{self.portal_base}/sharing/rest/content/items/{self.webmap_id}/data"
        data = self._json_get(webmap_data_url, params={"f": "json"})

        for layer in data.get("operationalLayers", []):
            title = str(layer.get("title") or "")
            layer_url = str(layer.get("url") or "").rstrip("/")
            if not title or not layer_url:
                continue
            layer_type = self._match_layer_type(title)
            if layer_type:
                self.layer_urls[layer_type] = layer_url

        return self.layer_urls

    def _resolve_webmap_from_experience(self) -> None:
        experience_data_url = f"https://www.arcgis.com/sharing/rest/content/items/{self.experience_id}/data"
        try:
            data = self._json_get(experience_data_url, params={"f": "json"})
        except Exception:
            return

        data_sources = data.get("dataSources", {})
        for source in data_sources.values():
            if source.get("type") != "WEB_MAP":
                continue
            item_id = source.get("itemId")
            if not item_id:
                continue
            self.webmap_id = str(item_id)
            portal_url = source.get("portalUrl") or data.get("attributes", {}).get("portalUrl")
            if portal_url:
                self.portal_base = str(portal_url).rstrip("/")
            return

    def point_lookup(self, latitude: float, longitude: float) -> tuple[dict[str, dict], list[dict[str, str]]]:
        layers = self.discover_layers()
        diagnostics: list[dict[str, str]] = []
        if not layers:
            diagnostics.append(
                {
                    "scope": "arcgis",
                    "status": "no_layers",
                    "detail": (
                        "No ArcGIS layers discovered from viewer configuration. "
                        f"experience_id={self.experience_id or 'n/a'} webmap_id={self.webmap_id or 'n/a'}"
                    ),
                }
            )
            return {}, diagnostics

        output: dict[str, dict] = {}
        for layer_type, layer_url in layers.items():
            query_url = f"{layer_url}/query"
            params = {
                "f": "json",
                "where": "1=1",
                "geometry": f"{longitude},{latitude}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "false",
                "resultRecordCount": "1",
            }

            try:
                data = self._json_get(query_url, params=params)
                features = data.get("features", [])
                if features:
                    output[layer_type] = features[0].get("attributes", {})
                    diagnostics.append(
                        {
                            "scope": layer_type,
                            "status": "matched",
                            "detail": f"Matched polygon from {layer_url}",
                        }
                    )
                else:
                    diagnostics.append(
                        {
                            "scope": layer_type,
                            "status": "no_match",
                            "detail": f"No intersecting feature from {layer_url}",
                        }
                    )
            except Exception:
                diagnostics.append(
                    {
                        "scope": layer_type,
                        "status": "query_error",
                        "detail": f"ArcGIS query failed for {layer_url}",
                    }
                )
                continue

        return output, diagnostics


def _extract_tier_from_attrs(attrs: dict[str, str]) -> str | None:
    for key, value in attrs.items():
        key_l = str(key).lower()
        if "tier" in key_l and value not in (None, ""):
            return _normalize_tier_value(str(value))
    return None


def _extract_tier_label_from_attrs(attrs: dict[str, str]) -> str | None:
    for key, value in attrs.items():
        if value in (None, ""):
            continue
        key_l = str(key).lower()
        val = str(value).strip()
        if "tier" in key_l:
            return val
    for key, value in attrs.items():
        if value in (None, ""):
            continue
        key_l = str(key).lower()
        val = str(value).strip()
        if "designation" in key_l and ("tier" in val.lower() or "lower 40" in val.lower() or "bottom 40" in val.lower()):
            return val
    return None


def _extract_lower_40_from_attrs(attrs: dict[str, str]) -> bool | None:
    for key, value in attrs.items():
        if value in (None, ""):
            continue
        key_l = str(key).lower()
        val = str(value).lower()
        if "lower" in key_l and "40" in key_l:
            if val in {"1", "true", "yes", "y"}:
                return True
            if val in {"0", "false", "no", "n"}:
                return False
        if "lower 40" in val or "lower40" in val:
            return True
    return None


def _extract_county_from_attrs(attrs: dict[str, str]) -> str | None:
    for key, value in attrs.items():
        if value in (None, ""):
            continue
        key_l = str(key).lower()
        if "county" in key_l or "cnty" in key_l:
            text = str(value).replace(" County", "").strip()
            if text:
                return text
    return None


def _county_name_from_fips(county_fips: str) -> str | None:
    code = "".join(ch for ch in str(county_fips) if ch.isdigit()).zfill(3)
    if not code:
        return None
    if code in _COUNTY_FIPS_CACHE:
        return _COUNTY_FIPS_CACHE[code]
    params = {
        "get": "NAME",
        "for": f"county:{code}",
        "in": "state:13",  # Georgia
    }
    try:
        response = requests.get(US_CENSUS_COUNTY_NAME_API, params=params, timeout=12, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or len(data) < 2 or len(data[1]) < 1:
            return None
        name = str(data[1][0]).replace(" County, Georgia", "").replace(" County", "").strip()
        if not name:
            return None
        _COUNTY_FIPS_CACHE[code] = name
        return name
    except Exception:
        return None


def _normalize_county_name(county: str | None) -> str | None:
    if not county:
        return None
    raw = str(county).strip()
    if not raw:
        return None
    numeric = "".join(ch for ch in raw if ch.isdigit())
    if raw.isdigit() or (numeric and len(numeric) <= 3 and numeric == raw):
        return _county_name_from_fips(numeric) or raw
    return raw.replace(" County", "").strip()


def _tier_from_county(county: str | None, tier_map: dict[str, str]) -> str | None:
    if not county:
        return None
    value = tier_map.get(_normalize_county_key(county))
    return _normalize_tier_value(value)


def _normalize_tier_value(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text_l = text.lower()
    if "lower 40" in text_l or "lower40" in text_l or "bottom 40" in text_l or "bottom40" in text_l:
        return "1"
    match = re.search(r"\b([1-4])\b", text)
    if match:
        return match.group(1)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 1 and digits in {"1", "2", "3", "4"}:
        return digits
    lowered = text.lower().replace("tier", "").strip()
    return lowered or None


def _display_tier_label(tier_value: str | None, tier_label_raw: str | None, tier1_lower_40: bool | None) -> str | None:
    raw = (tier_label_raw or "").strip()
    raw_l = raw.lower()
    if raw and ("lower 40" in raw_l or "bottom 40" in raw_l):
        return "Tier 1 Lower 40"
    if tier1_lower_40:
        return "Tier 1 Lower 40"
    normalized = _normalize_tier_value(tier_value)
    if normalized in {"1", "2", "3", "4"}:
        return f"Tier {normalized}"
    return raw or None


def _history_tier_label(raw_tier: str) -> str:
    tier = _normalize_tier_value(raw_tier)
    raw_l = str(raw_tier).lower()
    if "lower 40" in raw_l or "bottom 40" in raw_l:
        return "Tier 1 Lower 40"
    if tier in {"1", "2", "3", "4"}:
        return f"Tier {tier}"
    return str(raw_tier)


def _build_tier_history(
    county: str | None,
    tier_history_by_year: dict[str, dict[str, str]] | None,
    reference_year: int | None,
    years_back: int = 5,
) -> list[str]:
    if not county or not tier_history_by_year:
        return []
    county_key = _normalize_county_key(county)
    year_values: list[int] = []
    for year in tier_history_by_year.keys():
        if str(year).isdigit():
            year_values.append(int(year))
    if not year_values:
        return []
    ref = reference_year if reference_year is not None else max(year_values)
    selected = sorted([y for y in year_values if y <= ref], reverse=True)[: years_back + 1]
    history: list[str] = []
    for year in selected:
        tier_raw = tier_history_by_year.get(str(year), {}).get(county_key)
        if not tier_raw:
            continue
        history.append(f"{year}: {_history_tier_label(tier_raw)}")
    return history


def _county_from_address_text(address_text: str, tier_map: dict[str, str]) -> str | None:
    lower_text = address_text.lower()
    for county_key in tier_map.keys():
        if f"{county_key} county" in lower_text:
            return county_key.title()
        if county_key in lower_text:
            return county_key.title()
    return None


def _county_from_census_geography(address: str) -> str | None:
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    }
    try:
        response = requests.get(US_CENSUS_GEOGRAPHY_URL, params=params, timeout=12, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        data = response.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        geographies = matches[0].get("geographies", {})
        counties = geographies.get("Counties", [])
        if not counties:
            return None
        name = str(counties[0].get("NAME", "")).replace(" County", "").strip()
        return name or None
    except Exception:
        return None


def _format_special_designation(
    tier1_lower_40: bool | None,
    military_zone: bool | None,
    ldct: bool | None,
    opportunity_zone: bool | None,
) -> str:
    tags: list[str] = []
    if tier1_lower_40:
        tags.append("Tier 1 Lower 40")
    if military_zone:
        tags.append("Military Zone")
    if ldct:
        tags.append("LDCT")
    if opportunity_zone:
        tags.append("Opportunity Zone")
    return " & ".join(tags) if tags else "None"


def _estimate_jtc_benefit(
    tier_value: str | None,
    military_zone: bool | None,
    ldct: bool | None,
    opportunity_zone: bool | None,
    tier1_lower_40: bool | None,
    credit_policy: dict | None = None,
) -> tuple[str, str]:
    policy = credit_policy or DEFAULT_CREDIT_POLICY
    jtc = policy.get("jtc", {})
    has_special_designation = any([military_zone, ldct, opportunity_zone, tier1_lower_40])
    if not tier_value:
        return ("Unavailable", "Unavailable")
    normalized = "".join(ch for ch in str(tier_value) if ch.isdigit())
    tier_num = int(normalized) if normalized else None
    if not tier_num:
        return ("Unavailable", "Unavailable")

    base_threshold_by_tier = jtc.get("base_threshold_by_tier", {})
    base_amount_by_tier = jtc.get("base_amount_by_tier", {})

    if has_special_designation:
        threshold_by_designation = jtc.get("special_threshold_by_designation", {})
        amount_by_designation = jtc.get("special_amount_by_designation", {})
        threshold_candidates: list[str] = []
        amount_candidates: list[str] = []
        if military_zone:
            threshold = str(threshold_by_designation.get("military_zone", ""))
            amount = str(amount_by_designation.get("military_zone", ""))
            if threshold:
                threshold_candidates.append(threshold)
            if amount:
                amount_candidates.append(amount)
        if opportunity_zone:
            threshold = str(threshold_by_designation.get("opportunity_zone", ""))
            amount = str(amount_by_designation.get("opportunity_zone", ""))
            if threshold:
                threshold_candidates.append(threshold)
            if amount:
                amount_candidates.append(amount)
        if tier1_lower_40:
            threshold = str(threshold_by_designation.get("tier1_lower_40", ""))
            amount = str(amount_by_designation.get("tier1_lower_40", ""))
            if threshold:
                threshold_candidates.append(threshold)
            if amount:
                amount_candidates.append(amount)
        if ldct:
            threshold = str(threshold_by_designation.get("ldct", ""))
            amount = str(amount_by_designation.get("ldct", ""))
            if threshold:
                threshold_candidates.append(threshold)
            if amount:
                amount_candidates.append(amount)
        if threshold_candidates:
            special_threshold = min(
                threshold_candidates,
                key=lambda t: int("".join(ch for ch in t if ch.isdigit()) or "999"),
            )
        else:
            special_threshold = "+2"

        if amount_candidates:
            special_amount = max(
                amount_candidates,
                key=lambda amount: int("".join(ch for ch in amount if ch.isdigit()) or "0"),
            )
        else:
            # Backward compatibility for older policy files that still store special amounts by tier.
            special_amount = jtc.get("special_amount_by_tier", {}).get(str(tier_num), "Unavailable")
        return (special_threshold, special_amount)

    return (
        base_threshold_by_tier.get(str(tier_num), "Unavailable"),
        base_amount_by_tier.get(str(tier_num), "Unavailable"),
    )


def _investment_credit_pct_for_tier(tier_value: str | None, credit_policy: dict | None = None) -> str | None:
    policy = credit_policy or DEFAULT_CREDIT_POLICY
    if not tier_value:
        return None
    normalized = "".join(ch for ch in str(tier_value) if ch.isdigit())
    if not normalized:
        return None
    tier_num = int(normalized)
    percentages = policy.get("itc", {}).get("pct_by_tier", {})
    return percentages.get(str(tier_num))


def assess_locations(
    addresses: list[AddressInput],
    tier_map: dict[str, str],
    credit_policy: dict | None = None,
    tier_history_by_year: dict[str, dict[str, str]] | None = None,
    reference_year: int | None = None,
) -> list[LocationAssessment]:
    results: list[LocationAssessment] = []
    arcgis = ArcGISIncentiveLookup()

    for item in addresses:
        geo = geocode_address(item.raw)
        if not geo:
            fallback_county = _county_from_address_text(item.raw, tier_map)
            if not fallback_county:
                fallback_county = _county_from_census_geography(item.raw)
            fallback_tier = _tier_from_county(fallback_county, tier_map) if fallback_county else None
            fallback_evidence = ["Unable to geocode address automatically"]
            if fallback_county and fallback_tier:
                fallback_evidence.append("Manual fallback matched county name from entered address text")
            elif fallback_county:
                fallback_evidence.append("Census geography resolved county, but tier map did not match")
            results.append(
                LocationAssessment(
                    address=item.raw,
                    county=fallback_county,
                    ga_tier=fallback_tier,
                    confidence=0.5 if fallback_tier else 0.1,
                    evidence=fallback_evidence,
                    zone_details={
                        "_diagnostics": [
                            {
                                "scope": "geocode",
                                "status": "failed",
                                "detail": "Address could not be geocoded by Nominatim, ArcGIS World Geocoder, or U.S. Census Geocoder.",
                            }
                        ]
                    },
                )
            )
            continue

        addr = geo.get("address", {})
        county = (addr.get("county") or "").replace(" County", "").strip() or None
        county = _normalize_county_name(county)
        state = addr.get("state")
        lat = float(geo["lat"]) if geo.get("lat") else None
        lon = float(geo["lon"]) if geo.get("lon") else None

        tier = _tier_from_county(county, tier_map)
        tier_label_raw = None
        military_zone = None
        ldct = None
        opportunity_zone = None
        tier1_lower_40 = None
        zone_details: dict[str, dict] = {}

        evidence = ["OpenStreetMap Nominatim geocoding"]
        provider = str(geo.get("_provider") or "unknown")
        if provider != "nominatim":
            evidence = [f"{provider} geocoding"]
        diagnostics: list[dict[str, str]] = [
            {"scope": "geocode", "status": "ok", "detail": f"Address geocoded successfully with {provider}."}
        ]

        if lat is not None and lon is not None:
            zone_details, arcgis_diag = arcgis.point_lookup(lat, lon)
            diagnostics.extend(arcgis_diag)
            if zone_details:
                evidence.append("ArcGIS DCA map spatial intersection")

            if zone_details.get("tier"):
                tier = _extract_tier_from_attrs(zone_details["tier"]) or tier
                tier_label_raw = _extract_tier_label_from_attrs(zone_details["tier"]) or tier_label_raw
                lower_40 = _extract_lower_40_from_attrs(zone_details["tier"])
                if lower_40 is not None:
                    tier1_lower_40 = lower_40
                if not county:
                    county = _extract_county_from_attrs(zone_details["tier"]) or county
            if zone_details.get("tier1_lower_40"):
                tier1_lower_40 = True
                if not tier:
                    tier = "1"
            if not county and zone_details.get("military_zone"):
                county = _extract_county_from_attrs(zone_details["military_zone"]) or county
            if not county and zone_details.get("ldct"):
                county = _extract_county_from_attrs(zone_details["ldct"]) or county
            if not county and zone_details.get("opportunity_zone"):
                county = _extract_county_from_attrs(zone_details["opportunity_zone"]) or county
            county = _normalize_county_name(county)
            military_zone = bool(zone_details.get("military_zone")) if "military_zone" in zone_details else None
            ldct = bool(zone_details.get("ldct")) if "ldct" in zone_details else None
            opportunity_zone = bool(zone_details.get("opportunity_zone")) if "opportunity_zone" in zone_details else None

        if county and tier and "ArcGIS DCA map spatial intersection" not in evidence:
            evidence.append("Matched county to GA tier map")
        elif county and not tier:
            evidence.append("County identified but not mapped to GA tier (verify county/state)")

        if not county:
            guessed_county = _county_from_address_text(item.raw, tier_map)
            if not guessed_county:
                guessed_county = _county_from_census_geography(item.raw)
            if guessed_county:
                county = guessed_county
                tier = _tier_from_county(county, tier_map)
                evidence.append("Fallback county lookup resolved county for entered address")

        if state and "georgia" not in state.lower() and tier is None:
            evidence.append("Address appears outside Georgia; GA tier may not apply")

        confidence = 0.92 if county and state else 0.6
        tier_label = _display_tier_label(tier, tier_label_raw, tier1_lower_40)
        tier_history = _build_tier_history(
            county=county,
            tier_history_by_year=tier_history_by_year,
            reference_year=reference_year,
            years_back=5,
        )

        threshold, credit_amount = _estimate_jtc_benefit(
            tier,
            military_zone,
            ldct,
            opportunity_zone,
            tier1_lower_40,
            credit_policy=credit_policy,
        )

        results.append(
            LocationAssessment(
                address=item.raw,
                county=county,
                state=state,
                latitude=lat,
                longitude=lon,
                ga_tier=tier,
                ga_tier_label=tier_label,
                military_zone=military_zone,
                ldct=ldct,
                opportunity_zone=opportunity_zone,
                tier1_lower_40=tier1_lower_40,
                special_designation=_format_special_designation(
                    tier1_lower_40,
                    military_zone,
                    ldct,
                    opportunity_zone,
                ),
                job_creation_threshold=threshold,
                per_job_credit_amount=credit_amount,
                investment_tax_credit_pct=_investment_credit_pct_for_tier(tier, credit_policy=credit_policy),
                tier_history=tier_history,
                zone_details={**zone_details, "_diagnostics": diagnostics},
                confidence=confidence,
                evidence=evidence,
            )
        )

    return results
