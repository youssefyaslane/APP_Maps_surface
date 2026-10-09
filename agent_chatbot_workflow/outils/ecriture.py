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
from scripts.import_companies import load_known_cities, resolve_city
from services import db
from services.comptes import _log_audit
# Règle anti-doublon partagée avec l'import Excel (services/doublons.py) ;
# reprise ici sous les mêmes noms, que les tests et le reste du chatbot utilisent.
from services.doublons import IndexBase, domaine, nom_normalise, telephones  # noqa: F401

AJOUTEE, DEJA_EN_BASE, DOUBLON = "ajoutee", "deja_en_base", "doublon"
ACTION_AUDIT = "company_created_by_chatbot"


def _place_ids_existants(cur, place_ids):
    if not place_ids:
        return set()
    cur.execute("SELECT place_id FROM companies WHERE place_id = ANY(%s)", (list(place_ids),))
    return {r[0] for r in cur.fetchall()}


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
