"""Génère docs/Schema_base_de_donnees.pdf : le schéma de la base PostgreSQL
(schéma « solar intelligence ») et le rôle de chaque table et de chaque colonne.

    python3 scripts/pdf_schema_base.py

Se lance hors du conteneur : il demande reportlab et la police DejaVu Sans
(/usr/share/fonts/truetype/dejavu). Les tables et colonnes décrites ici sont
celles de services/schema.py au 9 octobre 2026 ; les nombres de lignes, ceux
de la base à cette date. À mettre à jour quand le schéma change.
"""
import os

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SORTIE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "docs", "Schema_base_de_donnees.pdf")
pdfmetrics.registerFont(TTFont("DV", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB", italic="DV", boldItalic="DVB")

VERT_FONCE, VERT, ENCRE, DOUX, TRAIT = (colors.HexColor(c) for c in
                                        ("#1f5130", "#2e7d32", "#0f1b20", "#546e7a", "#cfd8dc"))
# Une couleur par famille de tables, reprise dans le schéma et les titres.
FAMILLES = {
    "comptes": ("Comptes et journal", colors.HexColor("#455a64")),
    "toits": ("Bâtiments et toits", colors.HexColor("#1565c0")),
    "entreprises": ("Entreprises et potentiel solaire", colors.HexColor("#2e7d32")),
    "crm": ("Suivi commercial (CRM)", colors.HexColor("#8e24aa")),
}

st = {
    "titre": ParagraphStyle("titre", fontName="DVB", fontSize=21, leading=26, textColor=ENCRE),
    "sous": ParagraphStyle("sous", fontName="DV", fontSize=10.5, leading=15, textColor=DOUX),
    "h1": ParagraphStyle("h1", fontName="DVB", fontSize=14.5, leading=19, textColor=VERT_FONCE, spaceBefore=12, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName="DVB", fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=3),
    "p": ParagraphStyle("p", fontName="DV", fontSize=9.6, leading=13.6, textColor=ENCRE, spaceAfter=5),
    "petit": ParagraphStyle("petit", fontName="DV", fontSize=8.3, leading=11.3, textColor=DOUX, spaceAfter=4),
    "cell": ParagraphStyle("cell", fontName="DV", fontSize=8.4, leading=11, textColor=ENCRE),
    "code": ParagraphStyle("code", fontName="DVB", fontSize=8.4, leading=11, textColor=ENCRE),
    "entete": ParagraphStyle("entete", fontName="DVB", fontSize=8.4, leading=11, textColor=colors.white),
}


def P(texte, style="p"):
    return Paragraph(texte, st[style])


# ---------- tables : famille, nombre de lignes, rôle, colonnes ----------
# Colonne : (nom, type, obligatoire, rôle). « clé » : clé primaire ; « → table » : clé étrangère.
TABLES = [
    ("users", "comptes", 3,
     "Les comptes des personnes qui utilisent l'outil (commerciaux et administrateurs). Pas "
     "d'inscription en ligne : un admin crée les comptes.",
     [("id", "entier", "oui", "<b>Clé</b>. Numéro du compte, repris partout où l'on note qui a fait quoi."),
      ("username", "texte", "oui", "Identifiant de connexion, unique."),
      ("password_hash", "texte", "oui", "Mot de passe <b>chiffré</b> (jamais stocké en clair)."),
      ("display_name", "texte", "non", "Nom affiché dans l'interface (suivi, historique, notes)."),
      ("created_at", "date et heure", "oui", "Création du compte."),
      ("is_admin", "oui / non", "oui", "Administrateur : gère les comptes et la base, consulte tout le suivi "
                                      "commercial (sans le modifier).")]),
    ("audit_log", "comptes", 202,
     "Le journal de l'application : chaque action importante (toit ajouté ou supprimé, entreprise "
     "ajoutée par le chatbot, prospect pris, étape changée…), avec qui et quand. La fiche d'une "
     "entreprise en tire son « Historique ».",
     [("id", "entier", "oui", "<b>Clé</b>. Numéro de l'action."),
      ("user_id", "entier", "non", "<b>→ users</b>. Qui a agi ; vide pour une action du système ou d'un compte supprimé."),
      ("action", "texte", "oui", "Code de l'action : crm_pris, crm_statut, company_deleted, "
                                 "company_created_by_chatbot, company_marked_equipped…"),
      ("entity", "texte", "oui", "Table concernée (companies, ia_segments…)."),
      ("entity_id", "entier", "non", "Numéro de la ligne concernée. Volontairement sans lien forcé : "
                                     "la trace reste après la suppression de la ligne."),
      ("details", "JSON", "non", "Détails de l'action : étape de départ et d'arrivée, ancienne ligne "
                                 "supprimée, requête du chatbot…"),
      ("created_at", "date et heure", "oui", "Quand.")]),
    ("osm_buildings", "toits", 95006,
     "Les bâtiments d'OpenStreetMap importés pour la région (extrait Geofabrik). Première source "
     "de toit : contours tracés par des humains, les plus fiables.",
     [("osm_id", "grand entier", "oui", "<b>Clé</b>. Identifiant du bâtiment dans OpenStreetMap."),
      ("polygon", "JSON", "oui", "Contour du bâtiment : liste de points [longitude, latitude]."),
      ("holes", "JSON", "non", "Trous du contour (cours intérieures), retirés de la surface."),
      ("area_m2", "nombre", "oui", "Surface au sol calculée (m²)."),
      ("centroid_lon / centroid_lat", "nombre", "oui", "Centre du bâtiment : recherche rapide par zone."),
      ("name", "texte", "non", "Nom du bâtiment dans OpenStreetMap, s'il en a un."),
      ("building_type", "texte", "non", "Type (industrial, warehouse, commercial…)."),
      ("levels", "texte", "non", "Nombre d'étages, s'il est renseigné."),
      ("imported_at", "date et heure", "oui", "Date de l'import.")]),
    ("ms_buildings", "toits", 193253,
     "Les bâtiments détectés par IA par Microsoft (Global ML Building Footprints). Troisième source "
     "de toit, là où OpenStreetMap n'en a pas.",
     [("id", "entier", "oui", "<b>Clé</b>. Numéro du bâtiment."),
      ("polygon", "JSON", "oui", "Contour : liste de points [longitude, latitude]."),
      ("area_m2", "nombre", "oui", "Surface au sol (m²)."),
      ("centroid_lon / centroid_lat", "nombre", "oui", "Centre du bâtiment."),
      ("geom_hash", "texte", "non", "Empreinte du contour (calculée, unique) : un même bâtiment n'est "
                                    "pas importé deux fois.")]),
    ("ia_segments", "toits", 999,
     "Les toits détectés par l'IA de l'application (MobileSAM, au clic ou par zone) ou tracés à la "
     "main sur la carte. Deuxième source de toit.",
     [("id", "entier", "oui", "<b>Clé</b>. Numéro du toit."),
      ("polygon", "JSON", "oui", "Contour : liste de points [longitude, latitude]."),
      ("area_m2", "nombre", "oui", "Surface (m²)."),
      ("centroid_lon / centroid_lat", "nombre", "oui", "Centre du toit."),
      ("created_at", "date et heure", "oui", "Création."),
      ("source", "texte", "oui", "« ia-segmentation » (détecté) ou « manual-trace » (tracé à la main)."),
      ("created_by", "entier", "non", "<b>→ users</b>. Qui l'a détecté ou tracé.")]),
    ("pv_detections", "toits", 1707,
     "La détection automatique des panneaux solaires déjà posés sur un toit (modèle YOLO sur "
     "l'image satellite). Indique au tableau de bord « Déjà équipé ? ».",
     [("roof_key", "texte", "oui", "<b>Clé</b>. Le toit analysé, même valeur que companies.roof_key."),
      ("has_image", "oui / non", "oui", "Une image satellite assez précise existait-elle ?"),
      ("scores", "liste de nombres", "oui", "Confiance du modèle pour chaque panneau détecté."),
      ("max_score", "nombre", "oui", "Meilleure confiance."),
      ("model", "texte", "oui", "Modèle utilisé pour l'analyse."),
      ("detected_at", "date et heure", "oui", "Date de l'analyse."),
      ("dark_score", "nombre", "oui", "Part de pixels sombres des panneaux détectés : « Oui » au "
                                      "tableau de bord à partir du seuil PV_SEUIL (0,25).")]),
    ("companies", "entreprises", 2271,
     "Les entreprises (prospects), avec le toit trouvé sous leur position et leur potentiel solaire. "
     "Ne contient que ce qui décrit l'entreprise : le suivi commercial est dans les tables du CRM.",
     [("id", "entier", "oui", "<b>Clé</b>. Numéro de l'entreprise."),
      ("name", "texte", "oui", "Nom (Google Maps)."),
      ("category", "texte", "non", "Catégorie Google (Fabricant, Entrepôt…)."),
      ("address", "texte", "non", "Adresse."),
      ("city", "texte", "non", "Ville, rapprochée de l'orthographe des villes déjà en base."),
      ("phone / email / website", "texte", "non", "Coordonnées. Le téléphone et le site servent aussi "
                                                  "à reconnaître les doublons."),
      ("rating", "nombre", "non", "Note Google (conservée, plus affichée)."),
      ("lon / lat", "nombre", "oui", "Position Google : sert à trouver le toit sous l'entreprise."),
      ("place_id", "texte", "non", "Identifiant Google de la fiche, <b>unique</b> : la base refuse "
                                   "d'enregistrer deux fois la même fiche."),
      ("created_at", "date et heure", "oui", "Ajout en base."),
      ("roof_area_m2", "nombre", "non", "Surface du toit trouvé (m²) ; vide si aucun toit."),
      ("roof_source", "texte", "non", "Source du toit : osm, ia-segmentation, manual-trace, ms-buildings."),
      ("roof_key", "texte", "non", "Le toit : « osm:&lt;n°&gt; », « ms:&lt;n°&gt; » ou « ia:&lt;n°&gt; ». Repère "
                                   "les entreprises qui partagent un toit."),
      ("solar_panels", "entier", "non", "Panneaux installables (toit × 70 % ÷ 2 m²)."),
      ("solar_kwc", "nombre", "non", "Puissance installable (panneaux × 0,5 kWc)."),
      ("solar_yield_kwh_kwc", "nombre", "non", "Productible PVGIS du lieu (kWh par kWc et par an) : "
                                               "production = kWc × productible."),
      ("solar_computed_at", "date et heure", "non", "Date du calcul ; vide = à calculer."),
      ("equipped_at / equipped_by", "date / entier", "non", "Déjà équipée de panneaux, et par qui "
                                                            "(<b>→ users</b>) : retirée de la liste des prospects.")]),
    ("pvgis_cache", "entreprises", 28,
     "Les réponses de PVGIS (Commission européenne), une par zone de 0,1° (~10 km) et par réglage "
     "de pose : PVGIS n'est interrogé qu'une fois par zone.",
     [("case_lat / case_lon", "nombre", "oui", "<b>Clé</b> (avec les 4 suivantes). Zone de 0,1°."),
      ("inclinaison", "nombre", "oui", "Pente des panneaux (0° = à plat)."),
      ("orientation", "nombre", "oui", "Orientation (0 = sud, -90 = est, 90 = ouest)."),
      ("pertes", "nombre", "oui", "Pertes de l'installation (%)."),
      ("montage", "texte", "oui", "« building » (intégré) ou « free » (support libre)."),
      ("productible", "nombre", "oui", "kWh produits par kWc et par an."),
      ("mensuel", "JSON", "oui", "Production de chaque mois (12 valeurs)."),
      ("ensoleillement", "nombre", "non", "Ensoleillement reçu (kWh/m²/an)."),
      ("demande_le", "date et heure", "oui", "Date de la réponse.")]),
    ("opportunites", "crm", 3,
     "Le suivi commercial : une ligne par entreprise prise par un commercial. Libérée, elle reste "
     "(sans commercial) avec son historique, ses notes et son décideur.",
     [("id", "entier", "oui", "<b>Clé</b>. Numéro de l'opportunité."),
      ("company_id", "entier", "oui", "<b>→ companies</b>, <b>unique</b> : une opportunité par entreprise."),
      ("commercial_id", "entier", "non", "<b>→ users</b>. Le commercial qui la suit ; vide = libre."),
      ("statut", "texte", "oui", "Étape : a_contacter, contacte, rdv_fixe, visite_faite, devis_envoye, "
                                 "signe, perdu."),
      ("raison_perte", "texte", "non", "Si perdu : deja_equipee, pas_interesse, toiture_inadaptee, "
                                       "trop_cher, concurrent, injoignable, autre."),
      ("relance_le / relance_objet", "date / texte", "non", "Prochaine action prévue (« Mes relances »)."),
      ("pris_le", "date et heure", "non", "Prise par le commercial actuel."),
      ("cree_le / maj_le", "date et heure", "oui", "Création, et dernière action (tri de la page de suivi).")]),
    ("opportunite_etapes", "crm", 9,
     "L'historique du pipeline : une ligne à chaque enregistrement du suivi qui change quelque "
     "chose (étape, prochaine action ou commentaire). Donne les dates affichées sous les étapes de "
     "la fiche, le temps passé dans l'étape actuelle, et l'« Historique du pipeline » de la fiche.",
     [("id", "entier", "oui", "<b>Clé</b>."),
      ("opportunite_id", "entier", "oui", "<b>→ opportunites</b>."),
      ("etape", "texte", "oui", "Étape à ce moment. La date d'entrée dans une étape est celle de "
                               "sa première ligne : une relance ou un commentaire ensuite ne la change pas."),
      ("raison_perte", "texte", "non", "Raison, si l'étape est « perdu »."),
      ("relance_le / relance_objet", "date / texte", "non", "Prochaine action prévue à ce moment (date et objet)."),
      ("commentaire", "texte", "non", "Commentaire du commercial : ce qui s'est passé, ce qui a été convenu."),
      ("user_id", "entier", "non", "<b>→ users</b>. Qui a enregistré."),
      ("le", "date et heure", "oui", "Quand.")]),
    ("decideurs", "crm", 0,
     "Les contacts de l'entreprise (la personne qui décide). Plusieurs possibles ; la fiche montre "
     "le plus récent.",
     [("id", "entier", "oui", "<b>Clé</b>."),
      ("company_id", "entier", "oui", "<b>→ companies</b>."),
      ("nom / fonction", "texte", "non", "Nom et fonction (directeur, DAF…)."),
      ("telephone / email", "texte", "non", "Coordonnées directes."),
      ("cree_le / maj_le", "date et heure", "oui", "Création et dernière mise à jour.")]),
    ("visites", "crm", 0,
     "Chaque visite sur place : ce que le commercial ou le technicien a constaté. La dernière "
     "remplace les hypothèses dans les calculs de la fiche.",
     [("id", "entier", "oui", "<b>Clé</b>."),
      ("company_id", "entier", "oui", "<b>→ companies</b>."),
      ("user_id", "entier", "non", "<b>→ users</b>. Qui a fait la visite."),
      ("le", "date et heure", "oui", "Date de la visite."),
      ("conso_kwh_an", "nombre", "non", "Consommation annuelle lue sur la facture : donne les "
                                        "économies <b>réelles</b> au lieu des économies maximales."),
      ("etat_toiture", "texte", "non", "bon, a_renforcer ou inutilisable."),
      ("inclinaison / orientation", "nombre", "non", "Vraie pente et orientation des panneaux."),
      ("productible", "nombre", "non", "Productible PVGIS recalculé pour cette pente et cette orientation.")]),
    ("crm_notes", "crm", 0,
     "Les notes des commerciaux : appels, rendez-vous, échanges.",
     [("id", "entier", "oui", "<b>Clé</b>."),
      ("company_id", "entier", "oui", "<b>→ companies</b>."),
      ("user_id", "entier", "non", "<b>→ users</b>. Auteur."),
      ("texte", "texte", "oui", "Contenu de la note."),
      ("cree_le", "date et heure", "oui", "Date.")]),
]


def schema():
    """Schéma d'ensemble : les tables par famille, et leurs liens."""
    largeur, hauteur = 170 * mm, 182 * mm
    d = Drawing(largeur, hauteur)
    W, H = 46 * mm, 11 * mm
    cols = {"toits": 2 * mm, "entreprises": 62 * mm, "crm": 122 * mm}
    pos = {}

    def boite(nom, famille, x, y, lignes):
        couleur = FAMILLES[famille][1]
        d.add(Rect(x, y, W, H, rx=2.2 * mm, ry=2.2 * mm, fillColor=colors.white, strokeColor=couleur, strokeWidth=1.2))
        d.add(Rect(x, y + H - 1.6 * mm, W, 1.6 * mm, fillColor=couleur, strokeColor=couleur, strokeWidth=0))
        d.add(String(x + 3 * mm, y + 5.4 * mm, nom, fontName="DVB", fontSize=8.6, fillColor=ENCRE))
        d.add(String(x + 3 * mm, y + 2 * mm, f"{lignes:,} lignes".replace(",", " "), fontName="DV", fontSize=6.8, fillColor=DOUX))
        pos[nom] = (x, y)

    def titre_colonne(famille, x):
        libelle, couleur = FAMILLES[famille]
        d.add(String(x, hauteur - 6 * mm, libelle, fontName="DVB", fontSize=8.4, fillColor=couleur))

    for famille, x in cols.items():
        titre_colonne(famille, x)
    lignes = {t[0]: t[2] for t in TABLES}
    y0 = hauteur - 22 * mm
    for i, nom in enumerate(["osm_buildings", "ms_buildings", "ia_segments", "pv_detections"]):
        boite(nom, "toits", cols["toits"], y0 - i * 22 * mm, lignes[nom])
    boite("companies", "entreprises", cols["entreprises"], y0 - 22 * mm, lignes["companies"])
    boite("pvgis_cache", "entreprises", cols["entreprises"], y0 - 66 * mm, lignes["pvgis_cache"])
    for i, nom in enumerate(["opportunites", "opportunite_etapes", "decideurs", "visites", "crm_notes"]):
        boite(nom, "crm", cols["crm"], y0 - i * 22 * mm, lignes[nom])
    titre_colonne("comptes", cols["entreprises"])
    d.contents[-1].y = 46 * mm
    boite("users", "comptes", cols["entreprises"], 30 * mm, lignes["users"])
    boite("audit_log", "comptes", cols["entreprises"], 12 * mm, lignes["audit_log"])

    def ancre(nom, cote):
        x, y = pos[nom]
        return {"g": (x, y + H / 2), "d": (x + W, y + H / 2), "h": (x + W / 2, y + H), "b": (x + W / 2, y)}[cote]

    def lien(a, ca, b, cb, pointille=False, couleur=VERT_FONCE):
        (x1, y1), (x2, y2) = ancre(a, ca), ancre(b, cb)
        ligne = Line(x1, y1, x2, y2, strokeColor=couleur, strokeWidth=0.9)
        if pointille:
            ligne.strokeDashArray = [2.2, 2]
        d.add(ligne)
        d.add(Polygon([x2, y2, x2 - 1.2 * mm, y2 + 0.8 * mm, x2 - 1.2 * mm, y2 - 0.8 * mm] if cb == "g" else
                      [x2, y2, x2 + 1.2 * mm, y2 + 0.8 * mm, x2 + 1.2 * mm, y2 - 0.8 * mm] if cb == "d" else
                      [x2, y2, x2 - 0.8 * mm, y2 + 1.2 * mm, x2 + 0.8 * mm, y2 + 1.2 * mm] if cb == "b" else
                      [x2, y2, x2 - 0.8 * mm, y2 - 1.2 * mm, x2 + 0.8 * mm, y2 - 1.2 * mm],
                      fillColor=couleur, strokeColor=couleur))

    # Clés étrangères (traits pleins) : la table de départ pointe vers la table référencée.
    for t in ["opportunites", "decideurs", "visites", "crm_notes"]:
        lien(t, "g", "companies", "d")
    lien("opportunite_etapes", "h", "opportunites", "b", couleur=FAMILLES["crm"][1])
    lien("audit_log", "h", "users", "b", couleur=FAMILLES["comptes"][1])
    # Liens par valeur, sans contrainte (pointillés).
    for t in ["osm_buildings", "ms_buildings", "ia_segments", "pv_detections"]:
        lien("companies", "g", t, "d", pointille=True, couleur=FAMILLES["toits"][1])
    lien("companies", "b", "pvgis_cache", "h", pointille=True, couleur=FAMILLES["entreprises"][1])
    return d


def tableau_colonnes(colonnes, couleur):
    lignes = [[P("Colonne", "entete"), P("Type", "entete"), P("Obligatoire", "entete"), P("À quoi elle sert", "entete")]]
    for nom, typ, obligatoire, role in colonnes:
        lignes.append([P(nom, "code"), P(typ, "cell"), P(obligatoire, "cell"), P(role, "cell")])
    t = Table(lignes, colWidths=[38 * mm, 21 * mm, 23 * mm, 88 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), couleur),
        ("GRID", (0, 0), (-1, -1), 0.4, TRAIT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4.5), ("RIGHTPADDING", (0, 0), (-1, -1), 4.5),
    ]))
    t.spaceAfter = 6
    return t


