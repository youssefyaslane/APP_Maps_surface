// Page Base de données : fenêtres d'ajout et de modification, test d'une
// connexion, synchronisation suivie en direct. Tout texte venu du serveur
// (noms de serveur, messages d'erreur) passe par textContent, jamais innerHTML.
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const main = $(".db-main");
  const REQUIRED = ["users", "companies", "ia_segments", "ms_buildings", "audit_log"];

  const fmt = (n) => (typeof n === "number" ? n.toLocaleString("fr-FR") : "—");

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function check(list, state, label, detail) {
    const li = el("li", `db-check is-${state}`);
    const icon = { ok: "✓", ko: "✗", warn: "!", na: "–" }[state];
    li.append(el("span", "db-check-icon", icon));
    const text = el("span", "db-check-text");
    text.append(el("strong", null, label));
    if (detail) text.append(el("span", "db-check-detail", detail));
    li.append(text);
    list.append(li);
  }

  $$("[data-close]").forEach((btn) =>
    btn.addEventListener("click", () => btn.closest("dialog").close())
  );

  $$("form[data-confirm]").forEach((form) =>
    form.addEventListener("submit", (e) => {
      if (!confirm(form.dataset.confirm)) e.preventDefault();
    })
  );

  // ---- Ajouter / modifier / renommer -------------------------------------

  const formDialog = $("#db-form-dialog");
  const form = $("#db-form");
  const conn = $(".db-conn");
  const password = $("#db-password");
  const eye = $("#db-eye");

  function setPasswordVisible(visible) {
    password.type = visible ? "text" : "password";
    eye.setAttribute("aria-pressed", String(visible));
    eye.setAttribute("aria-label", visible ? "Masquer le mot de passe" : "Afficher le mot de passe");
  }

  function openForm({ title, action, values, editing, envOnly }) {
    form.reset();
    form.action = action;
    $("#db-form-title").textContent = title;
    Object.entries(values || {}).forEach(([name, value]) => {
      const field = form.elements.namedItem(name);
      if (field && value !== undefined && value !== "None") field.value = value;
    });
    // Base du .env : seul le nom se modifie. Un fieldset désactivé n'est ni
    // validé ni envoyé, ce qui neutralise les champs obligatoires masqués.
    conn.disabled = Boolean(envOnly);
    conn.hidden = Boolean(envOnly);
    $("#db-env-hint").hidden = !envOnly;
    // En modification, le mot de passe n'est jamais renvoyé par le serveur :
    // champ vide = conserver celui qui est enregistré.
    password.required = !editing;
    password.placeholder = editing ? "Laisser vide pour conserver l'actuel" : "";
    $("#db-pw-req").hidden = editing;
    setPasswordVisible(false);
    formDialog.showModal();
  }

  $("#db-add-btn")?.addEventListener("click", () =>
    openForm({ title: "Ajouter une configuration", action: form.dataset.addUrl, editing: false })
  );

  $$("[data-edit]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const d = btn.closest("tr").dataset;
      const envOnly = d.env === "true";
      openForm({
        title: envOnly ? "Renommer la base du fichier .env" : `Modifier « ${d.label} »`,
        action: btn.dataset.edit,
        editing: true,
        envOnly,
        values: envOnly
          ? { label: d.label }
          : {
              label: d.label, host: d.host, port: d.port, dbname: d.dbname,
              schema: d.schema, user: d.user, sslmode: d.sslmode,
            },
      });
    })
  );

  eye.addEventListener("click", () => setPasswordVisible(password.type === "password"));

  // ---- Tester -------------------------------------------------------------

  const testDialog = $("#db-test-dialog");
  const testBody = $("#db-test-body");

  function renderReport(r, isActive) {
    testBody.replaceChildren();
    const list = el("ul", "db-checks");
    testBody.append(list);
    if (!r.ok) {
      check(list, "ko", "Connexion", r.error || "Échec de la connexion.");
      return;
    }
    check(list, "ok", "Connexion", `PostgreSQL ${r.server_version}`);
    if (r.ssl === true) check(list, "ok", "SSL", "Échanges chiffrés");
    else if (r.ssl === false) check(list, "warn", "SSL", "Échanges non chiffrés");
    else check(list, "na", "SSL", "Non déterminé");

    if (!r.schema_exists) {
      check(list, "ko", `Schéma « ${r.schema} »`, "Absent : il doit être créé sur le serveur.");
      testBody.append(el("p", "db-verdict is-ko", "Créez d'abord le schéma sur ce serveur."));
      return;
    }
    check(list, "ok", `Schéma « ${r.schema} »`, "Présent");
    check(
      list, r.can_create ? "ok" : "ko", "Droit de créer des tables",
      r.can_create ? "Accordé" : "Refusé : la migration ne pourra pas créer les tables."
    );

    const present = Object.entries(r.tables).filter(([, n]) => n !== null);
    check(
      list, "na", "Tables de l'application",
      present.length
        ? present.map(([t, n]) => `${t} ${fmt(n)}`).join(" · ")
        : "Aucune pour l'instant"
    );
    if (r.admin_present !== null) {
      check(
        list, r.admin_present ? "ok" : "ko", "Votre compte administrateur",
        r.admin_present ? "Présent" : "Absent : l'activation vous déconnecterait."
      );
    }

    const complete = REQUIRED.every((t) => r.tables[t] !== null && r.tables[t] !== undefined);
    let verdict;
    if (isActive) verdict = ["ok", "C'est la base active."];
    else if (complete && r.admin_present) verdict = ["ok", "Prête à être activée."];
    else if (r.can_create) verdict = ["ok", "Prête à recevoir les données : lancez « Migrer les données »."];
    else verdict = ["ko", "Donnez à cet utilisateur le droit de créer des tables dans ce schéma."];
    testBody.append(el("p", `db-verdict is-${verdict[0]}`, verdict[1]));
  }

  $$("[data-test]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const row = btn.closest("tr").dataset;
      $("#db-test-title").textContent = `Test — ${row.label}`;
      testBody.replaceChildren(el("p", "db-hint", "Connexion en cours…"));
      testDialog.showModal();
      try {
        const resp = await fetch(btn.dataset.test, {
          method: "POST",
          headers: { Accept: "application/json" },
        });
        renderReport(await resp.json(), row.active === "true");
      } catch {
        testBody.replaceChildren(
          el("p", "admin-error", "La page n'a pas pu joindre l'application. Rechargez-la et réessayez.")
        );
      }
    })
  );

  // ---- Migrer (synchroniser) ----------------------------------------------

  const migDialog = $("#db-migrate-dialog");
  const progressBox = $("#db-migrate-progress");
  const result = $("#db-migrate-result");
  const go = $("#db-migrate-go");
  const reload = $("#db-migrate-reload");
  const cancel = $("#db-migrate-cancel");
  let migrateUrl = null;
  let lastDiff = null;
  let polling = false;

  function totals(diff) {
    const t = { added: 0, changed: 0, removed: 0 };
    Object.values(diff || {}).forEach((d) => {
      t.added += d.added;
      t.changed += d.changed;
      t.removed += d.removed;
    });
    return t;
  }

  function diffTable(diff) {
    const wrap = el("div", "db-diff-wrap");
    const table = el("table", "db-diff");
    const head = el("tr");
    ["Table", "Ajoutées", "Modifiées", "Retirées"].forEach((h) => head.append(el("th", null, h)));
    const thead = el("thead");
    thead.append(head);
    const tbody = el("tbody");
    Object.entries(diff).forEach(([name, d]) => {
      const tr = el("tr");
      tr.append(el("td", "db-mono", name));
      [["added", "+", "is-add"], ["changed", "~", "is-change"], ["removed", "−", "is-remove"]].forEach(
        ([key, sign, cls]) => {
          const n = d[key];
          tr.append(el("td", n ? `db-num ${cls}` : "db-num is-zero", n ? `${sign} ${fmt(n)}` : "—"));
        }
      );
      tbody.append(tr);
    });
    table.append(thead, tbody);
    wrap.append(table);
    return wrap;
  }

  function renderJob(j) {
    const running = j.state === "running";
    progressBox.hidden = !running;
    result.replaceChildren();
    go.hidden = true;
    reload.hidden = true;

    if (running) {
      $("#db-step").textContent = j.step || "…";
      let pct = 3;
      if (j.total) pct = Math.max(3, Math.round(((j.index - 1) / j.total) * 100));
      $("#db-bar-fill").style.width = `${pct}%`;
      $("#db-step-detail").textContent =
        j.table && j.total
          ? `Table ${j.index} sur ${j.total}${j.rows ? ` — ${fmt(j.rows)} lignes` : ""}`
          : "";
      cancel.textContent = "Fermer (le travail continue)";
      return;
    }
    cancel.textContent = "Fermer";

    if (j.state === "error") {
      result.append(el("p", "admin-error", j.error));
      reload.hidden = false;
      return;
    }
    if (j.state !== "done") return;

    const t = totals(j.diff);
    result.append(diffTable(j.diff));
    if (j.mode === "apply") {
      result.append(el("p", "db-verdict is-ok",
        "Terminé et vérifié : les deux bases sont identiques, sans aucune ligne en double."));
      reload.hidden = false;
      return;
    }
    // Aperçu
    lastDiff = j.diff;
    if (j.blocked) {
      result.append(el("p", "db-verdict is-ko", j.blocked));
    } else if (!t.added && !t.changed && !t.removed) {
      result.append(el("p", "db-verdict is-ok", "Les deux bases sont déjà identiques : rien à faire."));
    } else {
      result.append(el("p", "db-verdict is-warn",
        `À appliquer : ${fmt(t.added)} ligne(s) ajoutée(s), ${fmt(t.changed)} modifiée(s), `
        + `${fmt(t.removed)} retirée(s).`));
      go.hidden = !migrateUrl;
      go.disabled = false;
    }
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      for (;;) {
        const resp = await fetch(main.dataset.statusUrl, { headers: { Accept: "application/json" } });
        const job = await resp.json();
        renderJob(job);
        if (job.state !== "running") break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch {
      renderJob({ state: "error", error: "Suivi interrompu : rechargez la page pour voir où en est le travail." });
    } finally {
      polling = false;
    }
  }

  async function startJob(mode) {
    progressBox.hidden = false;
    result.replaceChildren();
    go.hidden = true;
    $("#db-step").textContent = mode === "apply" ? "Démarrage de la mise à jour…" : "Comparaison des deux bases…";
    $("#db-bar-fill").style.width = "3%";
    $("#db-step-detail").textContent = "";
    try {
      const resp = await fetch(migrateUrl, {
        method: "POST",
        headers: { Accept: "application/json" },
        body: new URLSearchParams({ mode }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        renderJob({ state: "error", error: data.error });
        return;
      }
      poll();
    } catch {
      renderJob({ state: "error", error: "La page n'a pas pu joindre l'application." });
    }
  }

  go.addEventListener("click", () => {
    const removed = totals(lastDiff).removed;
    if (removed && !confirm(`${fmt(removed)} ligne(s) absentes de la base active seront retirées de la cible. Continuer ?`)) {
      return;
    }
    go.disabled = true;
    startJob("apply");
  });

  $$("[data-migrate]").forEach((btn) =>
    btn.addEventListener("click", () => {
      migrateUrl = btn.dataset.migrate;
      $("#db-migrate-target").textContent = btn.closest("tr").dataset.label;
      $("#db-migrate-source").textContent = main.dataset.activeLabel;
      migDialog.showModal();
      startJob("preview");
    })
  );

  // Page rechargée pendant un travail en cours : on reprend le suivi.
  if (main.dataset.jobState === "running") {
    $("#db-migrate-target").textContent = main.dataset.jobTarget;
    $("#db-migrate-source").textContent = main.dataset.activeLabel;
    migDialog.showModal();
    poll();
  }
})();
