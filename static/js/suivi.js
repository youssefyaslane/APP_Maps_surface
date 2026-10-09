// Page « Mes opportunités » (/suivi) : les prospects pris par les commerciaux,
// en pipeline (une colonne par étape) ou en liste, et leur fiche (crm.js).
(() => {
  const kanban = document.getElementById("vue-pipeline");
  const vueListe = document.getElementById("vue-liste");
  const corps = document.getElementById("suivi-body");
  const kpis = document.getElementById("suivi-kpis");
  const compte = document.getElementById("suivi-count");
  // Absent pour un commercial, qui ne voit que ses opportunités.
  const commercialEl = document.getElementById("suivi-commercial");
  const perimetre = () => (commercialEl ? commercialEl.value : "moi");
  const statutEl = document.getElementById("suivi-statut");
  const rechercheEl = document.getElementById("suivi-search");
  const relancesEl = document.getElementById("suivi-relances");
  let ref = null;
  let vue = "pipeline";
  try { vue = localStorage.getItem("suivi-vue") || "pipeline"; } catch { /* stockage indisponible */ }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const nb = (n, d = 0) => (n === null || n === undefined ? "—"
    : Number(n).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const jour = (iso) => new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  const dh = (n) => (n >= 1e6 ? `${nb(n / 1e6, 1)} M DH` : `${nb(n)} DH`);
  const kwc = (n) => (n >= 1000 ? `${nb(n / 1000, 1)} MWc` : `${nb(n)} kWc`);
  const enCours = (o) => o.statut !== "signe" && o.statut !== "perdu";
  const economies = (o) => o.calculs.economies_reelles_dh || o.calculs.economies_max_dh || 0;

  function filtres() {
    const p = new URLSearchParams({ commercial: perimetre() });
    // Le pipeline montre toutes les étapes : le filtre d'étape ne vaut que pour la liste.
    if (vue === "liste" && statutEl.value) p.set("statut", statutEl.value);
    if (rechercheEl.value.trim()) p.set("search", rechercheEl.value.trim());
    if (relancesEl.getAttribute("aria-pressed") === "true") p.set("relances", "1");
    return p;
  }

  function afficherKpis(liste, dues) {
    const actives = liste.filter(enCours);
    const signees = liste.filter((o) => o.statut === "signe");
    const cartes = [
      ["dossier", nb(actives.length), "opportunités en cours", ""],
      ["soleil", kwc(actives.reduce((t, o) => t + (o.solar_kwc || 0), 0)), "de puissance en jeu", ""],
      ["billet", dh(actives.reduce((t, o) => t + economies(o), 0)), "d'économies en jeu par an", ""],
      ["valide", nb(signees.length), signees.length > 1 ? "opportunités signées" : "opportunité signée", "kpi-signe"],
      ["horloge", nb(dues), "relances dues", dues ? "kpi-alerte" : ""],
    ];
    kpis.innerHTML = cartes.map(([ico, valeur, libelle, classe], i) => `
      <div class="suivi-kpi ${classe}"${i === 4 ? ' role="button" tabindex="0" data-relances="1" title="Afficher les relances dues"' : ""}>
        <span class="suivi-kpi-icone">${icone(ico)}</span>
        <div><b>${valeur}</b><span>${esc(libelle)}</span></div>
      </div>`).join("");
  }

  // Prochaine action d'une opportunité : en rouge si due, signalée si absente.
  function prochaineAction(o) {
    if (!enCours(o)) return "";
    if (!o.relance) return `<span class="op-action op-sans-action">${icone("alerte")} Aucune action prévue</span>`;
    return `<span class="op-action${o.relance.due ? " due" : ""}" title="${esc(o.relance.objet || "")}">
      ${icone("horloge")} ${jour(o.relance.le)}${o.relance.objet ? ` · ${esc(o.relance.objet)}` : ""}</span>`;
  }

  function carte(o) {
    const raison = o.raison_perte ? `<span class="op-raison">${esc(ref.raisons_perte[o.raison_perte] || o.raison_perte)}</span>` : "";
    return `
      <button type="button" class="op-carte etape-${esc(o.statut)}" data-id="${o.id}">
        <span class="op-nom">${esc(o.name)}</span>
        <span class="op-lieu">${esc([o.city, o.category].filter(Boolean).join(" · ") || "—")}</span>
        ${o.etape_depuis ? `<span class="op-depuis" title="Dans cette étape depuis le ${esc(jour(o.etape_depuis))}">${icone("horloge")} Dans l'étape depuis ${esc(CRM.duree(o.etape_depuis))}</span>` : ""}
        <span class="op-chiffres">
          <span>${icone("soleil")} ${o.solar_kwc ? kwc(o.solar_kwc) : "Sans toit"}</span>
          ${economies(o) ? `<span>${icone("billet")} ${dh(economies(o))}</span>` : ""}
        </span>
        ${raison}${prochaineAction(o)}
        <span class="op-pied">
          <span>${icone("utilisateur")} ${o.decideur.nom ? esc(o.decideur.nom) : "Décideur à trouver"}</span>
          ${perimetre() !== "moi" ? `<span class="op-commercial">${esc(o.commercial.nom)}</span>` : ""}
        </span>
      </button>`;
  }

  function afficherPipeline(liste) {
    kanban.innerHTML = CRM.entrees("statuts").map(([etape, libelle]) => {
      const ops = liste.filter((o) => o.statut === etape);
      const total = ops.reduce((t, o) => t + (o.solar_kwc || 0), 0);
      return `
        <div class="kanban-colonne etape-${etape}">
          <header>
            <span class="kanban-titre">${esc(libelle)}</span>
            <span class="kanban-nombre">${nb(ops.length)}</span>
            <span class="kanban-total">${ops.length ? kwc(total) : ""}</span>
          </header>
          <div class="kanban-cartes">${ops.map(carte).join("") || `<p class="kanban-vide">Aucune</p>`}</div>
        </div>`;
    }).join("");
  }

  function ligne(o) {
    const etape = `<span class="crm-etape etape-${esc(o.statut)}">${esc(ref.statuts[o.statut] || o.statut)}</span>
      ${o.raison_perte ? `<span class="suivi-sous">${esc(ref.raisons_perte[o.raison_perte] || o.raison_perte)}</span>` : ""}`;
    const d = o.decideur;
    const decideur = d.nom
      ? `${esc(d.nom)}${d.fonction ? `<span class="suivi-sous">${esc(d.fonction)}</span>` : ""}
         ${d.telephone ? `<a class="suivi-sous" href="tel:${esc(d.telephone)}">${icone("telephone")} ${esc(d.telephone)}</a>` : ""}`
      : `<span class="suivi-sous">À trouver</span>`;
    const c = o.calculs;
    const potentiel = o.solar_kwc
      ? `<b>${nb(o.solar_kwc)} kWc</b>${c.production_mwh ? `<span class="suivi-sous">${nb(c.production_mwh)} MWh/an</span>` : ""}`
      : `<span class="suivi-sous">Sans toit</span>`;
    const eco = c.economies_reelles_dh
      ? `<b>${dh(c.economies_reelles_dh)}</b><span class="suivi-sous">réelles / an</span>`
      : c.economies_max_dh ? `${dh(c.economies_max_dh)}<span class="suivi-sous">max / an</span>` : "—";
    return `
      <tr data-id="${o.id}">
        <td><button type="button" class="company-name btn-fiche">${esc(o.name)}</button>
          <span class="company-address">${esc([o.category, o.city].filter(Boolean).join(" · "))}</span></td>
        <td>${etape}</td>
        <td>${prochaineAction(o) || `<span class="suivi-sous">—</span>`}</td>
        <td>${decideur}</td>
        <td class="num">${potentiel}</td>
        <td class="num">${eco}</td>
        <td class="col-commercial"${perimetre() === "moi" ? " hidden" : ""}>${icone("utilisateur")} ${esc(o.commercial.nom)}</td>
        <td><span class="suivi-sous">${o.maj_le ? jour(o.maj_le) : "—"}</span></td>
        <td><button type="button" class="btn-fiche btn-fiche-lien">${icone("dossier")} Ouvrir</button></td>
      </tr>`;
  }

  const vide = () => (perimetre() === "moi"
    ? "Vous n'avez encore pris aucun prospect. Dans « Prospects solaires » ou sur la carte, cliquez sur « Prendre »."
    : "Aucun commercial n'a encore pris de prospect.");

  function afficherListe(liste, total) {
    document.querySelector("th.col-commercial").hidden = perimetre() === "moi";
    corps.innerHTML = liste.length ? liste.map(ligne).join("")
      : `<tr><td colspan="9" class="empty">${total ? "Aucune opportunité ne correspond à ces filtres." : vide()}</td></tr>`;
    compte.textContent = `${nb(liste.length)} opportunité(s) affichée(s) sur ${nb(total)}`;
  }

  function choisirVue(v) {
    vue = v;
    try { localStorage.setItem("suivi-vue", v); } catch { /* stockage indisponible */ }
    document.querySelectorAll(".vue-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.vue === v)));
    kanban.hidden = v !== "pipeline";
    vueListe.hidden = v !== "liste";
    statutEl.hidden = v !== "liste";
    charger();
  }

  async function charger() {
    try {
      const resp = await fetch(`/api/crm/opportunites?${filtres()}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Erreur");
      relancesEl.classList.toggle("a-faire", data.relances_dues > 0);
      afficherKpis(data.opportunites, data.relances_dues);
      if (vue === "pipeline") {
        if (!data.total) kanban.innerHTML = `<p class="kanban-message">${vide()}</p>`;
        else afficherPipeline(data.opportunites);
      } else {
        afficherListe(data.opportunites, data.total);
      }
    } catch (err) {
      const msg = `Erreur de chargement : ${esc(err.message)}`;
      if (vue === "pipeline") kanban.innerHTML = `<p class="kanban-message">${msg}</p>`;
      else corps.innerHTML = `<tr><td colspan="9" class="empty">${msg}</td></tr>`;
    }
  }

  const ouvrir = (id) => CRM.ouvrir(id, charger);

  kanban.addEventListener("click", (e) => {
    const c = e.target.closest(".op-carte");
    if (c) ouvrir(c.dataset.id);
  });
  corps.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn-fiche");
    if (btn) ouvrir(btn.closest("tr").dataset.id);
  });
  const basculerRelances = (actif) => {
    relancesEl.setAttribute("aria-pressed", String(actif));
    charger();
  };
  relancesEl.addEventListener("click", () => basculerRelances(relancesEl.getAttribute("aria-pressed") !== "true"));
  kpis.addEventListener("click", (e) => { if (e.target.closest("[data-relances]")) basculerRelances(true); });
  kpis.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.closest("[data-relances]")) basculerRelances(true);
  });
  document.querySelectorAll(".vue-btn").forEach((b) => b.addEventListener("click", () => choisirVue(b.dataset.vue)));
  [commercialEl, statutEl].filter(Boolean).forEach((el) => el.addEventListener("change", charger));
  rechercheEl.addEventListener("keydown", (e) => { if (e.key === "Enter") charger(); });
  document.getElementById("suivi-reset").addEventListener("click", () => {
    if (commercialEl) commercialEl.value = "tous";
    statutEl.value = "";
    rechercheEl.value = "";
    relancesEl.setAttribute("aria-pressed", "false");
    charger();
  });

  (async () => {
    ref = await CRM.references();
    CRM.entrees("statuts").forEach(([v, l]) => statutEl.add(new Option(l, v)));
    if (commercialEl) {
      ref.commerciaux.filter((c) => c.id !== ref.moi)
        .forEach((c) => commercialEl.add(new Option(`Suivies par ${c.nom}`, c.id)));
    }
    choisirVue(vue === "liste" ? "liste" : "pipeline");
    // Arrivée depuis « Pris par moi » ou la carte : /suivi?fiche=<id>.
    const id = new URL(location.href).searchParams.get("fiche");
    if (id) ouvrir(id);
  })();
})();
