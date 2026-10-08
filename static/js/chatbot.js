// Chatbot de recherche (templates/_chatbot.html) : envoie chaque message à
// /api/chatbot et affiche la réponse. Quand la requête et la ville sont
// connues, un récapitulatif propose « Lancer » ou « Annuler » ; la recherche
// (Apify, payante, ou Google Maps direct, gratuite) tourne alors côté serveur
// et la page suit /api/chatbot/etat.
(() => {
  const racine = document.getElementById("chatbot");
  if (!racine) return;
  const bouton = document.getElementById("chatbot-bouton");
  const panneau = document.getElementById("chatbot-panel");
  const fil = document.getElementById("chatbot-messages");
  const form = document.getElementById("chatbot-form");
  const saisie = document.getElementById("chatbot-saisie");
  const envoyer = document.getElementById("chatbot-envoyer");

  const ACCUEIL =
    "Bonjour ! Dites-moi quel type d'entreprise chercher et dans quelle ville.\n" +
    "Exemple : « usine à Casablanca »";
  const SUIVI_MS = 3000;

  // textContent partout : le texte du modèle n'est jamais interprété comme du HTML.
  const ajouter = (texte, classe) => {
    const bulle = document.createElement("div");
    bulle.className = `chatbot-msg ${classe}`;
    bulle.textContent = texte;
    fil.appendChild(bulle);
    fil.scrollTop = fil.scrollHeight;
    return bulle;
  };

  const defiler = () => {
    fil.scrollTop = fil.scrollHeight;
  };

  // Pendant une recherche, plus de nouveau message : le serveur le refuserait.
  const occuper = (occupe) => {
    envoyer.disabled = occupe;
    saisie.disabled = occupe;
  };

  // Statut de chaque lieu après la classification et l'outil 2.
  const STATUTS = {
    ajoutee: "Ajoutée",
    deja_en_base: "Déjà en base",
    doublon: "Doublon probable",
    ecartee: "Écartée",
    a_verifier: "À vérifier",
    entreprise: "Entreprise",
  };
  const ORDRE = ["ajoutee", "a_verifier", "deja_en_base", "doublon", "entreprise", "ecartee"];

  const lieux = (resultats) => {
    if (!resultats || !resultats.length) return;
    const liste = document.createElement("ol");
    liste.className = "chatbot-lieux";
    const rang = (r) => (ORDRE.indexOf(r.classe) + 1) || ORDRE.length + 1;
    [...resultats].sort((a, b) => rang(a) - rang(b)).forEach((r) => {
      const li = document.createElement("li");
      const nom = document.createElement("strong");
      nom.textContent = r.nom;
      if (STATUTS[r.classe]) {
        const badge = document.createElement("em");
        badge.className = `chatbot-statut ${r.classe}`;
        badge.textContent = STATUTS[r.classe];
        if (r.raison) badge.title = r.raison;
        nom.appendChild(badge);
      }
      li.appendChild(nom);
      // Potentiel calculé par l'outil 3 pour les entreprises ajoutées.
      const nombre = (n) => Math.round(n).toLocaleString("fr-FR");
      const potentiel = r.kwc
        ? `☀️ ${nombre(r.kwc)} kWc${r.production_mwh ? ` · ⚡ ${nombre(r.production_mwh)} MWh/an` : ""}${
            r.co2_t ? ` · 🌱 ${nombre(r.co2_t)} t CO₂/an` : ""}`
        : "";
      const detail = [potentiel, r.categorie, r.adresse].filter(Boolean).join(" · ");
      if (detail) {
        const span = document.createElement("span");
        span.textContent = detail;
        li.appendChild(span);
      }
      liste.appendChild(li);
    });
    fil.appendChild(liste);
    defiler();
  };

  const suivre = (methode = "apify") => {
    const attente = ajouter(
      methode === "google_maps"
        ? "Recherche gratuite sur Google Maps en cours… (deux à cinq minutes)"
        : "Recherche Apify en cours… (une à trois minutes)",
      "bot attente"
    );
    occuper(true);
    const tour = async () => {
      let data;
      try {
        const resp = await fetch("/api/chatbot/etat", { cache: "no-store" });
        data = await resp.json();
      } catch (err) {
        setTimeout(tour, SUIVI_MS); // coupure réseau passagère : on réessaie
        return;
      }
      if (data.statut === "en_cours") {
        setTimeout(tour, SUIVI_MS);
        return;
      }
      attente.remove();
      occuper(false);
      if (data.statut === "fini") {
        ajouter(data.reponse, data.erreur ? "erreur" : "bot");
        lieux(data.resultats);
      } else {
        ajouter(data.error || "La recherche a échoué.", "erreur");
      }
      saisie.focus();
    };
    setTimeout(tour, SUIVI_MS);
  };

  const decider = async (lancer, boutons, methode = "apify") => {
    boutons.forEach((b) => { b.disabled = true; });
    try {
      const resp = await fetch("/api/chatbot/lancer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lancer, methode }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        ajouter(data.error || "Action impossible.", "erreur");
        boutons.forEach((b) => { b.disabled = false; });
        return;
      }
      if (data.statut === "en_cours") suivre(methode);
      else ajouter(data.reponse, "bot");
    } catch (err) {
      ajouter("Erreur réseau : action non envoyée.", "erreur");
      boutons.forEach((b) => { b.disabled = false; });
    }
  };

  const recapitulatif = (r) => {
    const carte = document.createElement("div");
    carte.className = "chatbot-recap";
    const dl = document.createElement("dl");
    [
      ["Recherche", r.requetes.join(", ")],
      ["Ville", r.ville],
      ["Maximum", `${r.max_resultats} lieux par requête`],
    ].forEach(([cle, valeur]) => {
      const dt = document.createElement("dt");
      dt.textContent = cle;
      const dd = document.createElement("dd");
      dd.textContent = valeur;
      dl.append(dt, dd);
    });
    // Choix de la méthode : Apify (payante, fiable) ou Google Maps direct
    // (gratuite, plus lente, peut être bloquée par Google).
    const choix = document.createElement("fieldset");
    choix.className = "chatbot-methodes";
    const legende = document.createElement("legend");
    legende.textContent = "Méthode";
    choix.appendChild(legende);
    const nomGroupe = `methode-${Date.now()}`;
    [
      ["apify", "Apify", "payante, environ 0,08 $ par requête · rapide et fiable"],
      ["google_maps", "Google Maps direct", "gratuite · plus lente, peut être bloquée par Google"],
    ].forEach(([valeur, titre, detail], i) => {
      const etiquette = document.createElement("label");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = nomGroupe;
      radio.value = valeur;
      radio.checked = i === 0;
      const texte = document.createElement("span");
      const fort = document.createElement("strong");
      fort.textContent = titre;
      texte.append(fort, ` — ${detail}`);
      etiquette.append(radio, texte);
      choix.appendChild(etiquette);
    });
    const methodeChoisie = () => choix.querySelector("input:checked")?.value || "apify";

    const actions = document.createElement("div");
    actions.className = "chatbot-actions";
    const lancer = document.createElement("button");
    lancer.type = "button";
    lancer.className = "chatbot-lancer";
    lancer.textContent = "Lancer la recherche";
    const annuler = document.createElement("button");
    annuler.type = "button";
    annuler.className = "chatbot-annuler";
    annuler.textContent = "Annuler";
    const boutons = [lancer, annuler, ...choix.querySelectorAll("input")];
    lancer.addEventListener("click", () => decider(true, boutons, methodeChoisie()));
    annuler.addEventListener("click", () => decider(false, boutons));
    actions.append(lancer, annuler);
    const note = document.createElement("p");
    note.textContent = "La recherche ne part qu'après ce clic.";
    carte.append(dl, choix, actions, note);
    fil.appendChild(carte);
    defiler();
  };

  const ouvrir = (ouvert) => {
    panneau.hidden = !ouvert;
    bouton.setAttribute("aria-expanded", String(ouvert));
    bouton.setAttribute("aria-label", ouvert ? "Fermer l'assistant de recherche" : "Ouvrir l'assistant de recherche");
    if (ouvert) {
      if (!fil.childElementCount) ajouter(ACCUEIL, "bot");
      saisie.focus();
    }
  };

  // Zone de saisie qui grandit avec le texte ; Entrée envoie, Maj+Entrée
  // passe à la ligne.
  const ajuster = () => {
    saisie.style.height = "auto";
    saisie.style.height = `${saisie.scrollHeight}px`;
  };
  saisie.addEventListener("input", ajuster);
  saisie.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  bouton.addEventListener("click", () => ouvrir(panneau.hidden));
  document.getElementById("chatbot-fermer").addEventListener("click", () => ouvrir(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panneau.hidden) ouvrir(false);
  });

  document.getElementById("chatbot-nouveau").addEventListener("click", async () => {
    try {
      const resp = await fetch("/api/chatbot/nouveau", { method: "POST" });
      if (resp.status === 409) {
        ajouter("Une recherche est en cours : attendez son résultat avant d'en commencer une autre.", "erreur");
        return;
      }
    } catch (err) {
      // Sans réseau, on vide quand même l'affichage ; le serveur reprendra
      // l'ancienne conversation au prochain message.
    }
    fil.replaceChildren();
    ajouter(ACCUEIL, "bot");
    saisie.focus();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = saisie.value.trim();
    if (!message) return;
    ajouter(message, "user");
    saisie.value = "";
    ajuster();
    envoyer.disabled = true;
    const attente = ajouter("…", "bot attente");
    try {
      const resp = await fetch("/api/chatbot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = await resp.json().catch(() => ({}));
      attente.remove();
      if (!resp.ok) {
        ajouter(data.error || "Le chatbot n'a pas pu répondre.", "erreur");
      } else {
        ajouter(data.reponse, "bot");
        if (data.confirmation) recapitulatif(data);
      }
    } catch (err) {
      attente.remove();
      ajouter("Erreur réseau : message non envoyé.", "erreur");
    } finally {
      if (!saisie.disabled) {
        envoyer.disabled = false;
        saisie.focus();
      }
    }
  });
})();
