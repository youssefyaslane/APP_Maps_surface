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

// Pane dédié pour les marqueurs d'entreprises : au-dessus des polygones
// (bâtiments OSM, détections IA, tracés manuels) pour qu'ils restent
// toujours accessibles au survol/clic même en cas de superposition.
map.createPane("companiesPane");
map.getPane("companiesPane").style.zIndex = 650;

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

const msBuildingsLayer = L.geoJSON(null, {
  style: () => ({
    color: "#7b1fa2",
    weight: 1,
    fillColor: "#ce93d8",
    fillOpacity: 0.4,
  }),
  onEachFeature: (feature, layer) => {
    layer.on({
      mouseover: (e) => {
        e.target.setStyle({ fillOpacity: 0.75, weight: 2 });
        showTooltip(e, { ...feature.properties, name: "Bâtiment détecté par IA (Microsoft)" });
      },
      mousemove: (e) => moveTooltip(e),
      mouseout: (e) => {
        msBuildingsLayer.resetStyle(e.target);
        hideTooltip();
      },
    });
  },
});

function makeDeletableLayer(style, name) {
  return L.geoJSON(null, {
    style: () => style,
    onEachFeature: (feature, layer) => {
      layer.on({
        mouseover: (e) => showTooltip(e, { ...feature.properties, name: `${name} (clic pour supprimer)` }),
        mousemove: (e) => moveTooltip(e),
        mouseout: () => hideTooltip(),
        click: (e) => {
          L.DomEvent.stopPropagation(e);
          deleteIaSegment(feature.id, layer);
        },
      });
    },
  }).addTo(map);
}

const aiDetectedLayer = makeDeletableLayer(
  { color: "#ff5252", weight: 2, dashArray: "6 4", fillColor: "#ff8a80", fillOpacity: 0.35 },
  "Bâtiment détecté par IA"
);
const manualTraceLayer = makeDeletableLayer(
  { color: "#2979ff", weight: 2, dashArray: "6 4", fillColor: "#82b1ff", fillOpacity: 0.35 },
  "Toit tracé manuellement"
);

// Vert : toit identifié (prospect qualifié, présent au tableau de bord).
// Rouge : aucun toit trouvé sous ce point — à tracer manuellement si l'entreprise
// est intéressante, ou point GPS mal placé par rapport au bâtiment.
const COMPANY_WITH_ROOF_STYLE = { color: "#1b5e20", fillColor: "#4caf50" };
const COMPANY_NO_ROOF_STYLE = { color: "#b71c1c", fillColor: "#ef5350" };

function companyMarkerStyle(props) {
  return props.has_roof ? COMPANY_WITH_ROOF_STYLE : COMPANY_NO_ROOF_STYLE;
}

const companiesLayer = L.geoJSON(null, {
  pointToLayer: (feature, latlng) =>
    L.circleMarker(latlng, {
      pane: "companiesPane",
      radius: 6,
      weight: 2,
      fillOpacity: 0.9,
      ...companyMarkerStyle(feature.properties),
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
      click: (e) => {
        L.DomEvent.stopPropagation(e);
        hideTooltip();
        openCompanyPanel(feature.properties, layer.getLatLng());
      },
    });
  },
}).addTo(map);

msBuildingsLayer.addTo(map);

// --- Masque de test généré par un LLM ---------------------------------------
// Emprise exacte du cadre soumis au modèle (composite Esri 4x4 tuiles, zoom 19,
// 1024x1024 px, ~255 m de côté), pour superposer le masque au bon endroit et
// juger visuellement de son alignement avec l'imagerie réelle.
const TEST_ZONE_BOUNDS = L.latLngBounds(
  [33.56886118255556, -7.591552734375],
  [33.57114966444732, -7.58880615234375]
);

// Masque vectorisé en polygones, pour qu'il se survole et se mesure comme les
// couches OSM et Microsoft plutôt que d'être une image plaquée.
const testMaskLayer = L.geoJSON(null, {
  style: () => ({
    color: "#e53935",
    weight: 1,
    fillColor: "#ef5350",
    fillOpacity: 0.45,
  }),
  onEachFeature: (feature, layer) => {
    layer.on({
      mouseover: (e) => {
        e.target.setStyle({ fillOpacity: 0.75, weight: 2, color: "#ffb300" });
        showTooltip(e, { ...feature.properties, name: "Emprise détectée par LLM" });
      },
      mousemove: (e) => moveTooltip(e),
      mouseout: (e) => {
        testMaskLayer.resetStyle(e.target);
        hideTooltip();
      },
    });
  },
});

