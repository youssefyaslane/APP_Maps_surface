"""CRM des commerciaux (phase 7 de la roadmap) : suivi de chaque prospect.

Chaque commercial prend lui-même les prospects qu'il suit (« Prendre »),
premier arrivé, premier servi. Il ne voit et ne modifie ensuite que les
siens. Un admin ne prend rien et ne modifie aucun suivi : il consulte
l'avancement de toutes les opportunités, et peut seulement libérer un
prospect (un commercial parti ne doit pas garder les siens bloqués). Une
entreprise avance dans le pipeline :

    À contacter → Contacté → RDV fixé → Visite faite → Devis envoyé → Signé / Perdu

« Perdu » demande une raison ; « Déjà équipée » en est une, et reste liée au
bouton historique du même nom (equipped_at) : l'entreprise sort alors de la
liste des prospects. Les données vivent dans leurs propres tables (voir services/schema.py) :
opportunites, opportunite_etapes (historique du pipeline), decideurs, visites
et crm_notes ; companies ne décrit que l'entreprise. Chaque geste est aussi
noté dans audit_log, dans la même transaction que le changement qu'il décrit.

Après une visite, la consommation lue sur la facture remplace l'hypothèse des
économies « maximales » (100 % consommé sur place), et la vraie pente des
panneaux redemande à PVGIS le productible du lieu.
"""
import datetime
import json

from services import pvgis
from services.comptes import _log_audit
from services.etat_base import _get_db_pool
from services.solar import SOLAR_TARIF_DH_PER_KWH, co2_evite_t, economies_dh, production_mwh

STATUTS = {
    "a_contacter": "À contacter",
    "contacte": "Contacté",
    "rdv_fixe": "RDV fixé",
    "visite_faite": "Visite faite",
    "devis_envoye": "Devis envoyé",
    "signe": "Signé",
    "perdu": "Perdu",
}
RAISONS_PERTE = {
    "deja_equipee": "Déjà équipée",
    "pas_interesse": "Pas intéressée",
    "toiture_inadaptee": "Toiture inadaptée",
    "trop_cher": "Trop cher",
    "concurrent": "Partie chez un concurrent",
    "injoignable": "Injoignable",
    "autre": "Autre",
}
ETATS_TOITURE = {"bon": "Bon état", "a_renforcer": "À renforcer", "inutilisable": "Inutilisable"}
# Étapes closes : plus de relance à prévoir.
CLOS = ("signe", "perdu")

LIBELLES_HISTORIQUE = {
    "crm_pris": "a pris le prospect",
    "crm_libere": "a libéré le prospect",
    "crm_statut": "a changé l'étape",
    "crm_relance": "a planifié une relance",
    "crm_relance_annulee": "a annulé la relance",
    "crm_decideur": "a mis à jour le décideur",
    "crm_visite": "a enregistré la visite",
    "crm_note": "a ajouté une note",
    "company_created_by_chatbot": "a ajouté l'entreprise (chatbot)",
    "company_marked_equipped": "a marqué « déjà équipée »",
    "company_unmarked_equipped": "a retiré « déjà équipée »",
    "city_corrected": "a corrigé la ville",
}


