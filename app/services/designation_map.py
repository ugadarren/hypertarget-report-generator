from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from app.services.location import ARCGIS_VIEWER_URL, USER_AGENT


@dataclass
class DesignationDefinition:
    id: str
    label: str
    layer_url: str
    where: str
    color: str


class ArcGISDesignationService:
    def __init__(self, viewer_url: str = ARCGIS_VIEWER_URL):
        parsed = urlparse(viewer_url)
        query = parse_qs(parsed.query)
        self.webmap_id = (query.get("webmap") or [""])[0]
        path_parts = [part for part in parsed.path.split("/") if part]
        self.experience_id = path_parts[1] if len(path_parts) >= 2 and path_parts[0] == "experience" else ""
        self.portal_base = f"{parsed.scheme}://{parsed.netloc}"
        self._catalog: dict[str, dict[str, str]] | None = None

    def _json_get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(url, params=params, timeout=16, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response.json()

    def _match_layer_type(self, title: str) -> str | None:
        normalized = title.lower()
        if "job tax credit county tier" in normalized:
            return "tier_county"
        if "military" in normalized:
            return "military_zone"
        if "less developed census tract" in normalized or "ldct" in normalized:
            return "ldct"
        if "opportunity zone" in normalized:
            return "opportunity_zone"
        if "rural zone" in normalized:
            return "rural_zone"
        return None

    def _load_catalog(self) -> dict[str, dict[str, str]]:
        if self._catalog is not None:
            return self._catalog
        if not self.webmap_id and self.experience_id:
            self._resolve_webmap_from_experience()
        if not self.webmap_id:
            self._catalog = {}
            return self._catalog

        webmap_data_url = f"{self.portal_base}/sharing/rest/content/items/{self.webmap_id}/data"
        data = self._json_get(webmap_data_url, params={"f": "json"})

        catalog: dict[str, dict[str, str]] = {}
        for layer in data.get("operationalLayers", []):
            title = str(layer.get("title") or "")
            layer_url = str(layer.get("url") or "").rstrip("/")
            if not title or not layer_url:
                continue
            layer_type = self._match_layer_type(title)
            if not layer_type:
                continue
            catalog[layer_type] = {"title": title, "url": layer_url}

        self._catalog = catalog
        return catalog

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

    @lru_cache(maxsize=16)
    def _resolve_tier_field(self, layer_url: str) -> str | None:
        layer_info = self._json_get(layer_url, params={"f": "json"})
        field_names = [str(field.get("name") or "") for field in layer_info.get("fields", [])]
        priority = [name for name in field_names if name.lower().startswith("tier") and len(name) >= 5]
        if priority:
            return priority[0]
        for name in field_names:
            if "tier" in name.lower():
                return name
        return None

    def get_designation_definitions(self) -> list[DesignationDefinition]:
        catalog = self._load_catalog()
        definitions: list[DesignationDefinition] = []

        tier_layer = catalog.get("tier_county")
        if tier_layer:
            tier_field = self._resolve_tier_field(tier_layer["url"])
            if tier_field:
                definitions.extend(
                    [
                        DesignationDefinition(
                            id="tier1_county",
                            label="Tier 1 Counties",
                            layer_url=tier_layer["url"],
                            where=f"{tier_field} = 'Tier 1'",
                            color="#c89220",
                        ),
                        DesignationDefinition(
                            id="bottom40_county",
                            label="Bottom 40 Counties",
                            layer_url=tier_layer["url"],
                            where=f"{tier_field} = 'Bottom 40'",
                            color="#5d6c80",
                        ),
                        DesignationDefinition(
                            id="tier1_bottom40_county",
                            label="Tier 1 + Bottom 40 Counties",
                            layer_url=tier_layer["url"],
                            where=f"{tier_field} IN ('Tier 1', 'Bottom 40')",
                            color="#7a5cc7",
                        ),
                    ]
                )

        base_defs: list[tuple[str, str, str, str]] = [
            ("military_zone", "Military Zones", "military_zone", "#666666"),
            ("ldct", "Less Developed Census Tracts (LDCT)", "ldct", "#ff73df"),
            ("opportunity_zone", "Opportunity Zones", "opportunity_zone", "#7cb342"),
            ("rural_zone", "Rural Zones", "rural_zone", "#0079c1"),
        ]
        for designation_id, label, layer_key, color in base_defs:
            layer = catalog.get(layer_key)
            if not layer:
                continue
            definitions.append(
                DesignationDefinition(
                    id=designation_id,
                    label=label,
                    layer_url=layer["url"],
                    where="1=1",
                    color=color,
                )
            )

        return definitions

    def query_designation_features(
        self,
        designation_ids: list[str],
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> dict[str, dict[str, Any]]:
        definitions_by_id = {item.id: item for item in self.get_designation_definitions()}
        output: dict[str, dict[str, Any]] = {}
        for designation_id in designation_ids:
            definition = definitions_by_id.get(designation_id)
            if not definition:
                continue

            width = abs(max_lon - min_lon)
            max_allowable_offset = min(max(width / 450.0, 0.00005), 0.02)
            params = {
                "f": "json",
                "where": definition.where,
                "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "maxAllowableOffset": str(max_allowable_offset),
                "resultRecordCount": "200",
            }
            query_url = f"{definition.layer_url}/query"

            try:
                raw = self._json_get(query_url, params=params)
            except Exception:
                continue

            features: list[dict[str, Any]] = []
            for feature in raw.get("features", []):
                geometry = feature.get("geometry") or {}
                rings = geometry.get("rings")
                if not rings:
                    continue
                attrs = feature.get("attributes", {})
                features.append(
                    {
                        "name": self._feature_name(attrs),
                        "rings": rings,
                    }
                )

            output[designation_id] = {
                "label": definition.label,
                "color": definition.color,
                "features": features,
            }

        return output

    def _feature_name(self, attrs: dict[str, Any]) -> str:
        candidates = [
            "County",
            "NAME",
            "Name",
            "NAMELSAD",
            "Tname",
            "RZName",
            "FULLNAME",
        ]
        for key in candidates:
            value = attrs.get(key)
            if value not in (None, ""):
                return str(value)
        for key, value in attrs.items():
            if value not in (None, "") and key.lower() in {"county", "name"}:
                return str(value)
        return "Unnamed designation"
