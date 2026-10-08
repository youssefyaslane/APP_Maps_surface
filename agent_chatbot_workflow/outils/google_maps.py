"""Outil 1 gratuit : recherche directe sur Google Maps, avec un navigateur
automatique (Playwright et Chromium), sans clé ni coût.

Même entrée et même sortie que l'outil Apify (apify.rechercher), si bien que
la classification, l'écriture et le calcul du potentiel ne voient pas la
différence. Pour chaque requête :
1. ouvre la recherche Google Maps, fait défiler la liste jusqu'à
   MAX_PAR_REQUETE fiches non sponsorisées (LISTE_JS) ;
2. ouvre chaque fiche et lit nom, catégorie, adresse, téléphone, site, note
   (FICHE_JS) ;
3. lit les coordonnées et l'identifiant Google dans le lien de la fiche.

À savoir : plus lent qu'Apify (les fiches s'ouvrent une à une), et fragile.
Google peut afficher un CAPTCHA ou bloquer l'adresse du serveur, et un
changement de sa page casse les sélecteurs : c'est ce qu'annonce l'erreur
« aucune fiche lisible ». Les conditions d'utilisation de Google interdisent
la lecture automatique : à réserver aux petites recherches, Apify restant la
méthode sûre.
"""
import asyncio
import random
import re
from urllib.parse import quote_plus, unquote

MAX_PAR_REQUETE = 20
DELAI_NAVIGATION_MS = 30000

# Exécuté sur la page de recherche : fait défiler la liste, puis renvoie nom et
# lien des __MAX__ premières fiches, sans annonce sponsorisée ni doublon.
LISTE_JS = r"""async () => {
  const MAX = __MAX__;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, ms = 10000) => {
    for (let t = 0; t < ms; t += 300) { const v = fn(); if (v) return v; await sleep(300); }
    return null;
  };
  const feed = await waitFor(() => document.querySelector('[role=feed]'), 15000);
  if (!feed) {
    // Un seul résultat : Google ouvre directement la fiche.
    if (document.querySelector('[role=main] h1')) return { error: 'single_result' };
    return { error: 'no_results_list' };
  }
  const cards = () => {
    const seen = new Set();
    return [...feed.querySelectorAll('a[href*="/maps/place/"]')]
      .filter(a => !/Sponsoris|Sponsored/i.test(a.parentElement.innerText))
      .filter(a => {
        const key = (a.href.match(/!1s(0x[0-9a-f]+:0x[0-9a-f]+)/) || [a.href.split('?')[0]])[0];
        return seen.has(key) ? false : seen.add(key);
      });
  };
  // Défile jusqu'à MAX fiches, ou trois défilements de suite sans nouvelle fiche.
  for (let i = 0, stale = 0; i < 15 && stale < 3 && cards().length < MAX; i++) {
    const before = cards().length;
    feed.scrollTo(0, feed.scrollHeight);
    await waitFor(() => cards().length > before, 3000);
    await sleep(800);
    stale = cards().length > before ? 0 : stale + 1;
    if (/Vous êtes arrivé à la fin|reached the end/i.test(feed.innerText)) break;
  }
  return cards().slice(0, MAX).map(a => ({ nom: a.getAttribute('aria-label') || '', lien: a.href }));
}"""

# Exécuté sur la page d'une fiche : attend son chargement, puis lit les champs.
# Sélecteurs fondés sur data-item-id, aria-label et jsaction, plus stables que
# les classes CSS générées par Google. Page en français (locale fr-FR).
FICHE_JS = r"""async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, ms) => {
    for (let t = 0; t < ms; t += 300) { const v = fn(); if (v) return v; await sleep(300); }
    return null;
  };
  const h1 = await waitFor(() => document.querySelector('[role=main] h1'), 15000);
  const r = h1?.closest('[role=main]') || document;
  await waitFor(() => r.querySelector('[data-item-id]'), 6000);
  await sleep(1000);
  const q = s => r.querySelector(s);
  const label = (s, prefix) => (q(s)?.getAttribute('aria-label') || '').replace(prefix, '').trim();
  const imgs = [...r.querySelectorAll('[role=img][aria-label]')].map(e => e.getAttribute('aria-label'));
  return {
    nom: h1?.textContent.trim() || '',
    categorie: (q('button[jsaction$=".category"]') || q('button.DkEaL'))?.textContent.trim() || '',
    adresse: label('[data-item-id="address"]', /^Adresse\s*:\s*/),
    telephone: label('[data-item-id^="phone:tel:"]', /^Numéro de téléphone\s*:\s*/),
    note: (imgs.find(t => /étoiles\s*$/.test(t)) || '').replace(/\s*étoiles\s*$/, ''),
    site: q('a[data-item-id="authority"]')?.href || '',
  };
}"""


class GoogleMapsIndisponible(RuntimeError):
    """La recherche gratuite ne peut pas tourner (navigateur absent, page
    bloquée ou changée)."""


