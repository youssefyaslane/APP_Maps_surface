const statsEl = document.getElementById("stats");
const bodyEl = document.getElementById("prospects-body");
const searchEl = document.getElementById("filter-search");
const cityEl = document.getElementById("filter-city");
const categoryEl = document.getElementById("filter-category");
const minKwcEl = document.getElementById("filter-min-kwc");
const viewEl = document.getElementById("filter-view");
const pvEl = document.getElementById("filter-pv");
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

// Détection automatique des panneaux déjà posés, sur l'image satellite. Ce
// n'est qu'une indication (la confiance apparaît au survol) : le bouton « Déjà
// équipée » reste le geste qui retire vraiment une entreprise de la liste.
function pvBadge(pv) {
  if (!pv) return `<span class="pv-badge pv-unknown" title="Toit pas encore analysé">—</span>`;
  if (pv.verdict === "pas d'image") {
    return `<span class="pv-badge pv-unknown" title="Pas d'image satellite assez précise pour ce toit">Pas d'image</span>`;
  }
  const conf = fmt(pv.confiance, 2);
  if (pv.verdict === "oui") {
    return `<span class="pv-badge pv-yes" title="${fmt(pv.nb)} panneau(x) détecté(s), confiance max ${conf}">Oui</span>`;
  }
  return `<span class="pv-badge pv-no" title="Aucun panneau confirmé sur ce toit">Non</span>`;
}

function currentFilters() {
  const params = new URLSearchParams();
  if (searchEl.value.trim()) params.set("search", searchEl.value.trim());
  if (cityEl.value.trim()) params.set("city", cityEl.value.trim());
  if (categoryEl.value.trim()) params.set("category", categoryEl.value.trim());
  if (minKwcEl.value) params.set("min_kwc", minKwcEl.value);
  if (viewEl.value === "1") params.set("equipped", "1");
  if (pvEl.value) params.set("pv", pvEl.value);
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

// `startRank` est le rang du premier prospect de la page. Sans lui, la
// numérotation repartait de 1 à chaque page : le 51e prospect par puissance
// s'affichait « 1 », au même rang que la plus grosse toiture de la base.
function renderRows(prospects, startRank) {
  const equippedView = viewEl.value === "1";
  if (!prospects.length) {
    bodyEl.innerHTML = equippedView
      ? `<tr><td colspan="12" class="empty">Aucune entreprise n'est marquée comme déjà équipée.</td></tr>`
      : `<tr><td colspan="12" class="empty">
      Aucun prospect ne correspond. Lancez <code>python -m scripts.compute_solar_potential</code>
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
        <tr data-id="${p.id}" data-name="${escapeHtml(p.name)}"${isShared ? ' class="is-shared"' : ""}>
          <td class="rank">${fmt(startRank + idx)}</td>
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
          <td>${pvBadge(p.pv)}</td>
          <td class="contact">${contact || "—"}</td>
          <td class="row-actions">
            <a class="map-link" href="/carte?lat=${p.lat}&lon=${p.lon}" title="Voir sur la carte">🗺️ Voir</a>
            <button type="button" class="btn-equipped" data-equipped="${equippedView ? "false" : "true"}"
                    title="${equippedView ? "Remettre dans la liste des prospects" : "Déjà équipée de panneaux : retirer de la liste"}">${equippedView ? "Rétablir" : "Déjà équipée"}</button>
          </td>
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
  bodyEl.innerHTML = `<tr><td colspan="12" class="empty">Chargement...</td></tr>`;
  const params = currentFilters();
  exportEl.href = `/api/prospects.csv?${params.toString()}`;

  params.set("limit", PAGE_SIZE);
  params.set("offset", (currentPage - 1) * PAGE_SIZE);

  try {
    const resp = await fetch(`/api/prospects?${params.toString()}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Erreur");

    const start = data.total_filtered === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
    renderStats(data.summary);
    renderRows(data.prospects, start);
    renderPagination(data.total_filtered);

    const end = start + data.prospects.length - 1;
    const equippedView = viewEl.value === "1";
    const label = equippedView ? "entreprise(s) déjà équipée(s)" : "prospect(s)";
    const hidden = !equippedView && data.summary.equipped
      ? ` · ${fmt(data.summary.equipped)} déjà équipée(s), masquée(s)`
      : "";
    resultCountEl.textContent = `${fmt(start)}–${fmt(end)} sur ${fmt(data.total_filtered)} ${label}${hidden}`;
  } catch (err) {
    bodyEl.innerHTML = `<tr><td colspan="12" class="empty">Erreur de chargement : ${escapeHtml(err.message)}</td></tr>`;
    paginationEl.innerHTML = "";
  }
}

// Villes et catégories proposées à partir de ce que contient réellement la
// base. Les deux champs étaient libres : il fallait deviner l'orthographe
// exacte (« Mohammédia », « Âïn-Harrouda ») pour obtenir autre chose qu'une
// liste vide. Le nombre entre parenthèses annonce combien de lignes le choix
// va produire. Les valeurs sont triées par fréquence : les secteurs utiles
// arrivent en tête d'une liste qui compte plusieurs centaines de catégories.
function fillSelect(selectEl, values) {
  const placeholder = selectEl.options[0];
  const selected = selectEl.value;
  selectEl.innerHTML = "";
  selectEl.appendChild(placeholder);
  values.forEach(({ value, count }) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = `${value} (${fmt(count)})`;
    selectEl.appendChild(opt);
  });
  // Le choix courant peut ne plus rien rendre une fois les autres filtres
  // appliqués : il doit rester dans la liste, sinon il disparaît du menu sans
  // que le tableau cesse d'être filtré dessus, et on ne peut plus l'annuler.
  if (selected && !values.some((v) => v.value === selected)) {
    const opt = document.createElement("option");
    opt.value = selected;
    opt.textContent = `${selected} (0)`;
    selectEl.appendChild(opt);
  }
  selectEl.value = selected;
}

