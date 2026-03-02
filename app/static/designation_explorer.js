const explorerState = {
  map: null,
  placesService: null,
  designationDefs: [],
  selectedIds: new Set(),
  designationPolygons: new Map(),
  businessMarkers: [],
  lastPlaces: [],
  autoRefreshTimer: null,
};

function byId(id) {
  return document.getElementById(id);
}

function clearMarkers() {
  for (const marker of explorerState.businessMarkers) {
    marker.setMap(null);
  }
  explorerState.businessMarkers = [];
}

function clearDesignationPolygons() {
  for (const polygonSet of explorerState.designationPolygons.values()) {
    for (const polygon of polygonSet) {
      polygon.setMap(null);
    }
  }
  explorerState.designationPolygons.clear();
}

function updateSummary(text) {
  byId("results-summary").textContent = text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function fetchDesignationPayload() {
  const params = new URLSearchParams();
  const ids = Array.from(explorerState.selectedIds);
  if (ids.length > 0) {
    const bounds = explorerState.map.getBounds();
    if (!bounds) {
      return null;
    }
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    params.set("ids", ids.join(","));
    params.set("min_lat", String(sw.lat()));
    params.set("min_lon", String(sw.lng()));
    params.set("max_lat", String(ne.lat()));
    params.set("max_lon", String(ne.lng()));
  }

  const response = await fetch(`/api/designations?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Unable to load designations");
  }
  return response.json();
}

function renderDesignationList(designations) {
  const container = byId("designation-list");
  container.innerHTML = "";
  if (!designations.length) {
    container.innerHTML = "<p class='small-note'>No designation layers found from ArcGIS source.</p>";
    return;
  }

  for (const designation of designations) {
    const label = document.createElement("label");
    label.className = "designation-row";
    label.innerHTML = `
      <input type="checkbox" value="${designation.id}" />
      <span class="chip" style="background:${designation.color}"></span>
      ${escapeHtml(designation.label)}
    `;
    const checkbox = label.querySelector("input");
    checkbox.addEventListener("change", async (event) => {
      if (event.target.checked) {
        explorerState.selectedIds.add(designation.id);
      } else {
        explorerState.selectedIds.delete(designation.id);
      }
      await refreshDesignationsAndResults();
    });
    container.appendChild(label);
  }
}

function drawDesignationPolygons(featuresById) {
  clearDesignationPolygons();
  for (const [designationId, info] of Object.entries(featuresById || {})) {
    const polygons = [];
    for (const feature of info.features || []) {
      for (const ring of feature.rings || []) {
        const path = ring.map((point) => ({ lng: point[0], lat: point[1] }));
        const polygon = new google.maps.Polygon({
          paths: path,
          strokeColor: info.color,
          strokeOpacity: 0.95,
          strokeWeight: 1.1,
          fillColor: info.color,
          fillOpacity: 0.18,
          map: explorerState.map,
          clickable: false,
        });
        polygons.push(polygon);
      }
    }
    explorerState.designationPolygons.set(designationId, polygons);
  }
}

function pointMatchesDesignations(point) {
  if (explorerState.selectedIds.size === 0) {
    return { included: true, matched: [] };
  }
  const matched = [];
  for (const designationId of explorerState.selectedIds) {
    const polygons = explorerState.designationPolygons.get(designationId) || [];
    for (const polygon of polygons) {
      if (google.maps.geometry.poly.containsLocation(point, polygon)) {
        matched.push(designationId);
        break;
      }
    }
  }
  return { included: matched.length > 0, matched };
}

function renderBusinessResults() {
  clearMarkers();
  const listEl = byId("results-list");
  listEl.innerHTML = "";

  const filtered = [];
  for (const place of explorerState.lastPlaces) {
    const location = place.geometry?.location;
    if (!location) {
      continue;
    }
    const inclusion = pointMatchesDesignations(location);
    if (!inclusion.included) {
      continue;
    }
    filtered.push({ place, matched: inclusion.matched });
  }

  updateSummary(`${filtered.length} businesses found in current map + selected designations.`);

  for (const item of filtered) {
    const place = item.place;
    const marker = new google.maps.Marker({
      map: explorerState.map,
      position: place.geometry.location,
      title: place.name || "",
    });
    explorerState.businessMarkers.push(marker);

    const li = document.createElement("li");
    const designationTag =
      item.matched.length > 0
        ? ` | In: ${item.matched.join(", ")}`
        : "";
    li.innerHTML = `<strong>${escapeHtml(place.name || "Unnamed")}</strong><br>${escapeHtml(
      place.formatted_address || place.vicinity || ""
    )}${escapeHtml(designationTag)}`;
    listEl.appendChild(li);
  }
}

async function refreshDesignationsAndResults() {
  const payload = await fetchDesignationPayload();
  if (!payload) {
    return;
  }
  if (!explorerState.designationDefs.length) {
    explorerState.designationDefs = payload.designations || [];
    renderDesignationList(explorerState.designationDefs);
  }
  drawDesignationPolygons(payload.features || {});
  renderBusinessResults();
}

function runBusinessSearch() {
  const query = (byId("business-query").value || "").trim() || "business";
  const bounds = explorerState.map.getBounds();
  if (!bounds) {
    return;
  }
  updateSummary("Searching Google Maps businesses...");

  explorerState.placesService.textSearch(
    { query, bounds },
    (results, status) => {
      if (status !== google.maps.places.PlacesServiceStatus.OK || !results) {
        explorerState.lastPlaces = [];
        renderBusinessResults();
        return;
      }
      explorerState.lastPlaces = results;
      renderBusinessResults();
    }
  );
}

window.initDesignationExplorer = async function initDesignationExplorer() {
  explorerState.map = new google.maps.Map(byId("map"), {
    center: { lat: 32.8, lng: -83.5 },
    zoom: 7,
    mapTypeControl: false,
    streetViewControl: false,
  });
  explorerState.placesService = new google.maps.places.PlacesService(explorerState.map);

  byId("search-btn").addEventListener("click", runBusinessSearch);
  byId("business-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runBusinessSearch();
    }
  });

  explorerState.map.addListener("idle", async () => {
    await refreshDesignationsAndResults();
    if (!byId("auto-refresh").checked) {
      return;
    }
    if (explorerState.autoRefreshTimer) {
      clearTimeout(explorerState.autoRefreshTimer);
    }
    explorerState.autoRefreshTimer = setTimeout(runBusinessSearch, 700);
  });

  await refreshDesignationsAndResults();
};

if (!window.HYPERTARGET_CONFIG?.hasGoogleMapsKey) {
  updateSummary("Google Maps key missing. Set GOOGLE_MAPS_API_KEY to use explorer.");
}