def pied(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 7.5)
    canvas.setFillColor(DOUX)
    canvas.drawString(20 * mm, 11 * mm, "Netis · Schéma de la base de données · 9 octobre 2026")
    canvas.drawRightString(190 * mm, 11 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(VERT)
    canvas.setLineWidth(2)
    canvas.line(20 * mm, 287 * mm, 190 * mm, 287 * mm)
    canvas.restoreState()


story = [
    P("Schéma de la base de données", "titre"),
    Spacer(1, 4),
    P("Netis · outil de prospection photovoltaïque · base PostgreSQL, schéma « solar intelligence ». "
      "13 tables, en 4 familles. Nombres de lignes au 9 octobre 2026.", "sous"),
    Spacer(1, 6),
    P("Vue d'ensemble", "h1"),
    schema(),
    P("Traits pleins : <b>clé étrangère</b> (la base garantit le lien ; supprimer une entreprise "
      "supprime son opportunité, ses visites, ses contacts et ses notes). Pointillés : lien par "
      "<b>valeur</b>, sans contrainte : companies.roof_key désigne un toit des tables de bâtiments "
      "(« osm:… », « ms:… », « ia:… ») et ses panneaux détectés ; la position de l'entreprise désigne "
      "sa zone dans pvgis_cache. users est référencée par presque toutes les tables (qui a fait "
      "quoi) : seul son lien avec le journal est dessiné.", "petit"),
    PageBreak(),
    P("Les 4 familles", "h1"),
]
familles = [
    ("comptes", "Qui utilise l'outil, et le journal de ce qui a été fait."),
    ("toits", "Les contours de bâtiments venus de trois sources, et la détection des panneaux déjà posés. "
              "Pour une entreprise, on cherche d'abord un toit OpenStreetMap, puis un toit détecté ou "
              "tracé dans l'application, puis un bâtiment Microsoft."),
    ("entreprises", "Les prospects, le toit trouvé sous chacun, et leur potentiel : puissance, "
                    "productible, d'où se déduisent production, CO₂ évité et économies."),
    ("crm", "Le travail des commerciaux : opportunité prise, étapes du pipeline, contacts, visites, notes. "
            "Séparé des entreprises : une entreprise existe sans suivi, et son suivi garde un historique."),
]
story.append(Table(
    [[P(f"<font color='{FAMILLES[f][1].hexval().replace('0x', '#')}'><b>{FAMILLES[f][0]}</b></font>", "p"),
      P(", ".join(t[0] for t in TABLES if t[1] == f), "code"), P(texte, "cell")] for f, texte in familles],
    colWidths=[38 * mm, 48 * mm, 84 * mm],
    style=TableStyle([("GRID", (0, 0), (-1, -1), 0.4, TRAIT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                      ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]),
))
# ---------- où trouver quoi : état actuel, historique, journal, notes ----------
VIOLET = FAMILLES["crm"][1]


def petit_tableau(lignes, largeurs, couleur):
    t = Table([[P(c, "entete") for c in lignes[0]]] + [[P(c, "cell") for c in l] for l in lignes[1:]],
              colWidths=[w * mm for w in largeurs], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), couleur),
        ("GRID", (0, 0), (-1, -1), 0.4, TRAIT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4.5), ("RIGHTPADDING", (0, 0), (-1, -1), 4.5),
    ]))
    t.spaceAfter = 6
    return t