class ErreurCRM(ValueError):
    """Action refusée ; le message est montré tel quel à l'utilisateur."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _connexion():
    pool = _get_db_pool()
    return pool, pool.getconn()


def _texte(valeur, longueur=200):
    valeur = " ".join(str(valeur).split()) if valeur is not None else ""
    return valeur[:longueur] or None


def _nombre(valeur, minimum=None, maximum=None, nom="valeur"):
    if valeur in (None, ""):
        return None
    try:
        n = float(str(valeur).replace(",", ".").replace(" ", ""))
    except ValueError:
        raise ErreurCRM(f"{nom} : nombre attendu.")
    if (minimum is not None and n < minimum) or (maximum is not None and n > maximum):
        raise ErreurCRM(f"{nom} : entre {minimum:g} et {maximum:g}.")
    return n


# Dernier décideur et dernière visite d'une entreprise : leurs tables en
# gardent plusieurs, la fiche montre le plus récent.
DERNIER_DECIDEUR = """
    LEFT JOIN LATERAL (
        SELECT nom, fonction, telephone, email FROM decideurs d
        WHERE d.company_id = c.id ORDER BY d.maj_le DESC, d.id DESC LIMIT 1
    ) dec ON true"""
DERNIERE_VISITE = """
    LEFT JOIN LATERAL (
        SELECT conso_kwh_an, etat_toiture, inclinaison, orientation, productible, le FROM visites v
        WHERE v.company_id = c.id ORDER BY v.le DESC, v.id DESC LIMIT 1
    ) vis ON true"""


def _verrouiller(cur, company_id):
    """L'entreprise et son opportunité, verrouillées jusqu'à la fin de la
    transaction : deux commerciaux qui prennent le même prospect au même
    instant ne peuvent pas l'obtenir tous les deux."""
    cur.execute("SELECT id, name, equipped_at FROM companies WHERE id = %s FOR UPDATE", (company_id,))
    entreprise = cur.fetchone()
    if entreprise is None:
        raise ErreurCRM("Entreprise introuvable.", 404)
    cur.execute(
        """
        SELECT id, commercial_id, statut, raison_perte, relance_le, relance_objet
        FROM opportunites WHERE company_id = %s FOR UPDATE
        """,
        (company_id,),
    )
    o = cur.fetchone() or (None, None, "a_contacter", None, None, None)
    return {"id": entreprise[0], "name": entreprise[1], "equipee": entreprise[2] is not None,
            "opp_id": o[0], "commercial_id": o[1], "statut": o[2], "raison": o[3],
            "relance_le": o[4], "relance_objet": o[5]}


