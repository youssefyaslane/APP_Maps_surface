"""Outil 2 : compare les identifiants avec la base et écrit les seules
entreprises nouvelles.

Pour chaque lieu classé « entreprise » :
1. son place_id est-il déjà dans `companies` ? → « déjà en base » ;
2. une entreprise de la base a-t-elle le même téléphone, le même site web, ou
   un nom proche à moins de 50 m ? → « doublon », non écrit. Google crée
   souvent deux fiches pour la même société (IMCE n° 319 et n° 4637 au même
   endroit ; J.J.W et JJWASHING, même téléphone) ;
3. sinon, elle est écrite au format des imports actuels, avec une ligne
   `audit_log` dans la même transaction.

Les colonnes du toit et de la puissance restent vides : le script
scripts/compute_solar_potential.py les remplit ensuite, comme après un import.
"""
import re
import unicodedata
from urllib.parse import urlparse

from scripts.import_companies import DUPLICATE_RADIUS_DEG, load_known_cities, resolve_city
from services import db
from services.comptes import _log_audit

AJOUTEE, DEJA_EN_BASE, DOUBLON = "ajoutee", "deja_en_base", "doublon"
ACTION_AUDIT = "company_created_by_chatbot"


def _place_ids_existants(cur, place_ids):
    if not place_ids:
        return set()
    cur.execute("SELECT place_id FROM companies WHERE place_id = ANY(%s)", (list(place_ids),))
    return {r[0] for r in cur.fetchall()}


# Mots qui ne distinguent pas une société d'une autre : « Bardahl Maghreb
# (Usine) » et « BARDAHL MAGHREB SARL » doivent se reconnaître.
MOTS_GENERIQUES = {"usine", "sarl", "sarlau", "sa", "ste", "societe", "groupe", "group", "maroc", "morocco"}
# Sites partagés par des milliers de fiches : une page Facebook n'identifie
# pas une société par son seul domaine.
SITES_GENERIQUES = {
    "facebook.com", "m.facebook.com", "instagram.com", "linkedin.com", "wa.me", "api.whatsapp.com",
    "linktr.ee", "google.com", "sites.google.com", "business.site", "goo.gl", "maps.app.goo.gl",
    "youtube.com", "tiktok.com", "twitter.com", "x.com", "avito.ma", "jumia.ma",
    # Annuaires : une fiche y pointe pour des sociétés sans rapport entre elles.
    "kerix.net", "telecontact.ma", "charika.ma", "pagesjaunes.ma", "maroc-annuaire.com",
}


def nom_normalise(nom):
    texte = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode().lower()
    texte = re.sub(r"\([^)]*\)?", " ", texte)
    texte = re.sub(r"\bs\.?\s?a\.?\s?r\.?\s?l\.?", " ", texte)
    mots = re.sub(r"[^a-z0-9]+", " ", texte).split()
    utiles = [m for m in mots if m not in MOTS_GENERIQUES]
    return " ".join(utiles or mots)


def telephones(valeur):
    """Numéros réduits à leurs 9 derniers chiffres : « +212 5 22 21 88 09 »
    et « 0522218809 » donnent la même clé."""
    cles = set()
    for morceau in re.split(r"[/,;]", str(valeur or "")):
        chiffres = re.sub(r"\D", "", morceau)
        if len(chiffres) >= 9:
            cles.add(chiffres[-9:])
    return cles


def domaine(site):
    if not site:
        return None
    hote = urlparse(site if "//" in site else f"http://{site}").netloc.lower().split(":")[0]
    hote = hote[4:] if hote.startswith("www.") else hote
    return hote if hote and "." in hote and hote not in SITES_GENERIQUES else None


