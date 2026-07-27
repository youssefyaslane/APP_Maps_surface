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
  maxBounds: MOROCCO_BOUNDS,
  maxBoundsViscosity: 0.8,
});

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);

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
  debounceTimer = setTimeout(loadBuildings, 400);
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

loadCitiesInfo();
