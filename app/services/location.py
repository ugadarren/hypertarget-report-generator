from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from app.models import AddressInput, LocationAssessment

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "HyperTargetReportBot/1.0 (+https://localhost)"
ARCGIS_VIEWER_URL = os.getenv(
    "ARCGIS_VIEWER_URL",
    (
        "https://georgia-dca.maps.arcgis.com/apps/Viewer/index.html"
        "?appid=7b71e8dac0bb4ae48118c1cf3108d61d"
        "&webmap=2562d9f7a70b4042b978bf05f28938b2"
    ),
)


def load_county_tiers(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    return {k.lower(): str(v) for k, v in data.items()}


def geocode_address(address: str) -> dict | None:
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

    def point_lookup(self, latitude: float, longitude: float) -> dict[str, dict]:
        layers = self.discover_layers()
        if not layers:
            return {}

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
            except Exception:
                continue

        return output


def _extract_tier_from_attrs(attrs: dict[str, str]) -> str | None:
    for key, value in attrs.items():
        key_l = str(key).lower()
        if "tier" in key_l and value not in (None, ""):
            return str(value)
    return None


def _format_special_designation(military_zone: bool | None, ldct: bool | None, opportunity_zone: bool | None) -> str:
    tags: list[str] = []
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
) -> tuple[str, str]:
    # Conservative estimation model used for automated drafting.
    has_special = any([military_zone, ldct, opportunity_zone])
    if not tier_value:
        return ("NAICS Dependent", "NAICS Dependent")
    normalized = "".join(ch for ch in str(tier_value) if ch.isdigit())
    tier_num = int(normalized) if normalized else None
    if not tier_num:
        return ("NAICS Dependent", "NAICS Dependent")

    if has_special:
        amount_by_tier = {
            1: "$4000/yr for 5 years",
            2: "$3500/yr for 5 years",
            3: "$2500/yr for 5 years",
            4: "$1750/yr for 5 years",
        }
        return ("+2", amount_by_tier.get(tier_num, "NAICS Dependent"))

    return ("NAICS Dependent", "NAICS Dependent")


def assess_locations(addresses: list[AddressInput], tier_map: dict[str, str]) -> list[LocationAssessment]:
    results: list[LocationAssessment] = []
    arcgis = ArcGISIncentiveLookup()

    for item in addresses:
        geo = geocode_address(item.raw)
        if not geo:
            results.append(
                LocationAssessment(
                    address=item.raw,
                    confidence=0.1,
                    evidence=["Unable to geocode address automatically"],
                )
            )
            continue

        addr = geo.get("address", {})
        county = (addr.get("county") or "").replace(" County", "").strip() or None
        state = addr.get("state")
        lat = float(geo["lat"]) if geo.get("lat") else None
        lon = float(geo["lon"]) if geo.get("lon") else None

        tier = tier_map.get(county.lower()) if county else None
        military_zone = None
        ldct = None
        opportunity_zone = None
        zone_details: dict[str, dict] = {}

        evidence = ["OpenStreetMap Nominatim geocoding"]

        if lat is not None and lon is not None:
            zone_details = arcgis.point_lookup(lat, lon)
            if zone_details:
                evidence.append("ArcGIS DCA map spatial intersection")

            if zone_details.get("tier"):
                tier = _extract_tier_from_attrs(zone_details["tier"]) or tier
            military_zone = bool(zone_details.get("military_zone")) if "military_zone" in zone_details else None
            ldct = bool(zone_details.get("ldct")) if "ldct" in zone_details else None
            opportunity_zone = bool(zone_details.get("opportunity_zone")) if "opportunity_zone" in zone_details else None

        if county and tier and "ArcGIS DCA map spatial intersection" not in evidence:
            evidence.append("Matched county to GA tier map")

        confidence = 0.92 if county and state else 0.6

        threshold, credit_amount = _estimate_jtc_benefit(tier, military_zone, ldct, opportunity_zone)

        results.append(
            LocationAssessment(
                address=item.raw,
                county=county,
                state=state,
                latitude=lat,
                longitude=lon,
                ga_tier=tier,
                military_zone=military_zone,
                ldct=ldct,
                opportunity_zone=opportunity_zone,
                special_designation=_format_special_designation(military_zone, ldct, opportunity_zone),
                job_creation_threshold=threshold,
                per_job_credit_amount=credit_amount,
                zone_details=zone_details,
                confidence=confidence,
                evidence=evidence,
            )
        )

    return results