class IndexBase:
    """Les entreprises de la base, chargées une fois par recherche, et celles
    écrites pendant celle-ci : deux fiches de la même société dans une même
    recherche se reconnaissent aussi."""

    def __init__(self, cur):
        cur.execute("SELECT id, name, lat, lon, phone, website FROM companies")
        self.par_tel, self.par_site, self.lieux = {}, {}, []
        for ident, nom, lat, lon, tel, site in cur.fetchall():
            self.ajouter(ident, nom, lat, lon, tel, site)

    def ajouter(self, ident, nom, lat, lon, tel, site):
        fiche = (ident, nom)
        for cle in telephones(tel):
            self.par_tel.setdefault(cle, fiche)
        if domaine(site):
            self.par_site.setdefault(domaine(site), fiche)
        if lat is not None and lon is not None:
            self.lieux.append((nom_normalise(nom), float(lat), float(lon), fiche))

    def doublon(self, v):
        """Raison du doublon (texte), ou None si l'entreprise est nouvelle."""
        for cle in telephones(v["phone"]):
            if cle in self.par_tel:
                return "même téléphone que n° %s « %s »" % self.par_tel[cle]
        if domaine(v["website"]) in self.par_site:
            return "même site web que n° %s « %s »" % self.par_site[domaine(v["website"])]
        cle, r = nom_normalise(v["name"]), DUPLICATE_RADIUS_DEG
        for nom, lat, lon, fiche in self.lieux:
            if nom == cle and abs(lat - v["lat"]) <= r and abs(lon - v["lon"]) <= r:
                return "même nom, à moins de 50 m de n° %s « %s »" % fiche
        return None


def _texte(valeur):
    valeur = " ".join(str(valeur).split()) if valeur is not None else ""
    return valeur or None


def ligne_companies(lieu, ville_demandee, villes):
    """Lieu Apify classé → valeurs des colonnes de `companies`."""
    ville, _connue = resolve_city(lieu.get("ville") or ville_demandee, villes)
    note = lieu.get("note")
    return {
        "name": _texte(lieu["nom"]),
        "category": _texte(lieu.get("categorie")),
        "address": _texte(lieu.get("adresse")),
        "city": ville,
        "phone": _texte(lieu.get("telephone")),
        "website": _texte(lieu.get("site")),
        "rating": float(note) if isinstance(note, (int, float)) else None,
        "lon": float(lieu["lon"]),
        "lat": float(lieu["lat"]),
        "place_id": lieu["place_id"],
    }


def ecrire_nouvelles(lieux, requetes, ville, user_id=None, connecter=None):
    """Écrit les entreprises nouvelles. Renvoie {place_id: (statut, raison)}
    pour les lieux classés « entreprise » ; la raison dit, pour un doublon,
    à quelle entreprise il ressemble. `connecter` se remplace dans les tests."""
    entreprises = [l for l in lieux if l.get("classe") == "entreprise"]
    if not entreprises:
        return {}
    conn = (connecter or db.connect)()
    statuts = {}
    try:
        with conn, conn.cursor() as cur:
            villes = load_known_cities(cur)
            existants = _place_ids_existants(cur, [l["place_id"] for l in entreprises])
            index = IndexBase(cur)
            for lieu in entreprises:
                if lieu["place_id"] in existants:
                    statuts[lieu["place_id"]] = (DEJA_EN_BASE, "déjà en base")
                    continue
                v = ligne_companies(lieu, ville, villes)
                raison = index.doublon(v)
                if raison:
                    statuts[lieu["place_id"]] = (DOUBLON, raison)
                    continue
                cur.execute(
                    """
                    INSERT INTO companies (name, category, address, city, phone, website, rating, lon, lat, place_id)
                    VALUES (%(name)s, %(category)s, %(address)s, %(city)s, %(phone)s, %(website)s,
                            %(rating)s, %(lon)s, %(lat)s, %(place_id)s)
                    ON CONFLICT (place_id) DO NOTHING
                    RETURNING id
                    """,
                    v,
                )
                ligne = cur.fetchone()
                if ligne is None:   # écrite entre-temps par une autre recherche
                    statuts[lieu["place_id"]] = (DEJA_EN_BASE, "déjà en base")
                    continue
                index.ajouter(ligne[0], v["name"], v["lat"], v["lon"], v["phone"], v["website"])
                _log_audit(cur, user_id, ACTION_AUDIT, "companies", ligne[0], {
                    "place_id": lieu["place_id"],
                    "requetes": list(requetes),
                    "ville": ville,
                    "confiance": lieu.get("confiance"),
                    "raison": lieu.get("raison"),
                })
                statuts[lieu["place_id"]] = (AJOUTEE, lieu.get("raison"))
    finally:
        conn.close()
    return statuts
