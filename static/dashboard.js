const statsEl = document.getElementById("stats");
const bodyEl = document.getElementById("prospects-body");
const searchEl = document.getElementById("filter-search");
const cityEl = document.getElementById("filter-city");
const categoryEl = document.getElementById("filter-category");
const minKwcEl = document.getElementById("filter-min-kwc");
const exportEl = document.getElementById("export-csv");
const resultCountEl = document.getElementById("result-count");
const paginationEl = document.getElementById("pagination");

const PAGE_SIZE = 50;
let currentPage = 1;

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
    [
      fmt(summary.distinct_roofs),
      summary.shared_companies
        ? `Toits distincts (${fmt(summary.shared_companies)} en partage)`
        : "Toits distincts",
      null,
    ],
    [`${fmt(summary.total_kwc, 1)} kWc`, "Potentiel total installable", null],
    [fmt(summary.total_panels), "Panneaux estimés au total", null],
    [`${fmt(summary.avg_roof_area_m2, 0)} m²`, "Surface moyenne par toit", null],
    [
      fmt(summary.big_prospects),
      // Compte des toitures, pas des entreprises : un toit partagé ne
      // représente qu'une installation à vendre. Le clic filtre la liste, qui
      // peut donc afficher plus de lignes que ce nombre.
      `Toitures prioritaires (≥ ${threshold} kWc)`,
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
      currentPage = 1;
      load();
    })
  );
}

function renderRows(prospects) {
  if (!prospects.length) {
    bodyEl.innerHTML = `<tr><td colspan="11" class="empty">
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

      // Un toit ne s'équipe qu'une fois : si plusieurs entreprises y sont
      // rattachées, la surface affichée n'est pas disponible pour ce prospect seul.
      const isShared = p.shared_count > 1;
      const roofStatus = isShared
        ? `<span class="roof-shared" title="Ce toit est aussi rattaché à ${
            p.shared_count - 1
          } autre(s) entreprise(s) — la surface n'est pas disponible pour ce seul prospect">⚠ Partagé × ${
            p.shared_count
          }</span>`
        : `<span class="roof-exclusive" title="Aucune autre entreprise connue sur ce toit">✓ Exclusif</span>`;

      return `
        <tr${isShared ? ' class="is-shared"' : ""}>
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
          <td class="roof-status">${roofStatus}</td>
          <td class="contact">${contact || "—"}</td>
          <td><a class="map-link" href="/carte?lat=${p.lat}&lon=${p.lon}" title="Voir sur la carte">🗺️ Voir</a></td>
        </tr>`;
    })
    .join("");
}

function renderPagination(totalFiltered) {
  const totalPages = Math.max(1, Math.ceil(totalFiltered / PAGE_SIZE));
  if (totalPages <= 1) {
    paginationEl.innerHTML = "";
    return;
  }

  const goTo = (page) => {
    currentPage = Math.min(Math.max(1, page), totalPages);
    load();
  };

  // Fenêtre de numéros de page autour de la page courante (max 7 boutons).
  const windowStart = Math.max(1, currentPage - 3);
  const windowEnd = Math.min(totalPages, windowStart + 6);
  let pageButtons = "";
  for (let p = windowStart; p <= windowEnd; p++) {
    pageButtons += `<button class="page-btn${p === currentPage ? " active" : ""}" data-page="${p}">${p}</button>`;
  }

  paginationEl.innerHTML = `
    <button class="page-nav" data-page="${currentPage - 1}" ${currentPage === 1 ? "disabled" : ""}>‹ Précédent</button>
    ${windowStart > 1 ? `<button class="page-btn" data-page="1">1</button><span class="page-ellipsis">…</span>` : ""}
    ${pageButtons}
    ${windowEnd < totalPages ? `<span class="page-ellipsis">…</span><button class="page-btn" data-page="${totalPages}">${totalPages}</button>` : ""}
    <button class="page-nav" data-page="${currentPage + 1}" ${currentPage === totalPages ? "disabled" : ""}>Suivant ›</button>
  `;

  paginationEl.querySelectorAll("[data-page]").forEach((btn) =>
    btn.addEventListener("click", () => goTo(Number(btn.dataset.page)))
  );
}

async function load() {
  bodyEl.innerHTML = `<tr><td colspan="11" class="empty">Chargement...</td></tr>`;
  const params = currentFilters();
  exportEl.href = `/api/prospects.csv?${params.toString()}`;

  params.set("limit", PAGE_SIZE);
  params.set("offset", (currentPage - 1) * PAGE_SIZE);

  try {
    const resp = await fetch(`/api/prospects?${params.toString()}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Erreur");
    renderStats(data.summary);
    renderRows(data.prospects);
    renderPagination(data.total_filtered);

    const start = data.total_filtered === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
    const end = start + data.prospects.length - 1;
    resultCountEl.textContent = `${fmt(start)}–${fmt(end)} sur ${fmt(data.total_filtered)} prospect(s)`;
  } catch (err) {
    bodyEl.innerHTML = `<tr><td colspan="11" class="empty">Erreur de chargement : ${escapeHtml(err.message)}</td></tr>`;
    paginationEl.innerHTML = "";
  }
}

function applyFiltersAndReload() {
  currentPage = 1;
  load();
}

document.getElementById("apply-filters").addEventListener("click", applyFiltersAndReload);
document.getElementById("reset-filters").addEventListener("click", () => {
  searchEl.value = "";
  cityEl.value = "";
  categoryEl.value = "";
  minKwcEl.value = "";
  applyFiltersAndReload();
});
[searchEl, cityEl, categoryEl, minKwcEl].forEach((el) =>
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") applyFiltersAndReload();
  })
);

load();
