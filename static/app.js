const MOROCCO_CENTER = [31.7917, -7.0926];
const MOROCCO_BOUNDS = [
  [27.0, -14.0],
  [36.2, -0.5],
];
const MIN_ZOOM_FOR_BUILDINGS = 16;

const map = L.map("map", {
  center: MOROCCO_CENTER,
  zoom: 6,
  minZoom: 5,
  maxZoom: 22,
  maxBounds: MOROCCO_BOUNDS,
  maxBoundsViscosity: 0.8,
});

const streetLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 22,
  maxNativeZoom: 19,
});

const satelliteLayer = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    attribution:
      "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    maxZoom: 22,
    maxNativeZoom: 19,
  }
);

streetLayer.addTo(map);

const buildingsLayer = L.geoJSON(null, {
  style: () => ({
    color: "#2e7d32",
    weight: 1,
    fillColor: "#66bb6a",
    fillOpacity: 0.45,
  }),
  onEachFeature: (feature, layer) => {
    layer.on({
      mouseover: (e) => {
        e.target.setStyle({ fillOpacity: 0.8, weight: 2, color: "#ffb300" });
        showTooltip(e, feature.properties);
      },
      mousemove: (e) => moveTooltip(e),
      mouseout: (e) => {
        buildingsLayer.resetStyle(e.target);
        hideTooltip();
      },
    });
  },
}).addTo(map);

const segmentationLayer = L.geoJSON(null, {
  style: (feature) =>
    feature.properties.source === "manual-trace"
      ? { color: "#2979ff", weight: 2, dashArray: "6 4", fillColor: "#82b1ff", fillOpacity: 0.35 }
      : { color: "#ff5252", weight: 2, dashArray: "6 4", fillColor: "#ff8a80", fillOpacity: 0.35 },
  onEachFeature: (feature, layer) => {
    const name =
      feature.properties.source === "manual-trace"
        ? "Toit tracé manuellement (clic pour supprimer)"
        : "Bâtiment détecté par IA (clic pour supprimer)";
    layer.on({
      mouseover: (e) => showTooltip(e, { ...feature.properties, name }),
      mousemove: (e) => moveTooltip(e),
      mouseout: () => hideTooltip(),
      click: (e) => {
        L.DomEvent.stopPropagation(e);
        deleteIaSegment(feature.id, layer);
      },
    });
  },
}).addTo(map);

const companiesLayer = L.geoJSON(null, {
  pointToLayer: (feature, latlng) =>
    L.circleMarker(latlng, {
      radius: 6,
      color: "#e65100",
      weight: 2,
      fillColor: "#ffa726",
      fillOpacity: 0.9,
    }),
  onEachFeature: (feature, layer) => {
    layer.on({
      mouseover: (e) => {
        e.target.setStyle({ radius: 8, weight: 3 });
        showCompanyTooltip(e, feature.properties);
      },
      mousemove: (e) => moveTooltip(e),
      mouseout: (e) => {
        e.target.setStyle({ radius: 6, weight: 2 });
        hideTooltip();
      },
    });
  },
}).addTo(map);

L.control
  .layers(
    { "Plan": streetLayer, "Satellite": satelliteLayer },
    { "Entreprises": companiesLayer }
  )
  .addTo(map);

const loadedCompanyIds = new Set();

