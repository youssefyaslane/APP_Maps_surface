// Fiche CRM d'un prospect (phase 7) : panneau latéral du tableau de bord.
// Tout le monde voit tout ; chacun prend les prospects qu'il suit. Chaque
// enregistrement renvoie la fiche à jour, réaffichée telle quelle : la page
// ne recalcule rien elle-même (économies, productible… viennent du serveur).
window.CRM = (() => {
  const fiche = document.getElementById("crm-fiche");
  const voile = document.getElementById("crm-voile");
  let ref = null;
  let courant = null;
  // Partie de la fiche ouverte (une seule à la fois) : gardée d'un enregistrement
  // à l'autre, remise sur « Potentiel » à l'ouverture d'une autre fiche.
  let ouvert = "potentiel";
  let surChangement = () => {};

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const nb = (n, d = 0) => (n === null || n === undefined ? "—"
    : Number(n).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const dh = (n) => (n ? `${nb(n)} DH` : "—");
  const date = (iso) => new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
  const jourCourt = (iso) => new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  // Temps passé depuis une date : « aujourd'hui », « 3 j », « 5 sem. », « 2 mois ».
  const duree = (iso) => {
    const jours = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
    if (jours < 1) return "aujourd'hui";
    if (jours < 14) return `${jours} j`;
    if (jours < 60) return `${Math.floor(jours / 7)} sem.`;
    return `${Math.floor(jours / 30)} mois`;
  };
  const quand = (iso) => new Date(iso).toLocaleString("fr-FR", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
  // Prochaine action proposée pour chaque étape (placeholder, puis objet par défaut).
  const ACTIONS = {
    a_contacter: "Premier appel",
    contacte: "Fixer un rendez-vous",
    rdv_fixe: "Rendez-vous : visite du site",
    visite_faite: "Envoyer le devis",
    devis_envoye: "Relancer le devis",
  };

  // Couples (valeur, libellé) dans l'ordre du parcours, pas dans l'ordre alphabétique du JSON.
  const entrees = (nom) => (ref.ordres?.[nom] || Object.keys(ref[nom])).map((v) => [v, ref[nom][v]]);
  const options = (nom, choisi, vide) =>
    (vide ? `<option value="">${esc(vide)}</option>` : "") +
    entrees(nom).map(([v, l]) => `<option value="${esc(v)}"${v === choisi ? " selected" : ""}>${esc(l)}</option>`).join("");

  async function references() {
    if (!ref) {
      const resp = await fetch("/api/crm/references");
      ref = await resp.json();
    }
    return ref;
  }

  function fermer() {
    fiche.hidden = true;
    voile.hidden = true;
    courant = null;
    const url = new URL(location.href);
    if (url.searchParams.has("fiche")) {
      url.searchParams.delete("fiche");
      history.replaceState(null, "", url);
    }
  }

  async function ouvrir(id, apresChangement) {
    if (apresChangement) surChangement = apresChangement;
    await references();
    if (!courant || String(courant.id) !== String(id)) ouvert = "potentiel";
    fiche.hidden = false;
    voile.hidden = false;
    fiche.innerHTML = `<p class="crm-chargement">Chargement de la fiche…</p>`;
    try {
      const resp = await fetch(`/api/crm/${id}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Fiche introuvable.");
      afficher(data);
    } catch (err) {
      fiche.innerHTML = `<button type="button" class="crm-fermer" aria-label="Fermer">${icone("fermer")}</button>
        <p class="crm-message erreur">${esc(err.message)}</p>`;
      fiche.querySelector(".crm-fermer").addEventListener("click", fermer);
    }
  }

  async function envoyer(action, corps, bouton) {
    if (bouton) bouton.disabled = true;
    try {
      const resp = await fetch(`/api/crm/${courant.id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(corps || {}),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || "L'enregistrement a échoué.");
      afficher(data, "Enregistré.");
      surChangement();
    } catch (err) {
      message(err.message, true);
      if (bouton) bouton.disabled = false;
    }
  }

  function message(texte, erreur) {
    const el = fiche.querySelector(".crm-message");
    if (!el) return;
    el.textContent = texte;
    el.className = `crm-message${erreur ? " erreur" : " ok"}`;
    el.hidden = false;
  }

  function blocPotentiel(f) {
    const c = f.calculs;
    if (!f.solar_kwc) {
      return `<p class="crm-aide">Aucun toit identifié sous cette entreprise : à tracer sur la carte.</p>`;
    }
    const economies = c.economies_reelles_dh
      ? `<div><b>${dh(c.economies_reelles_dh)}</b><span>économies réelles par an · ${nb(c.part_consommee * 100)} % de la production consommée sur place, ${nb(c.couverture_conso * 100)} % de la facture couverte</span></div>`
      : `<div><b>${dh(c.economies_max_dh)}</b><span>économies max par an · si toute la production est consommée sur place</span></div>`;
    return `
      ${c.toiture_inutilisable ? `<p class="crm-alerte">Toiture jugée inutilisable lors de la visite.</p>` : ""}
      <div class="crm-chiffres">
        <div><b>${nb(f.solar_kwc, f.solar_kwc % 1 ? 1 : 0)} kWc</b><span>${nb(f.solar_panels)} panneaux · toit ${nb(f.roof_area_m2)} m²</span></div>
        <div><b>${c.production_mwh ? `${nb(c.production_mwh)} MWh` : "—"}</b><span>par an · ${c.productible_source === "visite" ? "pente relevée en visite" : "panneaux à plat (hypothèse)"}</span></div>
        <div><b>${c.co2_t ? `${nb(c.co2_t)} t` : "—"}</b><span>de CO₂ évitées par an</span></div>
        ${economies}
      </div>`;
  }

  function blocSuivi(f) {
    const moi = ref.moi;
    let commercial;
    if (!f.commercial) {
      commercial = `<span>Personne ne suit ce prospect.</span>
        ${ref.admin ? "" : `<button type="button" class="crm-btn principal" data-action="prendre">Je prends ce prospect</button>`}`;
    } else if (f.commercial.id === moi) {
      commercial = `<span>Vous suivez ce prospect${f.pris_le ? ` depuis le ${date(f.pris_le)}` : ""}.</span>
        <button type="button" class="crm-btn" data-action="liberer">Libérer</button>`;
    } else {
      commercial = `<span>Suivi par <b>${esc(f.commercial.nom)}</b>.</span>
        ${ref.admin ? `<button type="button" class="crm-btn" data-action="liberer">Libérer</button>` : ""}`;
    }
    const aujourdhui = new Date().toISOString().slice(0, 10);
    return `
      <div class="crm-ligne crm-commercial">${commercial}</div>
      <label class="crm-champ">Étape
        <select name="statut">${options("statuts", f.statut)}</select>
      </label>
      <label class="crm-champ crm-raison"${f.statut === "perdu" ? "" : " hidden"}>Raison
        <select name="raison">${options("raisons_perte", f.raison_perte, "— Choisir —")}</select>
      </label>
      <div class="crm-relance">
        <h4>Prochaine action</h4>
        <div class="crm-ligne">
          <input type="date" name="relance_le" min="${aujourdhui}" value="${esc(f.relance?.le || "")}" aria-label="Date de la prochaine action" />
          <input type="text" name="relance_objet" value="${esc(f.relance?.objet || "")}" placeholder="${esc(ACTIONS[f.statut] || "")}" />
        </div>
        <p class="crm-aide">Sans date, aucune relance n'est prévue.</p>
      </div>
      <p class="crm-aide crm-clos" hidden>Prospect signé ou perdu : pas de prochaine action.</p>
      <label class="crm-champ">Commentaire
        <textarea name="commentaire" rows="2" placeholder="Ce qui s'est passé, ce qui a été convenu… (gardé dans l'historique du pipeline)"></textarea>
      </label>
      <p class="crm-a-enregistrer" hidden>Étape modifiée, pas encore enregistrée. L'ancienne relance concernait l'étape d'avant : indiquez la prochaine action, ou laissez vide.</p>
      <button type="button" class="crm-btn principal" data-action="suivi">Enregistrer le suivi</button>
      ${historiquePipeline(f)}`;
  }

  // Historique du pipeline : chaque enregistrement du suivi, avec l'étape, la
  // prochaine action prévue à ce moment et le commentaire du commercial.
  function historiquePipeline(f) {
    const lignes = f.parcours.lignes || [];
    if (!lignes.length) return "";
    return `<h4>Historique du pipeline</h4>
      <ol class="crm-chronologie">${lignes.map((l) => `
        <li class="${l.changement ? "changement" : ""} etape-${esc(l.etape)}">
          <span class="crm-chrono-tete">
            <b>${esc(ref.statuts[l.etape] || l.etape)}${l.raison_perte ? ` · ${esc(ref.raisons_perte[l.raison_perte] || l.raison_perte)}` : ""}</b>
            <span>${quand(l.le)}${l.auteur ? ` · ${esc(l.auteur)}` : ""}</span>
          </span>
          ${l.relance ? `<span class="crm-chrono-action">${icone("horloge")} Prochaine action : ${date(l.relance.le)}${l.relance.objet ? ` · ${esc(l.relance.objet)}` : ""}</span>` : ""}
          ${l.commentaire ? `<p>${esc(l.commentaire).replace(/\n/g, "<br>")}</p>` : ""}
        </li>`).join("")}</ol>`;
  }

  function blocDecideur(f) {
    const d = f.decideur;
    return `
      <div class="crm-grille">
        <label class="crm-champ">Nom<input type="text" name="nom" value="${esc(d.nom || "")}" /></label>
        <label class="crm-champ">Fonction<input type="text" name="fonction" value="${esc(d.fonction || "")}" placeholder="Directeur, DAF…" /></label>
        <label class="crm-champ">Téléphone<input type="tel" name="telephone" value="${esc(d.telephone || "")}" /></label>
        <label class="crm-champ">E-mail<input type="email" name="email" value="${esc(d.email || "")}" /></label>
      </div>
      <button type="button" class="crm-btn" data-action="decideur">Enregistrer le décideur</button>`;
  }

  function blocVisite(f) {
    const v = f.visite;
    return `
      <div class="crm-grille">
        <label class="crm-champ">Consommation annuelle (kWh)
          <input type="number" min="0" step="1000" name="conso_kwh_an" value="${v.conso_kwh_an ?? ""}" placeholder="Lue sur la facture" /></label>
        <label class="crm-champ">État de la toiture
          <select name="etat_toiture">${options("etats_toiture", v.etat_toiture, "— Non constaté —")}</select></label>
        <label class="crm-champ">Inclinaison des panneaux (°)
          <input type="number" min="0" max="90" step="1" name="inclinaison" value="${v.inclinaison ?? ""}" placeholder="0 = à plat" /></label>
        <label class="crm-champ">Orientation (°)
          <input type="number" min="-180" max="180" step="1" name="orientation" value="${v.orientation ?? ""}" placeholder="0 = sud, -90 = est, 90 = ouest" /></label>
      </div>
      <p class="crm-aide">La consommation remplace l'hypothèse des économies maximales ; la pente et l'orientation redemandent le productible à PVGIS.</p>
      <button type="button" class="crm-btn" data-action="visite">Enregistrer la visite</button>`;
  }

  function blocNotes(f) {
    const notes = f.notes.map((n) => `
      <li><p>${esc(n.texte).replace(/\n/g, "<br>")}</p>
        <span>${esc(n.auteur || "Compte supprimé")} · ${quand(n.le)}</span></li>`).join("");
    return `
      <textarea name="note" rows="3" placeholder="Appel, visite, échange…"></textarea>
      <button type="button" class="crm-btn" data-action="note">Ajouter la note</button>
      ${notes ? `<ul class="crm-notes">${notes}</ul>` : `<p class="crm-aide">Aucune note pour l'instant.</p>`}`;
  }

  function blocHistorique(f) {
    if (!f.historique.length) return `<p class="crm-aide">Aucune action enregistrée.</p>`;
    return `<ul class="crm-historique">${f.historique.map((h) => `
      <li><span class="crm-date">${quand(h.le)}</span>
        <span><b>${esc(h.auteur || "Système")}</b> ${esc(h.libelle)}${h.detail ? ` : ${esc(h.detail)}` : ""}</span></li>`).join("")}</ul>`;
  }

  // Pipeline de l'opportunité : étapes passées cochées, étape actuelle en
  // évidence, puis l'issue (signé ou perdu). Un commercial clique sur une
  // étape pour la préparer dans « Suivi », qu'il enregistre ensuite.
  function pipelineVisuel(f) {
    const parcours = ["a_contacter", "contacte", "rdv_fixe", "visite_faite", "devis_envoye"];
    const rang = parcours.indexOf(f.statut);
    const etapes = parcours.map((etape, i) => {
      const etat = f.statut === "signe" || (rang >= 0 && i < rang) ? "fait"
        : i === rang ? "actuel" : f.statut === "perdu" ? "abandon" : "avenir";
      // Historique du pipeline : date d'arrivée dans l'étape, et qui l'a franchie.
      const passage = f.parcours.etapes[etape];
      const titre = passage
        ? `${ref.statuts[etape]} — le ${date(passage.le)}${passage.auteur ? `, par ${passage.auteur}` : ""}`
        : ref.statuts[etape];
      const reperes = etat === "actuel" && f.parcours.etape_depuis
        ? `depuis ${duree(f.parcours.etape_depuis)}`
        : passage && etat === "fait" ? jourCourt(passage.le) : "";
      return `<li class="crm-pas ${etat}">
        <button type="button" data-etape="${etape}"${ref.admin ? " disabled" : ""} title="${esc(titre)}">
          <span class="crm-pastille">${etat === "fait" ? icone("valide") : i + 1}</span>
          <span class="crm-pas-nom">${esc(ref.statuts[etape])}</span>
          ${reperes ? `<span class="crm-pas-date">${esc(reperes)}</span>` : ""}
        </button></li>`;
    }).join("");
    const fin = f.parcours.etapes[f.statut];
    const finDate = fin ? `<span class="crm-pas-date">${esc(jourCourt(fin.le))}</span>` : "";
    const issue = f.statut === "signe"
      ? `<li class="crm-pas issue gagne"><span class="crm-pastille">${icone("valide")}</span><span class="crm-pas-nom">Signé</span>${finDate}</li>`
      : f.statut === "perdu"
        ? `<li class="crm-pas issue perdu"><span class="crm-pastille">${icone("fermer")}</span><span class="crm-pas-nom">Perdu${
            f.raison_perte ? ` · ${esc(ref.raisons_perte[f.raison_perte])}` : ""}</span>${finDate}</li>`
        : `<li class="crm-pas issue avenir"><span class="crm-pastille">${icone("valide")}</span><span class="crm-pas-nom">Signé</span></li>`;
    return `<ol class="crm-pipeline" aria-label="Étapes de l'opportunité">${etapes}${issue}</ol>`;
  }

  // Une partie repliable de la fiche : titre et résumé, puis ses champs une fois ouverte.
  function partie(bloc, titre, resume, contenu) {
    return `<details class="crm-partie" data-bloc="${bloc}"${bloc === ouvert ? " open" : ""}>
      <summary><h3>${esc(titre)}</h3><span class="crm-resume">${esc(resume)}</span>${icone("chevron", "crm-chevron")}</summary>
      <div class="crm-corps">${contenu}</div>
    </details>`;
  }

  const resumePotentiel = (f) => (f.solar_kwc
    ? `${nb(f.solar_kwc)} kWc · ${f.calculs.production_mwh ? `${nb(f.calculs.production_mwh)} MWh/an` : "production à calculer"}`
    : "Aucun toit identifié");
  const resumeSuivi = (f) => [
    ref.statuts[f.statut] || f.statut,
    f.raison_perte ? ref.raisons_perte[f.raison_perte] : null,
    f.relance ? `relance le ${date(f.relance.le)}` : null,
  ].filter(Boolean).join(" · ");
  const resumeDecideur = (f) => [f.decideur.nom, f.decideur.fonction].filter(Boolean).join(" · ") || "À renseigner";
  const resumeVisite = (f) => {
    const v = f.visite;
    const faits = [
      v.conso_kwh_an ? `${nb(v.conso_kwh_an)} kWh/an` : null,
      v.etat_toiture ? ref.etats_toiture[v.etat_toiture] : null,
      v.inclinaison !== null && v.inclinaison !== undefined ? `pente ${nb(v.inclinaison)}°` : null,
    ].filter(Boolean);
    return faits.length ? faits.join(" · ") : "Pas encore faite";
  };

  function afficher(f, info) {
    courant = f;
    const contact = [
      f.phone ? `<a href="tel:${esc(f.phone)}">${icone("telephone")} ${esc(f.phone)}</a>` : "",
      f.website ? `<a href="${esc(f.website)}" target="_blank" rel="noopener">${icone("globe")} Site</a>` : "",
      `<a href="/carte?lat=${f.lat}&lon=${f.lon}">${icone("carte")} Carte</a>`,
    ].filter(Boolean).join("");
    fiche.innerHTML = `
      <header class="crm-entete">
        <div>
          <h2>${esc(f.name)}</h2>
          <p>${esc([f.category, f.city].filter(Boolean).join(" · "))}</p>
          ${f.address ? `<p class="crm-adresse">${esc(f.address)}</p>` : ""}
          <p class="crm-contact">${contact}</p>
        </div>
        <button type="button" class="crm-fermer" aria-label="Fermer la fiche">${icone("fermer")}</button>
      </header>
      ${pipelineVisuel(f)}
      <p class="crm-message" role="status" hidden></p>
      ${partie("potentiel", "Potentiel", resumePotentiel(f), blocPotentiel(f))}
      ${partie("suivi", "Suivi", resumeSuivi(f), blocSuivi(f))}
      ${partie("decideur", "Décideur", resumeDecideur(f), blocDecideur(f))}
      ${partie("visite", "Visite", resumeVisite(f), blocVisite(f))}
      ${partie("notes", "Notes", f.notes.length ? `${f.notes.length} note(s)` : "Aucune", blocNotes(f))}
      ${partie("historique", "Historique", `${f.historique.length} action(s)`, blocHistorique(f))}`;
    // Accordéon : ouvrir une partie referme les autres.
    fiche.querySelectorAll("details.crm-partie").forEach((d) => d.addEventListener("toggle", () => {
      if (!d.open) return;
      ouvert = d.dataset.bloc;
      fiche.querySelectorAll("details.crm-partie").forEach((autre) => { if (autre !== d) autre.open = false; });
    }));
    if (info) message(info, false);
    // Admin : consultation seule. Les champs sont figés, seuls « Fermer » et
    // « Libérer » restent ; le serveur refuse de toute façon les modifications.
    if (ref.admin) {
      fiche.querySelectorAll("input, select, textarea").forEach((el) => { el.disabled = true; });
      fiche.querySelectorAll("[data-action]").forEach((b) => {
        if (b.dataset.action !== "liberer") b.remove();
      });
      fiche.querySelector(".crm-message").insertAdjacentHTML("afterend",
        `<p class="crm-lecture-seule">${icone("oeil")} Consultation (admin) : le suivi est tenu par le commercial.</p>`);
    }

    fiche.querySelector(".crm-fermer").addEventListener("click", fermer);
    const statutEl = fiche.querySelector('[name="statut"]');
    const dateEl = fiche.querySelector('[name="relance_le"]');
    const objetEl = fiche.querySelector('[name="relance_objet"]');
    // Changer d'étape : la relance prévue concernait l'étape d'avant. Le
    // formulaire se vide et propose l'action suivante ; rien n'est enregistré
    // avant « Enregistrer le suivi », ce que la fiche signale.
    const majFormulaire = () => {
      const etape = statutEl.value;
      const change = etape !== f.statut;
      const clos = etape === "signe" || etape === "perdu";
      fiche.querySelector(".crm-raison").hidden = etape !== "perdu";
      fiche.querySelector(".crm-relance").hidden = clos;
      fiche.querySelector(".crm-clos").hidden = !clos;
      fiche.querySelector(".crm-a-enregistrer").hidden = !change || ref.admin;
      objetEl.placeholder = ACTIONS[etape] || "";
      if (change) {
        dateEl.value = "";
        objetEl.value = clos ? "" : ACTIONS[etape] || "";
      } else {
        dateEl.value = f.relance?.le || "";
        objetEl.value = f.relance?.objet || "";
      }
    };
    statutEl.addEventListener("change", majFormulaire);
    // Clic sur une étape du pipeline : la préparer dans « Suivi » et l'ouvrir.
    fiche.querySelectorAll(".crm-pipeline [data-etape]").forEach((b) => b.addEventListener("click", () => {
      statutEl.value = b.dataset.etape;
      majFormulaire();
      const suivi = fiche.querySelector('details[data-bloc="suivi"]');
      suivi.open = true;
      suivi.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }));
    if (statutEl.value === "signe" || statutEl.value === "perdu") {
      fiche.querySelector(".crm-relance").hidden = true;
      fiche.querySelector(".crm-clos").hidden = false;
    }
    const valeurs = (bloc) => Object.fromEntries(
      [...fiche.querySelectorAll(`[data-bloc="${bloc}"] [name]`)].map((el) => [el.name, el.value.trim()]));
    const actions = {
      prendre: (b) => envoyer("prendre", {}, b),
      liberer: (b) => confirm("Libérer ce prospect ? Un autre commercial pourra le prendre.") && envoyer("liberer", {}, b),
      suivi: (b) => {
        const v = valeurs("suivi");
        const clos = v.statut === "signe" || v.statut === "perdu";
        envoyer("suivi", {
          statut: v.statut,
          raison: v.raison || null,
          relance_le: clos ? null : v.relance_le || null,
          relance_objet: clos ? null : v.relance_objet,
          commentaire: v.commentaire || null,
        }, b);
      },
      decideur: (b) => envoyer("decideur", valeurs("decideur"), b),
      visite: (b) => envoyer("visite", valeurs("visite"), b),
      note: (b) => {
        const texte = fiche.querySelector('[name="note"]').value.trim();
        if (!texte) return message("La note est vide.", true);
        envoyer("notes", { texte }, b);
      },
    };
    fiche.querySelectorAll("[data-action]").forEach((b) =>
      b.addEventListener("click", () => actions[b.dataset.action](b)));
  }

  voile.addEventListener("click", fermer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !fiche.hidden) fermer();
  });

  return { ouvrir, references, duree, entrees: (nom) => entrees(nom), libelleStatut: (s) => ref?.statuts?.[s] || s };
})();
