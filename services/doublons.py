"""Règle anti-doublon des entreprises, partagée par toutes les entrées en base :
le chatbot (agent_chatbot_workflow/outils/ecriture.py) et l'import Excel
(scripts/import_companies.py).

Une entreprise est un doublon d'une entreprise déjà en base si elle a :
- le même téléphone (« +212 5 22 21 88 09 » = « 0522218809 ») ;
- le même site web (hors réseaux sociaux et annuaires) ;
- ou un nom proche (sans « (Usine) », « SARL », accents ni ponctuation) à moins
  de 50 m.

Règle volontairement stricte, choisie par Netis : Google crée souvent deux
fiches pour la même société (IMCE n° 319 et n° 4637 au même endroit ; J.J.W et
JJWASHING, même téléphone), et une nouvelle succursale d'une chaîne déjà en
base (même site web) est écartée elle aussi.
"""
import re
import unicodedata
from urllib.parse import urlparse

# ~50 m en degrés : deux points plus proches sont « au même endroit ».
DUPLICATE_RADIUS_DEG = 0.00045

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