let testMaskLoaded = false;

async function loadTestMask() {
  if (testMaskLoaded) return;
  try {
    const resp = await fetch("/static/zone_test_mask.geojson");
    if (!resp.ok) return;
    testMaskLayer.addData(await resp.json());
    testMaskLoaded = true;
  } catch (err) {
    // couche de test, échec non bloquant
  }
}

map.on("overlayadd", (e) => {
  if (e.layer === testMaskLayer) loadTestMask();
});

L.control
  .layers(
    { "Plan": streetLayer, "Satellite": satelliteLayer },
    {
      "Bâtiments OpenStreetMap": buildingsLayer,
      "Bâtiments détectés par IA": aiDetectedLayer,
      "Toits tracés manuellement": manualTraceLayer,
      "Entreprises": companiesLayer,
      "Bâtiments IA (Microsoft)": msBuildingsLayer,
      "Emprises LLM (test)": testMaskLayer,
    }
  )
  .addTo(map);

document.getElementById("test-zone-btn").addEventListener("click", async () => {
  await loadTestMask();
  if (!map.hasLayer(testMaskLayer)) testMaskLayer.addTo(map);
  map.fitBounds(TEST_ZONE_BOUNDS);
  setStatus("Zone de test — survolez une emprise LLM pour sa surface et son potentiel.");
  setTimeout(() => setStatus(null), 4000);
});

const loadedMsBuildingIds = new Set();