story.append(KeepTogether([
    P("Suivi commercial : où trouver quoi", "h1"),
    P("Quatre tables gardent la trace du travail des commerciaux. Chacune a un rôle précis :"),
    petit_tableau([
        ["Table", "Ce qu'elle garde", "Où on la voit"],
        ["<b>opportunites</b>", "<b>L'état actuel</b> de chaque opportunité : commercial, étape, "
         "prochaine relance d'aujourd'hui. Une seule ligne par entreprise, mise à jour.",
         "Page « Mes opportunités » (colonnes, cartes), haut de la fiche."],
        ["<b>opportunite_etapes</b>", "<b>L'historique du pipeline</b> : une ligne à chaque enregistrement "
         "du suivi qui change quelque chose — étape, prochaine action prévue alors, commentaire, qui, quand. "
         "Rien n'y est modifié ni effacé.",
         "Dates sous les étapes du pipeline de la fiche, « depuis 3 j », partie Suivi → "
         "« Historique du pipeline »."],
        ["<b>crm_notes</b>", "Les notes libres (appel, échange), en dehors des changements d'étape.",
         "Partie « Notes » de la fiche."],
        ["<b>audit_log</b>", "Le journal général de l'application : prises, décideurs, visites, toits, "
         "entreprises ajoutées par le chatbot, suppressions…",
         "Partie « Historique » en bas de la fiche."],
    ], [37, 81, 52], VIOLET),
]))
story.append(KeepTogether([
    P("Exemple : l'historique d'une opportunité", "h2"),
    P("Ce qu'enregistre opportunite_etapes quand un commercial fait avancer un prospect. La date "
      "d'entrée dans une étape est celle de sa première ligne : la 3ᵉ ligne (nouvelle relance dans "
      "la même étape) ne change pas la date d'entrée dans « Contacté »."),
    petit_tableau([
        ["Ligne", "etape", "relance_le · relance_objet", "commentaire"],
        ["1 · prise", "a_contacter", "—", "—"],
        ["2", "contacte", "10 janv. · Fixer un rendez-vous", "Premier appel, intéressé"],
        ["3", "contacte", "15 janv. · Rappeler le DAF", "Absent ce jour"],
        ["4", "rdv_fixe", "20 janv. · Visite du site", "—"],
    ], [22, 30, 62, 56], VIOLET),
    P("Dans opportunites, la même entreprise n'a qu'une ligne : étape « rdv_fixe », relance le "
      "20 janvier, « Visite du site ».", "petit"),
]))
story.append(PageBreak())

for famille in FAMILLES:
    libelle, couleur = FAMILLES[famille]
    # Le titre de la famille part avec sa première table : jamais seul en bas de page.
    titre = [P(libelle, "h1")]
    for nom, fam, n, role, colonnes in TABLES:
        if fam != famille:
            continue
        story.append(KeepTogether(titre + [
            P(f"<font color='{couleur.hexval().replace('0x', '#')}'>■</font> {nom} "
              f"<font name='DV' size='8.5' color='#546e7a'>· {n:,} lignes</font>".replace(",", " "), "h2"),
            P(role),
            tableau_colonnes(colonnes, couleur),
        ]))
        titre = []

doc = SimpleDocTemplate(SORTIE, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                        topMargin=16 * mm, bottomMargin=18 * mm,
                        title="Schéma de la base de données", author="Netis")
doc.build(story, onFirstPage=pied, onLaterPages=pied)
print("écrit :", SORTIE)