def lire_lien(url):
    """Coordonnées et identifiants d'un lien /maps/place/.../data=… :
    !3d<lat> !4d<lon>, !19s<place_id ChIJ…>, !1s0x…:0x<b> (CID = int(b, 16))."""
    def extraire(motif):
        m = re.search(motif, url or "")
        return m.group(1) if m else ""

    feature = extraire(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)")
    lat, lon = extraire(r"!3d(-?\d+(?:\.\d+)?)"), extraire(r"!4d(-?\d+(?:\.\d+)?)")
    return {
        "lat": float(lat) if lat else None,
        "lon": float(lon) if lon else None,
        "place_id": unquote(extraire(r"!19s([^!?]+)")) or None,
        "cid": str(int(feature.split(":")[1], 16)) if feature else None,
    }


def _note(texte):
    try:
        return float(str(texte).replace(",", ".").strip())
    except ValueError:
        return None


def normaliser(fiches):
    """Fiches lues → même format que apify.normaliser. L'identifiant est le
    place_id Google (ChIJ…), celui d'Apify, pour que les deux méthodes se
    reconnaissent ; à défaut, le CID préfixé « cid: ». Une fiche sans nom, sans
    coordonnées ou sans identifiant est écartée ; un doublon n'est gardé qu'une fois."""
    lieux, vus = [], set()
    for f in fiches:
        lien = lire_lien(f.get("lien"))
        ident = lien["place_id"] or (f"cid:{lien['cid']}" if lien["cid"] else None)
        if not f.get("nom") or lien["lat"] is None or lien["lon"] is None or not ident or ident in vus:
            continue
        vus.add(ident)
        lieux.append({
            "nom": f["nom"],
            "categorie": f.get("categorie") or None,
            "adresse": f.get("adresse") or None,
            "ville": None,   # Google ne la donne pas à part : la ville demandée sert à l'écriture
            "telephone": f.get("telephone") or None,
            "site": f.get("site") or None,
            "note": _note(f["note"]) if f.get("note") else None,
            "lat": lien["lat"],
            "lon": lien["lon"],
            "place_id": ident,
            "url": (f.get("lien") or "").split("?")[0],
        })
    return lieux


async def _passer_consentement(page):
    """Page « Avant d'accéder à Google » : refuse les cookies pour atteindre Maps."""
    if "consent.google" not in page.url:
        return
    bouton = page.get_by_role("button", name=re.compile("Tout refuser|Reject all", re.I)).first
    await bouton.click()
    await page.wait_for_url(re.compile(r"google\.[a-z.]+/maps"), timeout=DELAI_NAVIGATION_MS)


async def _lire_fiche(page, lien):
    await page.goto(lien, wait_until="domcontentloaded", timeout=DELAI_NAVIGATION_MS)
    await _passer_consentement(page)
    fiche = await page.evaluate(FICHE_JS)
    return {**fiche, "lien": page.url if "/maps/place/" in page.url else lien}


async def _une_requete(page, texte, maximum):
    url = f"https://www.google.com/maps/search/{quote_plus(texte)}?hl=fr"
    await page.goto(url, wait_until="domcontentloaded", timeout=DELAI_NAVIGATION_MS)
    await _passer_consentement(page)
    cartes = await page.evaluate(LISTE_JS.replace("__MAX__", str(int(maximum))))
    if isinstance(cartes, dict):
        if cartes.get("error") == "single_result":
            return [await _lire_fiche(page, page.url)]
        return []
    fiches = []
    for carte in cartes:
        # Une courte pause entre deux fiches : plus lent, mais moins vite repéré.
        await asyncio.sleep(random.uniform(0.5, 1.5))
        try:
            fiche = await _lire_fiche(page, carte["lien"])
        except Exception:  # noqa: BLE001 — une fiche illisible n'arrête pas la recherche
            fiche = {}
        fiches.append({**carte, **{k: v for k, v in fiche.items() if v}})
    return fiches


async def _rechercher(requetes, ville, maximum):
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise GoogleMapsIndisponible("le navigateur automatique (Playwright) n'est pas installé") from exc
    fiches = []
    async with async_playwright() as pw:
        # --no-sandbox : Chromium tourne en root dans le conteneur Docker.
        navigateur = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            contexte = await navigateur.new_context(locale="fr-FR", viewport={"width": 1280, "height": 900})
            page = await contexte.new_page()
            for requete in requetes:
                fiches += await _une_requete(page, f"{requete} {ville} Maroc", maximum)
        finally:
            await navigateur.close()
    return fiches


def rechercher(requetes, ville, maximum=MAX_PAR_REQUETE, extraire=None):
    """(lieux, coût) comme apify.rechercher ; le coût est toujours 0.
    `extraire` remplace le navigateur dans les tests."""
    fiches = (extraire or (lambda r, v, m: asyncio.run(_rechercher(r, v, m))))(requetes, ville, maximum)
    lieux = normaliser(fiches)
    if fiches and not lieux:
        raise GoogleMapsIndisponible(
            "aucune fiche lisible : Google a peut-être bloqué la recherche ou changé sa page"
        )
    return lieux, 0.0