async function loadMsBuildings() {
  if (map.getZoom() < MIN_ZOOM_FOR_BUILDINGS) return;

  const bounds = map.getBounds();
  const params = new URLSearchParams({
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast(),
  });

  try {
    const resp = await fetch(`/api/ms_buildings?${params.toString()}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const newFeatures = data.features.filter((f) => {
      if (loadedMsBuildingIds.has(f.id)) return false;
      loadedMsBuildingIds.add(f.id);
      return true;
    });
    if (newFeatures.length) {
      msBuildingsLayer.addData({ type: "FeatureCollection", features: newFeatures });
    }
  } catch (err) {
    // rechargement silencieux, non bloquant
  }
}

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

// Remet à jour la couleur des marqueurs déjà affichés après l'ajout ou la
// suppression d'un toit (vert = toit identifié, rouge = aucun).
async function refreshCompanyRoofStatus() {
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
    const byId = new Map(data.features.map((f) => [f.id, f.properties]));

    companiesLayer.eachLayer((layer) => {
      const props = byId.get(layer.feature.id);
      if (!props) return;
      Object.assign(layer.feature.properties, props);
      layer.setStyle(companyMarkerStyle(props));
    });
  } catch (err) {
    // non bloquant
  }
}

function showCompanyTooltip(e, props) {
  const category = props.category ? `<div>${escapeHtml(props.category)}</div>` : "";
  const address = props.address ? `<div>${escapeHtml(props.address)}</div>` : "";
  const phone = props.phone ? `<div>📞 ${escapeHtml(props.phone)}</div>` : "";
  const rating = props.rating ? `<div>⭐ ${props.rating}</div>` : "";
  const roof = props.has_roof
    ? `<div class="solar">🏠 ${props.roof_area_m2.toLocaleString("fr-FR")} m²${
        props.solar_kwc ? ` — ☀️ ${props.solar_kwc.toLocaleString("fr-FR")} kWc` : ""
      }</div>`
    : `<div class="no-roof">⚠️ Aucun toit identifié</div>`;
  tooltipEl.innerHTML = `
    <div><strong>${escapeHtml(props.name || "Entreprise")}</strong></div>
    ${category}
    ${address}
    ${phone}
    ${rating}
    ${roof}
  `;
  tooltipEl.classList.remove("hidden");
  moveTooltip(e);
}

const companyPanelEl = document.getElementById("company-panel");
const companyPanelContentEl = document.getElementById("company-panel-content");
const companyPanelCloseEl = document.getElementById("company-panel-close");

function field(icon, label, value, isLink = false) {
  if (!value) return "";
  const content = isLink
    ? `<a href="${escapeHtml(value)}" target="_blank" rel="noopener">${escapeHtml(value)}</a>`
    : escapeHtml(String(value));
  return `
    <div class="field">
      <span class="field-icon">${icon}</span>
      <span class="field-body"><span class="field-label">${label}</span>${content}</span>
    </div>
  `;
}

// Couches de contours de bâtiments à isoler quand on affiche le toit d'une
// entreprise sélectionnée (masquées le temps du panneau, puis restaurées).
const ROOF_LAYERS = [buildingsLayer, aiDetectedLayer, manualTraceLayer, msBuildingsLayer];
let hiddenRoofLayers = [];
let companyRoofHighlight = null;

function openCompanyPanel(props, latlng) {
  const categoryBadge = props.category
    ? `<span class="field-category">${escapeHtml(props.category)}</span>`
    : "";
  companyPanelContentEl.innerHTML = `
    <h2>${escapeHtml(props.name || "Entreprise")}</h2>
    ${categoryBadge}
    ${field("📍", "Adresse", props.address)}
    ${field("🏙️", "Ville", props.city)}
    ${field("📞", "Téléphone", props.phone)}
    ${field("✉️", "Email", props.email)}
    ${field("🌐", "Site web", props.website, true)}
    ${field("⭐", "Note", props.rating)}
    <div class="field" id="company-roof-field">
      <span class="field-icon">🏠</span>
      <span class="field-body"><span class="field-label">Toit</span>Recherche...</span>
    </div>
  `;
  companyPanelEl.classList.remove("hidden");
  if (companyRoofHighlight) {
    map.removeLayer(companyRoofHighlight);
    companyRoofHighlight = null;
  }
  isolateRoofLayers();
  loadCompanyRoof(latlng);
}

function isolateRoofLayers() {
  if (hiddenRoofLayers.length) return; // déjà isolé (changement d'entreprise sans fermer le panneau)
  hiddenRoofLayers = ROOF_LAYERS.filter((layer) => map.hasLayer(layer));
  hiddenRoofLayers.forEach((layer) => map.removeLayer(layer));
}

function restoreRoofLayers() {
  hiddenRoofLayers.forEach((layer) => map.addLayer(layer));
  hiddenRoofLayers = [];
  if (companyRoofHighlight) {
    map.removeLayer(companyRoofHighlight);
    companyRoofHighlight = null;
  }
}

async function loadCompanyRoof(latlng) {
  const roofFieldEl = document.getElementById("company-roof-field");
  try {
    const params = new URLSearchParams({ lon: latlng.lng, lat: latlng.lat });
    const resp = await fetch(`/api/company_roof?${params.toString()}`);
    const data = await resp.json();
    if (!roofFieldEl) return; // panneau fermé/changé entre-temps

    if (!resp.ok || !data.area_m2) {
      roofFieldEl.remove();
      return;
    }

    const solar = estimateSolarPanels(data.area_m2);
    const solarText = solar
      ? ` — ☀️ ~${solar.nPanels} panneau(x) (${solar.capacityKWc.toLocaleString("fr-FR")} kWc)`
      : "";
    roofFieldEl.querySelector(".field-body").innerHTML =
      `<span class="field-label">Toit</span>${data.area_m2.toLocaleString("fr-FR")} m²${solarText}`;

    if (data.polygon && data.polygon.length >= 3) {
      const latlngs = data.polygon.map(([lon, lat]) => [lat, lon]);
      companyRoofHighlight = L.polygon(latlngs, {
        color: "#ffd54f",
        weight: 3,
        fillColor: "#ffd54f",
        fillOpacity: 0.4,
      }).addTo(map);
    }
  } catch (err) {
    if (roofFieldEl) roofFieldEl.remove();
  }
}

function closeCompanyPanel() {
  companyPanelEl.classList.add("hidden");
  restoreRoofLayers();
}

companyPanelCloseEl.addEventListener("click", closeCompanyPanel);

const loadedIaIds = new Set();

function addIaFeatures(featureCollection) {
  const newFeatures = featureCollection.features.filter((f) => {
    if (loadedIaIds.has(f.id)) return false;
    loadedIaIds.add(f.id);
    return true;
  });
  const manualFeatures = newFeatures.filter((f) => f.properties.source === "manual-trace");
  const aiFeatures = newFeatures.filter((f) => f.properties.source !== "manual-trace");
  if (aiFeatures.length) {
    aiDetectedLayer.addData({ type: "FeatureCollection", features: aiFeatures });
  }
  if (manualFeatures.length) {
    manualTraceLayer.addData({ type: "FeatureCollection", features: manualFeatures });
  }
  if (newFeatures.length) {
    // Un nouveau toit peut qualifier une entreprise située dessous.
    refreshCompanyRoofStatus();
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
    layer.remove();
    loadedIaIds.delete(id);
    refreshCompanyRoofStatus();
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

const SOLAR_PANEL_AREA_M2 = 1.7; // 1.0m x 1.7m
const SOLAR_PANEL_POWER_W = 400;
const SOLAR_USABLE_ROOF_FRACTION = 0.7;

function estimateSolarPanels(area_m2) {
  if (!area_m2 || area_m2 <= 0) return null;
  const nPanels = Math.floor((area_m2 * SOLAR_USABLE_ROOF_FRACTION) / SOLAR_PANEL_AREA_M2);
  const capacityKWc = (nPanels * SOLAR_PANEL_POWER_W) / 1000;
  return { nPanels, capacityKWc };
}

function showTooltip(e, props) {
  const area = props.area_m2 ? `${props.area_m2.toLocaleString("fr-FR")} m²` : "Surface inconnue";
  const levels = props.levels ? `<div>Étages: ${props.levels}</div>` : "";
  const solar = estimateSolarPanels(props.area_m2);
  const solarLine = solar
    ? `<div class="solar">☀️ ~${solar.nPanels} panneau(x) (${solar.capacityKWc.toLocaleString("fr-FR")} kWc)</div>`
    : "";
  tooltipEl.innerHTML = `
    <div><strong>${escapeHtml(props.name || "Bâtiment")}</strong></div>
    <div class="surface">${area}</div>
    ${levels}
    ${solarLine}
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
    loadMsBuildings();
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

const SEARCH_ZOOM = 17;
const COORDS_RE = /^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/;

const searchFormEl = document.getElementById("search-form");
const searchInputEl = document.getElementById("search-input");

searchFormEl.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = searchInputEl.value.trim();
  if (!query) return;

  const coordsMatch = query.match(COORDS_RE);
  if (coordsMatch) {
    const lat = parseFloat(coordsMatch[1]);
    const lon = parseFloat(coordsMatch[2]);
    map.setView([lat, lon], SEARCH_ZOOM);
    setStatus(`Déplacé vers ${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    setTimeout(() => setStatus(null), 2500);
    return;
  }

  setStatus("Recherche du lieu...");
  try {
    const resp = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
    const data = await resp.json();

    if (!resp.ok) {
      setStatus(data.error || "Lieu introuvable", true);
      setTimeout(() => setStatus(null), 3000);
      return;
    }

    map.setView([data.lat, data.lon], SEARCH_ZOOM);
    setStatus(`Trouvé : ${data.display_name}`);
    setTimeout(() => setStatus(null), 3000);
  } catch (err) {
    setStatus("Erreur réseau pendant la recherche", true);
    setTimeout(() => setStatus(null), 2500);
  }
});

map.on("moveend zoomend", scheduleLoadBuildings);

let segmentationInFlight = false;

// --- Segmentation interactive ---------------------------------------------
// Le premier clic encode l'imagerie côté serveur (quelques secondes) ; les
// clics suivants corrigent le contour en réutilisant cet encodage (~0,2s).
// Clic gauche = « ça fait partie du toit », clic droit = « ça, non ».

const segControlsEl = document.getElementById("seg-controls");
const segAreaEl = document.getElementById("seg-area");
const segConfirmBtn = document.getElementById("seg-confirm-btn");
const segUndoBtn = document.getElementById("seg-undo-btn");
const segCancelBtn = document.getElementById("seg-cancel-btn");

const SEG_HINT = "Clic pour étendre le toit · clic droit pour retirer une zone · Enregistrer quand c'est bon.";

let segSession = null; // { id, polygon, area_m2 }
let segMarkers = [];

const segPreviewLayer = L.polygon([], {
  color: "#ff9100",
  weight: 3,
  dashArray: "6 4",
  fillColor: "#ffb74d",
  fillOpacity: 0.35,
}).addTo(map);

function segRender(data) {
  segSession.polygon = data.polygon;
  segSession.area_m2 = data.area_m2;
  segPreviewLayer.setLatLngs(data.polygon.map(([lon, lat]) => [lat, lon]));
  segAreaEl.textContent = `${data.area_m2.toLocaleString("fr-FR")} m²`;
  segControlsEl.classList.remove("hidden");
}

function segAddMarker(latlng, positive) {
  const marker = L.circleMarker(latlng, {
    radius: 5,
    weight: 2,
    color: positive ? "#1b5e20" : "#b71c1c",
    fillColor: positive ? "#4caf50" : "#ef5350",
    fillOpacity: 0.95,
  }).addTo(map);
  segMarkers.push(marker);
}

function segClear() {
  segSession = null;
  segMarkers.forEach((m) => map.removeLayer(m));
  segMarkers = [];
  segPreviewLayer.setLatLngs([]);
  segControlsEl.classList.add("hidden");
  hintEl.textContent = DEFAULT_HINT;
}

async function segStart(latlng) {
  if (map.getZoom() < MIN_ZOOM_FOR_BUILDINGS) {
    setStatus("Zoomez davantage pour utiliser la détection IA.", true);
    setTimeout(() => setStatus(null), 2500);
    return;
  }

  segmentationInFlight = true;
  setStatus("Analyse IA de l'imagerie satellite (quelques secondes)...");
  segAddMarker(latlng, true);

  try {
    const resp = await fetch("/api/segment/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lon: latlng.lng, lat: latlng.lat }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      setStatus(data.error || "Échec de la détection IA", true);
      setTimeout(() => setStatus(null), 3000);
      segClear();
      return;
    }

    segSession = { id: data.session_id };
    segRender(data);
    hintEl.textContent = SEG_HINT;
    setStatus("Corrigez le contour si besoin, puis Enregistrer.");
    setTimeout(() => setStatus(null), 3500);
  } catch (err) {
    setStatus("Erreur réseau pendant la détection IA", true);
    setTimeout(() => setStatus(null), 3000);
    segClear();
  } finally {
    segmentationInFlight = false;
  }
}

async function segRefine(latlng, label) {
  if (!segSession || segmentationInFlight) return;

  segmentationInFlight = true;
  segAddMarker(latlng, label === 1);

  try {
    const resp = await fetch("/api/segment/refine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: segSession.id, lon: latlng.lng, lat: latlng.lat, label }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      setStatus(data.error || "Échec de la correction", true);
      setTimeout(() => setStatus(null), 3000);
      return;
    }
    segRender(data);
  } catch (err) {
    setStatus("Erreur réseau pendant la correction", true);
    setTimeout(() => setStatus(null), 2500);
  } finally {
    segmentationInFlight = false;
  }
}

segUndoBtn.addEventListener("click", async () => {
  if (!segSession || segmentationInFlight) return;
  segmentationInFlight = true;
  try {
    const resp = await fetch("/api/segment/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: segSession.id }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setStatus(data.error || "Plus rien à annuler", true);
      setTimeout(() => setStatus(null), 2000);
      return;
    }
    const last = segMarkers.pop();
    if (last) map.removeLayer(last);
    segRender(data);
  } catch (err) {
    setStatus("Erreur réseau", true);
    setTimeout(() => setStatus(null), 2000);
  } finally {
    segmentationInFlight = false;
  }
});

segConfirmBtn.addEventListener("click", async () => {
  if (!segSession || segmentationInFlight) return;
  segmentationInFlight = true;
  setStatus("Enregistrement du toit...");
  try {
    const resp = await fetch("/api/segment/commit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: segSession.id, polygon: segSession.polygon }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setStatus(data.error || "Échec de l'enregistrement", true);
      setTimeout(() => setStatus(null), 3000);
      return;
    }
    addIaFeatures({ type: "FeatureCollection", features: [data] });
    setStatus(`Toit enregistré : ${data.properties.area_m2.toLocaleString("fr-FR")} m²`);
    setTimeout(() => setStatus(null), 3500);
    segClear();
  } catch (err) {
    setStatus("Erreur réseau pendant l'enregistrement", true);
    setTimeout(() => setStatus(null), 3000);
  } finally {
    segmentationInFlight = false;
  }
});

segCancelBtn.addEventListener("click", () => {
  if (!segSession) return;
  const sessionId = segSession.id;
  segClear();
  setStatus(null);
  fetch("/api/segment/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  }).catch(() => {}); // libération best-effort, la session expire de toute façon
});

map.on("click", (e) => {
  if (zoneSelectMode || pointsMode) return;
  if (segSession) {
    segRefine(e.latlng, 1);
  } else if (!segmentationInFlight) {
    segStart(e.latlng);
  }
});

map.on("contextmenu", (e) => {
  if (zoneSelectMode || pointsMode || !segSession) return;
  L.DomEvent.preventDefault(e);
  segRefine(e.latlng, 0);
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

// Centrage direct via ?lat=&lon= (lien « Voir sur la carte » du tableau de bord).
function applyUrlLocation() {
  const params = new URLSearchParams(window.location.search);
  const lat = parseFloat(params.get("lat"));
  const lon = parseFloat(params.get("lon"));
  if (Number.isFinite(lat) && Number.isFinite(lon)) {
    map.setView([lat, lon], 19);
  }
}

loadCitiesInfo();
loadCompanies();
applyUrlLocation();