def _etape(cur, opp_id, etape, raison, user_id, relance_le=None, relance_objet=None, commentaire=None):
    """Une ligne de l'historique du pipeline : l'étape, la prochaine action
    prévue à ce moment, et le commentaire du commercial."""
    cur.execute(
        """
        INSERT INTO opportunite_etapes (opportunite_id, etape, raison_perte, user_id, relance_le,
                                        relance_objet, commentaire)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (opp_id, etape, raison, user_id, relance_le, relance_objet, commentaire),
    )


def _attribuer(cur, ligne, user_id):
    """Donne l'opportunité à ce commercial ; la crée à la première prise. Une
    opportunité libérée garde ses notes, son décideur et son historique."""
    if ligne["opp_id"] is None:
        cur.execute(
            "INSERT INTO opportunites (company_id, commercial_id, pris_le) VALUES (%s, %s, now()) RETURNING id",
            (ligne["id"], user_id),
        )
        ligne["opp_id"] = cur.fetchone()[0]
        _etape(cur, ligne["opp_id"], "a_contacter", None, user_id)
    else:
        cur.execute(
            "UPDATE opportunites SET commercial_id = %s, pris_le = now(), maj_le = now() WHERE id = %s",
            (user_id, ligne["opp_id"]),
        )
    _log_audit(cur, user_id, "crm_pris", "companies", ligne["id"], {"name": ligne["name"]})
    ligne["commercial_id"] = user_id


def _maj(cur, ligne, champs):
    colonnes = ", ".join(f"{c} = %s" for c in champs)
    cur.execute(f"UPDATE opportunites SET {colonnes}, maj_le = now() WHERE id = %s",
                (*champs.values(), ligne["opp_id"]))


def _toucher(cur, ligne):
    """Dernière action sur l'opportunité (tri de la page de suivi)."""
    cur.execute("UPDATE opportunites SET maj_le = now() WHERE id = %s", (ligne["opp_id"],))


def _executer(company_id, action, user_id=None, is_admin=False, controler=True):
    """Exécute `action(cur, ligne)` dans une transaction, ligne verrouillée.

    `controler` : un commercial n'agit que sur ses prospects. Un prospect pris
    par un collègue est refusé ; un prospect libre devient le sien (agir sur
    un prospect, c'est le prendre). Un admin consulte sans modifier : refusé."""
    pool, conn = _connexion()
    try:
        with conn, conn.cursor() as cur:
            ligne = _verrouiller(cur, company_id)
            if controler and is_admin:
                raise ErreurCRM("Un admin consulte le suivi sans le modifier.", 403)
            if controler:
                if ligne["commercial_id"] not in (None, user_id):
                    raise ErreurCRM("Ce prospect est suivi par un autre commercial.", 403)
                if ligne["commercial_id"] is None:
                    _attribuer(cur, ligne, user_id)
            return action(cur, ligne)
    finally:
        pool.putconn(conn)


# ---------- actions ----------

def prendre(company_id, user_id, is_admin=False):
    if is_admin:
        raise ErreurCRM("Un admin ne prend pas de prospect : ce sont les commerciaux qui les suivent.", 403)

    def action(cur, ligne):
        if ligne["commercial_id"] == user_id:
            return
        if ligne["commercial_id"] is not None:
            raise ErreurCRM("Ce prospect est déjà suivi par un autre commercial.", 409)
        _attribuer(cur, ligne, user_id)
    _executer(company_id, action, controler=False)


def liberer(company_id, user_id, is_admin=False):
    def action(cur, ligne):
        if ligne["commercial_id"] is None:
            return
        if ligne["commercial_id"] != user_id and not is_admin:
            raise ErreurCRM("Seul le commercial qui suit ce prospect, ou un admin, peut le libérer.", 403)
        _maj(cur, ligne, {"commercial_id": None, "pris_le": None})
        _log_audit(cur, user_id, "crm_libere", "companies", company_id,
                   {"name": ligne["name"], "commercial_id": ligne["commercial_id"]})
    _executer(company_id, action, controler=False)


def mettre_a_jour_suivi(company_id, statut, raison, relance_le, relance_objet, user_id, is_admin=False,
                        commentaire=None):
    """Étape et prochaine action, enregistrées ensemble.

    La relance est celle du formulaire, telle quelle : une date la planifie,
    pas de date l'annule. Changer d'étape sans nouvelle date efface donc
    l'ancienne relance, qui concernait l'étape d'avant (« fixer un RDV » n'a
    plus lieu d'être une fois le RDV fixé). Pas de relance dans le passé, ni
    pour un prospect signé ou perdu. Chaque enregistrement qui change quelque
    chose — étape, prochaine action, ou commentaire — entre dans l'historique
    du pipeline (opportunite_etapes)."""
    if statut not in STATUTS:
        raise ErreurCRM("Étape inconnue.")
    if statut == "perdu" and raison not in RAISONS_PERTE:
        raise ErreurCRM("Indiquer pourquoi le prospect est perdu.")
    raison = raison if statut == "perdu" else None
    if relance_le:
        try:
            relance_le = datetime.date.fromisoformat(str(relance_le))
        except ValueError:
            raise ErreurCRM("Date de relance invalide.")
        if statut in CLOS:
            raise ErreurCRM("Prospect signé ou perdu : pas de relance à prévoir.")
        if relance_le < datetime.date.today():
            raise ErreurCRM("La relance doit être prévue aujourd'hui ou plus tard.")
    else:
        relance_le = None
    relance_objet = _texte(relance_objet) if relance_le else None
    commentaire = (commentaire or "").strip()[:2000] or None
    equipee = raison == "deja_equipee"

    def action(cur, ligne):
        # « Déjà équipée » reste le même constat que le bouton historique : il
        # retire l'entreprise de la liste, et l'y rend quand on change d'avis.
        if equipee and not ligne["equipee"]:
            cur.execute("UPDATE companies SET equipped_at = now(), equipped_by = %s WHERE id = %s",
                        (user_id, company_id))
            _log_audit(cur, user_id, "company_marked_equipped", "companies", company_id, {"name": ligne["name"]})
        elif not equipee and ligne["equipee"]:
            cur.execute("UPDATE companies SET equipped_at = NULL, equipped_by = NULL WHERE id = %s", (company_id,))
            _log_audit(cur, user_id, "company_unmarked_equipped", "companies", company_id, {"name": ligne["name"]})
        _maj(cur, ligne, {"statut": statut, "raison_perte": raison,
                          "relance_le": relance_le, "relance_objet": relance_objet})
        # L'historique et le journal ne gardent que ce qui a changé.
        etape_change = statut != ligne["statut"] or raison != ligne["raison"]
        relance_change = (relance_le, relance_objet) != (ligne["relance_le"], ligne["relance_objet"])
        if etape_change or relance_change or commentaire:
            _etape(cur, ligne["opp_id"], statut, raison, user_id, relance_le, relance_objet, commentaire)
        if etape_change:
            _log_audit(cur, user_id, "crm_statut", "companies", company_id, {
                "de": ligne["statut"], "vers": statut, "raison": raison,
            })
        if relance_le and (relance_le, relance_objet) != (ligne["relance_le"], ligne["relance_objet"]):
            _log_audit(cur, user_id, "crm_relance", "companies", company_id,
                       {"date": relance_le.isoformat(), "objet": relance_objet})
        elif not relance_le and ligne["relance_le"]:
            _log_audit(cur, user_id, "crm_relance_annulee", "companies", company_id, {})
    _executer(company_id, action, user_id, is_admin)


def enregistrer_decideur(company_id, donnees, user_id, is_admin=False):
    """Met à jour le contact principal de l'entreprise (le plus récent), ou le crée."""
    champs = {
        "nom": _texte(donnees.get("nom")),
        "fonction": _texte(donnees.get("fonction")),
        "telephone": _texte(donnees.get("telephone"), 40),
        "email": _texte(donnees.get("email"), 120),
    }
    if champs["email"] and "@" not in champs["email"]:
        raise ErreurCRM("Adresse e-mail invalide.")

    def action(cur, ligne):
        cur.execute(
            "SELECT id FROM decideurs WHERE company_id = %s ORDER BY maj_le DESC, id DESC LIMIT 1",
            (company_id,),
        )
        existant = cur.fetchone()
        if existant:
            cur.execute(
                "UPDATE decideurs SET nom = %s, fonction = %s, telephone = %s, email = %s, maj_le = now() "
                "WHERE id = %s",
                (*champs.values(), existant[0]),
            )
        else:
            cur.execute(
                "INSERT INTO decideurs (company_id, nom, fonction, telephone, email) VALUES (%s, %s, %s, %s, %s)",
                (company_id, *champs.values()),
            )
        _toucher(cur, ligne)
        _log_audit(cur, user_id, "crm_decideur", "companies", company_id, champs)
    _executer(company_id, action, user_id, is_admin)


def enregistrer_visite(company_id, donnees, user_id, is_admin=False, session_pvgis=None):
    """Ajoute une visite : chaque passage est gardé, la fiche montre le dernier."""
    conso = _nombre(donnees.get("conso_kwh_an"), 0, 1e9, "Consommation annuelle")
    etat = donnees.get("etat_toiture") or None
    if etat is not None and etat not in ETATS_TOITURE:
        raise ErreurCRM("État de la toiture inconnu.")
    inclinaison = _nombre(donnees.get("inclinaison"), 0, 90, "Inclinaison")
    orientation = _nombre(donnees.get("orientation"), -180, 180, "Orientation")

    def action(cur, ligne):
        productible = None
        if inclinaison is not None or orientation is not None:
            cur.execute("SELECT lat, lon FROM companies WHERE id = %s", (company_id,))
            lat, lon = cur.fetchone()
            try:
                productible = pvgis.productible(cur, lat, lon, session_pvgis, inclinaison, orientation)
            except pvgis.PvgisIndisponible:
                raise ErreurCRM("PVGIS injoignable : la pente n'a pas pu être prise en compte. Réessayer.", 503)
        cur.execute(
            """
            INSERT INTO visites (company_id, user_id, conso_kwh_an, etat_toiture, inclinaison,
                                 orientation, productible)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, user_id, conso, etat, inclinaison, orientation, productible),
        )
        _toucher(cur, ligne)
        _log_audit(cur, user_id, "crm_visite", "companies", company_id, {
            "conso_kwh_an": conso, "etat_toiture": etat,
            "inclinaison": inclinaison, "orientation": orientation, "productible": productible,
        })
    _executer(company_id, action, user_id, is_admin)


def ajouter_note(company_id, texte, user_id, is_admin=False):
    texte = (texte or "").strip()
    if not texte:
        raise ErreurCRM("Note vide.")
    if len(texte) > 5000:
        raise ErreurCRM("Note trop longue (5 000 caractères au plus).")

    def action(cur, ligne):
        cur.execute("INSERT INTO crm_notes (company_id, user_id, texte) VALUES (%s, %s, %s) RETURNING id",
                    (company_id, user_id, texte))
        note_id = cur.fetchone()[0]
        _toucher(cur, ligne)
        _log_audit(cur, user_id, "crm_note", "companies", company_id, {"note_id": note_id})
    _executer(company_id, action, user_id, is_admin)


# ---------- lecture ----------

def calculs(kwc, productible, productible_visite, conso_kwh_an, etat_toiture):
    """Potentiel de la fiche. Les relevés de visite priment sur les
    hypothèses : vraie pente (productible) et vraie consommation (économies
    réelles au lieu des économies maximales)."""
    retenu = productible_visite or productible
    production = production_mwh(kwc, retenu)
    resultat = {
        "productible": retenu,
        "productible_source": "visite" if productible_visite else "hypothese",
        "production_mwh": production,
        "co2_t": co2_evite_t(production),
        "economies_max_dh": economies_dh(production),
        "economies_reelles_dh": None,
        "part_consommee": None,
        "couverture_conso": None,
        "toiture_inutilisable": etat_toiture == "inutilisable",
    }
    if production and conso_kwh_an:
        production_kwh = production * 1000
        consommee = min(production_kwh, conso_kwh_an)
        resultat.update({
            "economies_reelles_dh": round(consommee * SOLAR_TARIF_DH_PER_KWH),
            # Part de la production utilisée sur place, et part de la facture couverte.
            "part_consommee": round(consommee / production_kwh, 3),
            "couverture_conso": round(min(production_kwh / conso_kwh_an, 1), 3),
        })
    return resultat


def _historique(details, action):
    d = details or {}
    if action == "crm_statut":
        texte = f"{STATUTS.get(d.get('de'), d.get('de'))} → {STATUTS.get(d.get('vers'), d.get('vers'))}"
        if d.get("raison"):
            texte += f" ({RAISONS_PERTE.get(d['raison'], d['raison'])})"
        return texte
    if action == "crm_relance":
        return f"le {d.get('date')}" + (f" : {d['objet']}" if d.get("objet") else "")
    if action == "crm_visite":
        morceaux = []
        if d.get("conso_kwh_an"):
            morceaux.append(f"consommation {d['conso_kwh_an']:,.0f} kWh/an".replace(",", "\u202f"))
        if d.get("etat_toiture"):
            morceaux.append(f"toiture : {ETATS_TOITURE.get(d['etat_toiture'], d['etat_toiture'])}")
        if d.get("inclinaison") is not None:
            morceaux.append(f"pente {d['inclinaison']:g}°")
        return ", ".join(morceaux)
    return ""


def parcours(etapes_vues, statut, pris_le):
    """Historique du pipeline (opportunite_etapes). `etapes_vues` : (étape,
    date, auteur, raison, relance_le, relance_objet, commentaire), de la plus
    ancienne à la plus récente.

    - `etapes` : pour chaque étape, quand elle a été atteinte (la dernière
      fois) et par qui. Une nouvelle relance ou un commentaire dans la même
      étape ne change pas cette date : seul le passage d'une étape à une
      autre compte ;
    - `etape_depuis` : depuis quand le prospect est dans son étape actuelle ;
    - `lignes` : tout l'historique, du plus récent au plus ancien."""
    etapes, precedente, lignes = {}, None, []
    for etape, le, auteur, raison, relance_le, relance_objet, commentaire in etapes_vues:
        if etape != precedente:
            etapes[etape] = {"le": le.isoformat(), "auteur": auteur}
            precedente = etape
        lignes.append({
            "etape": etape, "le": le.isoformat(), "auteur": auteur, "raison_perte": raison,
            "relance": {"le": relance_le.isoformat(), "objet": relance_objet} if relance_le else None,
            "commentaire": commentaire, "changement": len(lignes) == 0 or lignes[-1]["etape"] != etape,
        })
    depuis = etapes.get(statut, {}).get("le") or (pris_le.isoformat() if pris_le else None)
    return {"etapes": etapes, "etape_depuis": depuis, "lignes": lignes[::-1]}


def fiche(company_id, user_id=None, is_admin=True):
    """Fiche de suivi, ou None si l'entreprise n'existe pas. Un commercial ne
    lit que les siennes, et celles que personne n'a prises (ErreurCRM 403)."""
    pool, conn = _connexion()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id, c.name, c.category, c.address, c.city, c.phone, c.email, c.website,
                       c.lat, c.lon, c.roof_area_m2, c.solar_panels, c.solar_kwc, c.solar_yield_kwh_kwc,
                       COALESCE(o.statut, 'a_contacter'), o.raison_perte, o.commercial_id,
                       u.display_name, u.username, o.pris_le, o.relance_le, o.relance_objet, o.maj_le,
                       dec.nom, dec.fonction, dec.telephone, dec.email,
                       vis.conso_kwh_an, vis.etat_toiture, vis.inclinaison, vis.orientation,
                       vis.productible, c.equipped_at, o.id, vis.le
                FROM companies c
                LEFT JOIN opportunites o ON o.company_id = c.id
                LEFT JOIN users u ON u.id = o.commercial_id
                {DERNIER_DECIDEUR}
                {DERNIERE_VISITE}
                WHERE c.id = %s
                """,
                (company_id,),
            )
            r = cur.fetchone()
            if r is None:
                return None
            if not is_admin and r[16] not in (None, user_id):
                raise ErreurCRM("Ce prospect est suivi par un autre commercial.", 403)
            cur.execute(
                """
                SELECT n.id, n.texte, n.cree_le, COALESCE(u.display_name, u.username)
                FROM crm_notes n LEFT JOIN users u ON u.id = n.user_id
                WHERE n.company_id = %s ORDER BY n.cree_le DESC, n.id DESC
                """,
                (company_id,),
            )
            notes = [{"id": n[0], "texte": n[1], "le": n[2].isoformat(), "auteur": n[3]} for n in cur.fetchall()]
            etapes_vues = []
            if r[33] is not None:
                cur.execute(
                    """
                    SELECT e.etape, e.le, COALESCE(u.display_name, u.username), e.raison_perte,
                           e.relance_le, e.relance_objet, e.commentaire
                    FROM opportunite_etapes e LEFT JOIN users u ON u.id = e.user_id
                    WHERE e.opportunite_id = %s ORDER BY e.le, e.id
                    """,
                    (r[33],),
                )
                etapes_vues = cur.fetchall()
            cur.execute(
                """
                SELECT a.action, a.details, a.created_at, COALESCE(u.display_name, u.username)
                FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
                WHERE a.entity = 'companies' AND a.entity_id = %s
                ORDER BY a.created_at DESC, a.id DESC LIMIT 100
                """,
                (company_id,),
            )
            historique = [
                {"action": h[0], "libelle": LIBELLES_HISTORIQUE.get(h[0], h[0]),
                 "detail": _historique(h[1] if isinstance(h[1], dict) else json.loads(h[1] or "{}"), h[0]),
                 "le": h[2].isoformat(), "auteur": h[3]}
                for h in cur.fetchall()
            ]
    finally:
        pool.putconn(conn)
    return {
        "id": r[0], "name": r[1], "category": r[2], "address": r[3], "city": r[4],
        "phone": r[5], "email": r[6], "website": r[7], "lat": r[8], "lon": r[9],
        "roof_area_m2": r[10], "solar_panels": r[11], "solar_kwc": r[12],
        "statut": r[14], "raison_perte": r[15],
        "commercial": {"id": r[16], "nom": r[17] or r[18]} if r[16] else None,
        "pris_le": r[19].isoformat() if r[19] else None,
        "relance": {"le": r[20].isoformat(), "objet": r[21]} if r[20] else None,
        "maj_le": r[22].isoformat() if r[22] else None,
        "decideur": {"nom": r[23], "fonction": r[24], "telephone": r[25], "email": r[26]},
        "visite": {"conso_kwh_an": r[27], "etat_toiture": r[28], "inclinaison": r[29], "orientation": r[30],
                   "le": r[34].isoformat() if r[34] else None},
        "calculs": calculs(r[12], r[13], r[31], r[27], r[28]),
        "equipee": r[32] is not None,
        "notes": notes,
        "parcours": parcours(etapes_vues, r[14], r[19]),
        "historique": historique,
    }


def commerciaux():
    pool, conn = _connexion()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id, COALESCE(display_name, username) FROM users ORDER BY 2")
            return [{"id": i, "nom": n} for i, n in cur.fetchall()]
    finally:
        pool.putconn(conn)


def relances_dues(user_id):
    """Relances du jour et en retard de ce commercial, la plus ancienne d'abord."""
    pool, conn = _connexion()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.name, c.city, o.relance_le, o.relance_objet, o.statut
                FROM opportunites o JOIN companies c ON c.id = o.company_id
                WHERE o.commercial_id = %s AND o.relance_le <= current_date
                  AND o.statut NOT IN ('signe', 'perdu')
                ORDER BY o.relance_le, c.name
                """,
                (user_id,),
            )
            return [{"id": r[0], "name": r[1], "city": r[2], "le": r[3].isoformat(), "objet": r[4],
                     "statut": r[5]} for r in cur.fetchall()]
    finally:
        pool.putconn(conn)


def opportunites(user_id, commercial="moi", statut=None, relances=False, search=None, is_admin=False):
    """Opportunités prises par les commerciaux, pour la page de suivi.

    `commercial` : « moi » (par défaut), « tous » (toutes les opportunités
    prises), ou l'identifiant d'un compte. Les compteurs par étape et le
    nombre de relances dues portent sur ce périmètre, sans le filtre d'étape :
    ils servent à choisir l'étape à afficher."""
    # Un commercial ne voit que ses opportunités, quoi qu'il demande.
    if not is_admin:
        commercial = "moi"
    perimetre, params = ["o.commercial_id IS NOT NULL"], []
    if commercial == "moi":
        perimetre.append("o.commercial_id = %s")
        params.append(user_id)
    elif commercial and str(commercial).isdigit():
        perimetre.append("o.commercial_id = %s")
        params.append(int(commercial))
    if search:
        perimetre.append("(c.name ILIKE %s OR c.city ILIKE %s OR dec.nom ILIKE %s)")
        params.extend([f"%{search}%"] * 3)
    filtres, fparams = list(perimetre), list(params)
    if statut == "en_cours":
        filtres.append("o.statut NOT IN ('signe', 'perdu')")
    elif statut in STATUTS:
        filtres.append("o.statut = %s")
        fparams.append(statut)
    if relances:
        filtres.append("o.relance_le <= current_date")
        filtres.append("o.statut NOT IN ('signe', 'perdu')")

    jointures = f"""
        FROM opportunites o
        JOIN companies c ON c.id = o.company_id
        LEFT JOIN users u ON u.id = o.commercial_id
        {DERNIER_DECIDEUR}
        {DERNIERE_VISITE}"""
    pool, conn = _connexion()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT o.statut, count(*),
                       count(*) FILTER (WHERE o.relance_le <= current_date
                                        AND o.statut NOT IN ('signe', 'perdu'))
                {jointures}
                WHERE {" AND ".join(perimetre)} GROUP BY 1
                """,
                params,
            )
            compteurs, dues = {}, 0
            for etape, n, d in cur.fetchall():
                compteurs[etape] = n
                dues += d
            cur.execute(
                f"""
                SELECT c.id, c.name, c.city, c.category, c.phone,
                       o.statut, o.raison_perte, o.commercial_id, COALESCE(u.display_name, u.username),
                       o.relance_le, o.relance_objet, o.maj_le, o.pris_le,
                       c.solar_kwc, c.solar_yield_kwh_kwc, vis.productible,
                       vis.conso_kwh_an, vis.etat_toiture,
                       dec.nom, dec.fonction, dec.telephone,
                       -- Entrée dans l'étape actuelle : premier passage après la dernière
                       -- ligne d'une autre étape (une relance ou un commentaire ne compte pas).
                       (SELECT min(e.le) FROM opportunite_etapes e
                        WHERE e.opportunite_id = o.id AND e.etape = o.statut
                          AND e.le > COALESCE((SELECT max(e2.le) FROM opportunite_etapes e2
                                               WHERE e2.opportunite_id = o.id AND e2.etape <> o.statut),
                                              '-infinity'))
                {jointures}
                WHERE {" AND ".join(filtres)}
                ORDER BY o.relance_le NULLS LAST, o.maj_le DESC, c.name
                LIMIT 500
                """,
                fparams,
            )
            lignes = cur.fetchall()
    finally:
        pool.putconn(conn)
    aujourdhui = datetime.date.today()
    resultat = []
    for r in lignes:
        resultat.append({
            "id": r[0], "name": r[1], "city": r[2], "category": r[3], "phone": r[4],
            "statut": r[5], "raison_perte": r[6],
            "commercial": {"id": r[7], "nom": r[8]},
            "relance": {"le": r[9].isoformat(), "objet": r[10], "due": r[9] <= aujourdhui} if r[9] else None,
            "maj_le": r[11].isoformat() if r[11] else None,
            "pris_le": r[12].isoformat() if r[12] else None,
            "solar_kwc": r[13],
            "calculs": calculs(r[13], r[14], r[15], r[16], r[17]),
            "decideur": {"nom": r[18], "fonction": r[19], "telephone": r[20]},
            # Entrée dans l'étape actuelle (historique du pipeline), sinon la prise.
            "etape_depuis": (r[21] or r[12]).isoformat() if (r[21] or r[12]) else None,
        })
    return {"opportunites": resultat, "compteurs": compteurs, "relances_dues": dues,
            "total": sum(compteurs.values())}


def references():
    """Libellés des listes déroulantes de la fiche. `ordres` donne l'ordre du
    parcours de vente : le JSON renvoyé trie les clés par ordre alphabétique,
    qui mettrait « Devis envoyé » avant « RDV fixé »."""
    return {
        "statuts": STATUTS, "raisons_perte": RAISONS_PERTE, "etats_toiture": ETATS_TOITURE,
        "ordres": {"statuts": list(STATUTS), "raisons_perte": list(RAISONS_PERTE),
                   "etats_toiture": list(ETATS_TOITURE)},
    }