async function loadCompanies() {
  const bounds = map.getBounds();
  const params = new URLSearchParams({
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast(),
  });

  try {
    const resp = await fetch(`/api/companies?${params.toString()}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const newFeatures = data.features.filter((f) => {
      if (loadedCompanyIds.has(f.id)) return false;
      loadedCompanyIds.add(f.id);
      return true;
    });
    if (newFeatures.length) {
      companiesLayer.addData({ type: "FeatureCollection", features: newFeatures });
    }
  } catch (err) {
    // rechargement silencieux, non bloquant
  }
}

function showCompanyTooltip(e, props) {
  const category = props.category ? `<div>${escapeHtml(props.category)}</div>` : "";
  const address = props.address ? `<div>${escapeHtml(props.address)}</div>` : "";
  const phone = props.phone ? `<div>📞 ${escapeHtml(props.phone)}</div>` : "";
  const rating = props.rating ? `<div>⭐ ${props.rating}</div>` : "";
  tooltipEl.innerHTML = `
    <div><strong>${escapeHtml(props.name || "Entreprise")}</strong></div>
    ${category}
    ${address}
    ${phone}
    ${rating}
  `;
  tooltipEl.classList.remove("hidden");
  moveTooltip(e);
}

const loadedIaIds = new Set();

function addIaFeatures(featureCollection) {
  const newFeatures = featureCollection.features.filter((f) => {
    if (loadedIaIds.has(f.id)) return false;
    loadedIaIds.add(f.id);
    return true;
  });
  if (newFeatures.length) {
    segmentationLayer.addData({ type: "FeatureCollection", features: newFeatures });
  }
}

async function deleteIaSegment(id, layer) {
  if (!confirm("Supprimer ce toit détecté par IA ?")) return;
  try {
    const resp = await fetch(`/api/ia_segments/${id}`, { method: "DELETE" });
    if (!resp.ok) {
      setStatus("Échec de la suppression", true);
      setTimeout(() => setStatus(null), 2500);
      return;
    }
    segmentationLayer.removeLayer(layer);
    loadedIaIds.delete(id);
  } catch (err) {
    setStatus("Erreur réseau pendant la suppression", true);
    setTimeout(() => setStatus(null), 2500);
  }
}

async function loadIaSegments() {
  if (map.getZoom() < MIN_ZOOM_FOR_BUILDINGS) return;

  const bounds = map.getBounds();
  const params = new URLSearchParams({
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast(),
  });

  try {
    const resp = await fetch(`/api/ia_segments?${params.toString()}`);
    if (!resp.ok) return;
    const data = await resp.json();
    addIaFeatures(data);
  } catch (err) {
    // rechargement silencieux, non bloquant
  }
}

const hintEl = document.getElementById("hint");
const tooltipEl = document.getElementById("tooltip");
const statusEl = document.getElementById("status");
const citySelectEl = document.getElementById("city-select");

function showTooltip(e, props) {
  const area = props.area_m2 ? `${props.area_m2.toLocaleString("fr-FR")} m²` : "Surface inconnue";
  const levels = props.levels ? `<div>Étages: ${props.levels}</div>` : "";
  tooltipEl.innerHTML = `
    <div><strong>${escapeHtml(props.name || "Bâtiment")}</strong></div>
    <div class="surface">${area}</div>
    ${levels}
  `;
  tooltipEl.classList.remove("hidden");
  moveTooltip(e);
}

function moveTooltip(e) {
  const { x, y } = e.originalEvent;
  tooltipEl.style.left = `${x + 14}px`;
  tooltipEl.style.top = `${y + 14}px`;
}

function hideTooltip() {
  tooltipEl.classList.add("hidden");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function setStatus(message, isError = false) {
  if (!message) {
    statusEl.classList.add("hidden");
    return;
  }
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
  statusEl.classList.remove("hidden");
}

let fetchController = null;
let debounceTimer = null;

function scheduleLoadBuildings() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    loadBuildings();
    loadIaSegments();
    loadCompanies();
  }, 400);
}

async function loadBuildings() {
  const zoom = map.getZoom();

  if (zoom < MIN_ZOOM_FOR_BUILDINGS) {
    buildingsLayer.clearLayers();
    hintEl.textContent = `Zoomez davantage (niveau ${zoom}/${MIN_ZOOM_FOR_BUILDINGS}) pour charger les bâtiments réels.`;
    setStatus(null);
    return;
  }

  hintEl.textContent = "Survolez un bâtiment pour voir sa surface.";

  const bounds = map.getBounds();
  const params = new URLSearchParams({
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast(),
  });

  if (fetchController) fetchController.abort();
  fetchController = new AbortController();

  setStatus("Chargement des bâtiments depuis OpenStreetMap...");
  try {
    const resp = await fetch(`/api/buildings?${params.toString()}`, {
      signal: fetchController.signal,
    });
    const data = await resp.json();

    if (!resp.ok) {
      setStatus(data.error || "Erreur lors du chargement", true);
      return;
    }

    buildingsLayer.clearLayers();
    buildingsLayer.addData(data);
    setStatus(
      data.features.length
        ? `${data.features.length} bâtiment(s) chargé(s)`
        : "Aucun bâtiment trouvé dans cette zone"
    );
    setTimeout(() => setStatus(null), 2500);
  } catch (err) {
    if (err.name !== "AbortError") {
      setStatus("Erreur réseau lors du chargement des bâtiments", true);
    }
  }
}

let citiesInfo = {};

async function loadCitiesInfo() {
  const resp = await fetch("/api/cities");
  citiesInfo = await resp.json();

  citySelectEl.innerHTML = '<option value="">— Choisir une ville —</option>';
  Object.entries(citiesInfo).forEach(([key, city]) => {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = city.label;
    citySelectEl.appendChild(opt);
  });
}

citySelectEl.addEventListener("change", () => {
  const city = citiesInfo[citySelectEl.value];
  if (city) {
    map.setView(city.center, city.zoom);
  }
});

map.on("moveend zoomend", scheduleLoadBuildings);

let segmentationInFlight = false;

async function segmentAtLatLng(latlng) {
  if (segmentationInFlight) return;
  if (map.getZoom() < MIN_ZOOM_FOR_BUILDINGS) {
    setStatus("Zoomez davantage pour utiliser la détection IA.", true);
    setTimeout(() => setStatus(null), 2500);
    return;
  }

  segmentationInFlight = true;
  const marker = L.circleMarker(latlng, { radius: 5, color: "#ff5252" }).addTo(map);
  setStatus("Analyse IA de l'imagerie satellite en cours (peut prendre quelques secondes)...");

  try {
    const params = new URLSearchParams({ lon: latlng.lng, lat: latlng.lat });
    const resp = await fetch(`/api/segment?${params.toString()}`);
    const data = await resp.json();

    if (!resp.ok) {
      setStatus(data.error || "Échec de la détection IA", true);
      setTimeout(() => setStatus(null), 3000);
      return;
    }

    addIaFeatures({ type: "FeatureCollection", features: [data] });
    setStatus(`Bâtiment détecté par IA : ${data.properties.area_m2.toLocaleString("fr-FR")} m²`);
    setTimeout(() => setStatus(null), 4000);
  } catch (err) {
    setStatus("Erreur réseau pendant la détection IA", true);
    setTimeout(() => setStatus(null), 3000);
  } finally {
    map.removeLayer(marker);
    segmentationInFlight = false;
  }
}

map.on("click", (e) => {
  if (zoneSelectMode || pointsMode) return;
  segmentAtLatLng(e.latlng);
});

const DEFAULT_HINT = hintEl.textContent;
const zoneBtn = document.getElementById("zone-select-btn");
let zoneSelectMode = false;
let zoneDrawing = false;
let zoneStartLatLng = null;
let zoneRectangle = null;

zoneBtn.addEventListener("click", () => {
  zoneSelectMode = !zoneSelectMode;
  zoneBtn.classList.toggle("active", zoneSelectMode);
  map.getContainer().style.cursor = zoneSelectMode ? "crosshair" : "";

  if (zoneSelectMode) {
    if (pointsMode) pointsBtn.click(); // modes mutuellement exclusifs
    map.dragging.disable();
    hintEl.textContent = "Cliquez-glissez sur la carte pour sélectionner une zone à analyser par IA.";
  } else {
    map.dragging.enable();
    hintEl.textContent = DEFAULT_HINT;
    if (zoneRectangle) {
      map.removeLayer(zoneRectangle);
      zoneRectangle = null;
    }
  }
});

map.on("mousedown", (e) => {
  if (!zoneSelectMode || segmentationInFlight) return;
  zoneDrawing = true;
  zoneStartLatLng = e.latlng;
  if (zoneRectangle) {
    map.removeLayer(zoneRectangle);
    zoneRectangle = null;
  }
});

map.on("mousemove", (e) => {
  if (!zoneDrawing) return;
  const bounds = L.latLngBounds(zoneStartLatLng, e.latlng);
  if (zoneRectangle) {
    zoneRectangle.setBounds(bounds);
  } else {
    zoneRectangle = L.rectangle(bounds, { color: "#2979ff", weight: 2, fillOpacity: 0.08 }).addTo(map);
  }
});

map.on("mouseup", (e) => {
  if (!zoneDrawing) return;
  zoneDrawing = false;
  const bounds = L.latLngBounds(zoneStartLatLng, e.latlng);
  zoneStartLatLng = null;

  // Ignore un drag trop petit (clic accidentel)
  if (bounds.getNorthEast().distanceTo(bounds.getSouthWest()) < 10) {
    if (zoneRectangle) {
      map.removeLayer(zoneRectangle);
      zoneRectangle = null;
    }
    return;
  }

  segmentZone(bounds);
});

async function segmentZone(bounds) {
  if (segmentationInFlight) return;
  if (map.getZoom() < MIN_ZOOM_FOR_BUILDINGS) {
    setStatus("Zoomez davantage pour utiliser la détection IA de zone.", true);
    setTimeout(() => setStatus(null), 2500);
    return;
  }

  segmentationInFlight = true;
  setStatus("Analyse IA de la zone en cours (peut prendre 1 à 2 minutes pour une grande zone)...");

  try {
    const params = new URLSearchParams({
      south: bounds.getSouth(),
      west: bounds.getWest(),
      north: bounds.getNorth(),
      east: bounds.getEast(),
    });
    const resp = await fetch(`/api/segment_zone?${params.toString()}`);
    const data = await resp.json();

    if (!resp.ok) {
      setStatus(data.error || "Échec de la détection IA de zone", true);
      setTimeout(() => setStatus(null), 3500);
      return;
    }

    addIaFeatures(data);
    setStatus(
      data.features.length
        ? `${data.features.length} toit(s) détecté(s) dans la zone`
        : "Aucun toit détecté dans cette zone"
    );
    setTimeout(() => setStatus(null), 4000);
  } catch (err) {
    setStatus("Erreur réseau pendant la détection IA de zone", true);
    setTimeout(() => setStatus(null), 3000);
  } finally {
    segmentationInFlight = false;
    if (zoneRectangle) {
      map.removeLayer(zoneRectangle);
      zoneRectangle = null;
    }
  }
}

// --- Mode "tracer un toit" : l'utilisateur place lui-même les sommets du
// contour au clic (aucun appel au modèle IA), puis valide pour calculer la
// surface et l'enregistrer. Fonctionnalité indépendante du clic simple et
// de la sélection de zone ci-dessus.

const pointsBtn = document.getElementById("points-select-btn");
const pointsControlsEl = document.getElementById("points-controls");
const pointsConfirmBtn = document.getElementById("points-confirm-btn");
const pointsCancelBtn = document.getElementById("points-cancel-btn");

let pointsMode = false;
let currentPoints = [];
let pointMarkers = [];

const tracePreviewLayer = L.polygon([], {
  color: "#2979ff",
  weight: 2,
  dashArray: "3 3",
  fillColor: "#82b1ff",
  fillOpacity: 0.3,
}).addTo(map);

pointsBtn.addEventListener("click", () => {
  pointsMode = !pointsMode;
  pointsBtn.classList.toggle("active", pointsMode);

  if (pointsMode) {
    if (zoneSelectMode) zoneBtn.click(); // modes mutuellement exclusifs
    hintEl.textContent = "Cliquez pour placer les sommets du contour du toit, puis Valider.";
  } else {
    clearPointsSession();
    hintEl.textContent = DEFAULT_HINT;
  }
});

function clearPointsSession() {
  currentPoints = [];
  pointMarkers.forEach((m) => map.removeLayer(m));
  pointMarkers = [];
  tracePreviewLayer.setLatLngs([]);
  pointsControlsEl.classList.add("hidden");
}

map.on("click", (e) => {
  if (!pointsMode) return;
  L.DomEvent.stopPropagation(e);
  addTracePoint(e.latlng);
});

function addTracePoint(latlng) {
  const marker = L.circleMarker(latlng, {
    radius: 5,
    color: "#2979ff",
    fillColor: "#82b1ff",
    fillOpacity: 0.9,
  }).addTo(map);
  pointMarkers.push(marker);
  currentPoints.push([latlng.lng, latlng.lat]);

  tracePreviewLayer.setLatLngs(currentPoints.map(([lon, lat]) => [lat, lon]));
  pointsControlsEl.classList.toggle("hidden", currentPoints.length === 0);
}

pointsConfirmBtn.addEventListener("click", async () => {
  if (currentPoints.length < 3 || segmentationInFlight) {
    if (currentPoints.length < 3) {
      setStatus("Il faut au moins 3 points pour former un contour.", true);
      setTimeout(() => setStatus(null), 2500);
    }
    return;
  }

  segmentationInFlight = true;
  setStatus("Enregistrement du toit...");

  try {
    const resp = await fetch("/api/roof_manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ points: currentPoints }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      setStatus(data.error || "Échec de l'enregistrement", true);
      setTimeout(() => setStatus(null), 3000);
      return;
    }

    addIaFeatures({ type: "FeatureCollection", features: [data] });
    setStatus(`Toit enregistré : ${data.properties.area_m2.toLocaleString("fr-FR")} m²`);
    setTimeout(() => setStatus(null), 3000);
  } catch (err) {
    setStatus("Erreur réseau pendant l'enregistrement", true);
    setTimeout(() => setStatus(null), 3000);
  } finally {
    segmentationInFlight = false;
    clearPointsSession();
  }
});

pointsCancelBtn.addEventListener("click", clearPointsSession);

loadCitiesInfo();
loadCompanies();
