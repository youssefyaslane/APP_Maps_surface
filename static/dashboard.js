const statsEl = document.getElementById("stats");
const bodyEl = document.getElementById("prospects-body");
const searchEl = document.getElementById("filter-search");
const cityEl = document.getElementById("filter-city");
const categoryEl = document.getElementById("filter-category");
const minKwcEl = document.getElementById("filter-min-kwc");
const exportEl = document.getElementById("export-csv");
const resultCountEl = document.getElementById("result-count");

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmt(n, digits = 0) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

const SOURCE_LABELS = {
  "osm": ["OpenStreetMap", "badge-osm"],
  "ia-segmentation": ["IA", "badge-ia"],
  "manual-trace": ["Tracé manuel", "badge-manual"],
  "ms-buildings": ["Microsoft", "badge-ms"],
};

function sourceBadge(source) {
  const [label, cls] = SOURCE_LABELS[source] || [source || "—", ""];
  return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
}

function currentFilters() {
  const params = new URLSearchParams();
  if (searchEl.value.trim()) params.set("search", searchEl.value.trim());
  if (cityEl.value.trim()) params.set("city", cityEl.value.trim());
  if (categoryEl.value.trim()) params.set("category", categoryEl.value.trim());
  if (minKwcEl.value) params.set("min_kwc", minKwcEl.value);
  return params;
}

function renderStats(summary) {
  const threshold = summary.big_prospect_threshold;
  const cards = [
    [fmt(summary.with_roof), "Prospects avec toit identifié", null],
    [`${fmt(summary.total_kwc, 1)} kWc`, "Potentiel total installable", null],
    [fmt(summary.total_panels), "Panneaux estimés au total", null],
    [`${fmt(summary.avg_roof_area_m2, 0)} m²`, "Surface moyenne par toit", null],
    [
      fmt(summary.big_prospects),
      `Cibles prioritaires (≥ ${threshold} kWc)`,
      threshold,
    ],
  ];
  statsEl.innerHTML = cards
    .map(
      ([value, label, filterKwc]) => `
        <div class="stat-card${filterKwc ? " clickable" : ""}"${
        filterKwc ? ` data-min-kwc="${filterKwc}" title="Cliquer pour filtrer"` : ""
      }>
          <div class="stat-value">${value}</div>
          <div class="stat-label">${label}</div>
        </div>`
    )
    .join("");

  statsEl.querySelectorAll(".stat-card.clickable").forEach((card) =>
    card.addEventListener("click", () => {
      minKwcEl.value = card.dataset.minKwc;
      load();
    })
  );
}

function renderRows(prospects) {
  if (!prospects.length) {
    bodyEl.innerHTML = `<tr><td colspan="10" class="empty">
      Aucun prospect ne correspond. Lancez <code>python compute_solar_potential.py</code>
      pour calculer le potentiel solaire des entreprises.
    </td></tr>`;
    return;
  }

  bodyEl.innerHTML = prospects
    .map((p, idx) => {
      const contact = [
        p.phone ? `<a href="tel:${escapeHtml(p.phone)}">📞 ${escapeHtml(p.phone)}</a>` : "",
        p.website ? `<a href="${escapeHtml(p.website)}" target="_blank" rel="noopener">🌐 Site</a>` : "",
      ].join("");

      return `
        <tr>
          <td class="rank">${idx + 1}</td>
          <td>
            <span class="company-name">${escapeHtml(p.name)}</span>
            ${p.address ? `<span class="company-address">${escapeHtml(p.address)}</span>` : ""}
          </td>
          <td>${escapeHtml(p.category || "—")}</td>
          <td>${escapeHtml(p.city || "—")}</td>
          <td class="num">${fmt(p.roof_area_m2, 0)} m²</td>
          <td class="num">${fmt(p.solar_panels)}</td>
          <td class="num"><strong>${fmt(p.solar_kwc, 1)} kWc</strong></td>
          <td>${sourceBadge(p.roof_source)}</td>
          <td class="contact">${contact || "—"}</td>
          <td><a class="map-link" href="/?lat=${p.lat}&lon=${p.lon}" title="Voir sur la carte">🗺️ Voir</a></td>
        </tr>`;
    })
    .join("");
}

async function load() {
  bodyEl.innerHTML = `<tr><td colspan="10" class="empty">Chargement...</td></tr>`;
  const params = currentFilters();
  exportEl.href = `/api/prospects.csv?${params.toString()}`;

  try {
    const resp = await fetch(`/api/prospects?${params.toString()}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Erreur");
    renderStats(data.summary);
    renderRows(data.prospects);

    const n = data.prospects.length;
    const filtered = [...params.keys()].length > 0;
    resultCountEl.textContent = filtered
      ? `${fmt(n)} prospect(s) affiché(s) sur ${fmt(data.summary.with_roof)}`
      : `${fmt(n)} prospect(s)`;
  } catch (err) {
    bodyEl.innerHTML = `<tr><td colspan="10" class="empty">Erreur de chargement : ${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("apply-filters").addEventListener("click", load);
document.getElementById("reset-filters").addEventListener("click", () => {
  searchEl.value = "";
  cityEl.value = "";
  categoryEl.value = "";
  minKwcEl.value = "";
  load();
});
[searchEl, cityEl, categoryEl, minKwcEl].forEach((el) =>
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") load();
  })
);

load();