async function loadFilterOptions() {
  try {
    const resp = await fetch(`/api/prospect_filters?${currentFilters()}`);
    if (!resp.ok) return;
    const data = await resp.json();
    fillSelect(cityEl, data.cities);
    fillSelect(categoryEl, data.categories);
  } catch (err) {
    // Les listes restent réduites à leur option « Toutes » : le tableau et les
    // autres filtres continuent de fonctionner.
  }
}

function applyFiltersAndReload() {
  currentPage = 1;
  load();
  // Les nombres affichés en face de chaque choix dépendent des autres filtres
  // actifs : sans ce rechargement, ils resteraient ceux du chargement initial
  // et annonceraient « Casablanca (1080) » là où le tableau n'affiche que
  // 86 lignes.
  loadFilterOptions();
}

document.getElementById("apply-filters").addEventListener("click", applyFiltersAndReload);
document.getElementById("reset-filters").addEventListener("click", () => {
  searchEl.value = "";
  cityEl.value = "";
  categoryEl.value = "";
  minKwcEl.value = "";
  viewEl.value = "";
  pvEl.value = "";
  applyFiltersAndReload();
});
[searchEl, cityEl, categoryEl, minKwcEl].forEach((el) =>
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") applyFiltersAndReload();
  })
);

// Choisir dans une liste est un geste complet : inutile de demander en plus de
// cliquer sur « Filtrer ».
[cityEl, categoryEl, viewEl, pvEl].forEach((el) => el.addEventListener("change", applyFiltersAndReload));

// « Déjà équipée » : l'entreprise a déjà des panneaux, elle sort de la liste
// des prospects et des totaux sans être supprimée. « Rétablir » l'y remet.
bodyEl.addEventListener("click", async (e) => {
  const btn = e.target.closest(".btn-equipped");
  if (!btn) return;
  const row = btn.closest("tr");
  const equipped = btn.dataset.equipped === "true";
  const question = equipped
    ? `Retirer « ${row.dataset.name} » de la liste des prospects ? Elle restera visible sur la carte, en gris, et pourra être rétablie.`
    : `Remettre « ${row.dataset.name} » dans la liste des prospects ?`;
  if (!confirm(question)) return;
  btn.disabled = true;
  try {
    const resp = await fetch(`/api/companies/${row.dataset.id}/equipped`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ equipped }),
    });
    if (!resp.ok) throw new Error();
    load();
    loadFilterOptions();
  } catch {
    btn.disabled = false;
    alert("L'enregistrement a échoué. Réessayez.");
  }
});

loadFilterOptions();
load();
