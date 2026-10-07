"""Génère docs/Calculs_potentiel_solaire.pdf : les formules de la puissance, de la
production et du CO₂ évité, avec des exemples réels et la source de chaque valeur.

    python3 scripts/pdf_calculs.py

Se lance hors du conteneur : il demande reportlab et la police DejaVu Sans
(/usr/share/fonts/truetype/dejavu), pour que ×, ÷, ² et ≈ s'affichent.

Les chiffres (entreprises, productibles, totaux de la base, facteur CO₂) sont
ceux du 7 octobre 2026, écrits ci-dessous : à mettre à jour à la main avant de
régénérer le document, comme la date du pied de page. Les hypothèses doivent
rester celles de services/solar.py.
"""
import math
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

SORTIE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "docs", "Calculs_potentiel_solaire.pdf")
pdfmetrics.registerFont(TTFont("DV", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB", italic="DV", boldItalic="DVB")

BLEU = colors.HexColor("#077ea3")
VERT = colors.HexColor("#2b9d86")
ENCRE = colors.HexColor("#0f1b20")
DOUX = colors.HexColor("#47585f")
FOND = colors.HexColor("#eef6f6")
TRAIT = colors.HexColor("#c9dcdd")

# Hypothèses de l'application (services/solar.py) et réglages PVGIS retenus.
FRACTION, SURF_PANNEAU, PUISS_PANNEAU = 0.70, 2.0, 0.5
CO2 = 0.596  # t de CO₂ par MWh du réseau marocain (2025)

st = {
    "titre": ParagraphStyle("titre", fontName="DVB", fontSize=21, leading=26, textColor=ENCRE),
    "sous": ParagraphStyle("sous", fontName="DV", fontSize=10.5, leading=15, textColor=DOUX),
    "h1": ParagraphStyle("h1", fontName="DVB", fontSize=14.5, leading=19, textColor=BLEU,
                         spaceBefore=14, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName="DVB", fontSize=11.5, leading=15, textColor=ENCRE,
                         spaceBefore=9, spaceAfter=4),
    "p": ParagraphStyle("p", fontName="DV", fontSize=9.8, leading=14.2, textColor=ENCRE, spaceAfter=5),
    "petit": ParagraphStyle("petit", fontName="DV", fontSize=8.4, leading=11.5, textColor=DOUX),
    "formule": ParagraphStyle("formule", fontName="DVB", fontSize=12.5, leading=17,
                              textColor=ENCRE, alignment=TA_CENTER),
    "cell": ParagraphStyle("cell", fontName="DV", fontSize=8.8, leading=11.5, textColor=ENCRE),
    "cellb": ParagraphStyle("cellb", fontName="DVB", fontSize=8.8, leading=11.5, textColor=colors.white),
}


def P(texte, style="p"):
    return Paragraph(texte, st[style])


def nb(x, d=0):
    """Nombre à la française : espace fine pour les milliers, virgule décimale."""
    s = f"{x:,.{d}f}".replace(",", " ").replace(".", ",")
    return s


def formule(texte, legende=None):
    contenu = [[P(texte, "formule")]]
    if legende:
        contenu.append([P(legende, "petit")])
    t = Table(contenu, colWidths=[170 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FOND),
        ("LINEBEFORE", (0, 0), (0, -1), 3, BLEU),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    t.spaceBefore, t.spaceAfter = 3, 6
    return t


def tableau(lignes, largeurs, entete=True, marge=6):
    donnees = [[P(str(c), "cellb" if (entete and i == 0) else "cell") for c in ligne]
               for i, ligne in enumerate(lignes)]
    t = Table(donnees, colWidths=[w * mm for w in largeurs], repeatRows=1 if entete else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, TRAIT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), marge), ("RIGHTPADDING", (0, 0), (-1, -1), marge),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6fafa")]),
    ]
    if entete:
        style.append(("BACKGROUND", (0, 0), (-1, 0), BLEU))
    t.setStyle(TableStyle(style))
    t.spaceAfter = 6
    return t


def pied(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 7.5)
    canvas.setFillColor(DOUX)
    canvas.drawString(20 * mm, 11 * mm, "Netis · Calculs du potentiel solaire · 7 octobre 2026")
    canvas.drawRightString(190 * mm, 11 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(VERT)
    canvas.setLineWidth(2)
    canvas.line(20 * mm, 287 * mm, 190 * mm, 287 * mm)
    canvas.restoreState()


# ---------- chiffres réels (base de données et PVGIS, 7 octobre 2026) ----------
entreprises = [  # nom, ville, surface du toit (m²), productible (kWh/kWc/an)
    ("Paframa", "Casablanca", 527, 1452.8),
    ("IEN MAROC", "Casablanca", 1492, 1447.0),
    ("J.J.W", "Casablanca", 4431, 1447.0),
    ("Univers Acier", "Casablanca", 56216, 1448.6),
    ("Maghreb Steel – Bled Solb", "Aïn Harrouda", 116378, 1448.7),
]
mensuel = [75.3, 86.5, 120.9, 142.7, 160.6, 162.7, 170.0, 156.9, 128.1, 104.7, 76.9, 69.9]
ENSOLEILLEMENT, PRODUCTIBLE_CASA = 1971.0, 1455.1


def puissance(surface):
    panneaux = math.floor(surface * FRACTION / SURF_PANNEAU)
    return panneaux, panneaux * PUISS_PANNEAU


story = []
story += [
    P("Calculs du potentiel solaire", "titre"),
    Spacer(1, 4),
    P("Netis · outil de prospection photovoltaïque · comment l'application calcule la "
      "<b>puissance installable</b> (kWc), la <b>production par an</b> (MWh) et le <b>CO₂ évité</b> "
      "(tonnes) de chaque entreprise. Chiffres réels de la base et de PVGIS au 7 octobre 2026. "
      "Les numéros entre crochets [1]… renvoient aux sources, à la fin du document.", "sous"),
    Spacer(1, 8),
]

# ---------- résumé ----------
story.append(P("En résumé", "h1"))
story.append(formule(
    "Toit (m²) × 70 % ÷ 2 m² × 0,5 kWc = <font color='#077ea3'>Puissance (kWc)</font><br/>"
    "Puissance (kWc) × Productible ÷ 1 000 = <font color='#2b9d86'>Production (MWh/an)</font><br/>"
    "Production (MWh/an) × 0,596 = <font color='#3db258'>CO₂ évité (t/an)</font>",
    "La puissance dépend du toit : c'est la taille de l'installation, ce que l'on vend. "
    "La production dépend de la puissance et du soleil du lieu : c'est l'électricité produite, "
    "ce qui fait économiser le client. Le CO₂ évité est ce que le réseau électrique aurait émis "
    "pour produire la même électricité."))
n, k = puissance(4431)
story.append(tableau([
    ["Étape", "Calcul pour J.J.W (Casablanca)", "Résultat"],
    ["Surface du toit", "mesurée sur la carte", "4 431 m²"],
    ["Surface utile", "4 431 × 70 %", f"{nb(4431 * FRACTION, 1)} m²"],
    ["Panneaux", f"{nb(4431 * FRACTION, 1)} ÷ 2 m², arrondi vers le bas", f"{nb(n)} panneaux"],
    ["Puissance", f"{nb(n)} × 0,5 kWc", f"<b>{nb(k)} kWc</b>"],
    ["Productible", "donné par PVGIS pour ce lieu", "1 447 kWh/kWc/an"],
    ["Production", f"{nb(k)} × 1 447 ÷ 1 000", f"<b>{nb(k * 1447 / 1000, 1)} MWh/an</b>"],
    ["CO₂ évité", f"{nb(k * 1447 / 1000, 1)} × 0,596", f"<b>{nb(round(k * 1447 / 1000, 1) * CO2, 1)} t/an</b>"],
], [38, 82, 50]))

# ---------- unités ----------
story.append(P("1. Les unités", "h1"))
story.append(P("Il n'y a que deux sortes d'unités : la <b>puissance</b> (la force de "
               "l'installation à un instant) et l'<b>énergie</b> (la quantité d'électricité "
               "produite dans le temps, celle de la facture)."))
story.append(tableau([
    ["Unité", "Nature", "Signification", "Exemple"],
    ["kW", "Puissance", "1 000 watts", "Un climatiseur ≈ 1 kW"],
    ["kWc (= kWp)", "Puissance", "Puissance maximale des panneaux en plein soleil. "
     "« c » = crête ; « p » = peak en anglais : c'est la même unité.", "2 panneaux de 500 W = 1 kWc"],
    ["MWc", "Puissance", "1 000 kWc", "Maghreb Steel ≈ 20 MWc"],
    ["kWh", "Énergie", "1 kW pendant 1 heure", "Climatiseur de 1 kW, 5 h = 5 kWh"],
    ["MWh", "Énergie", "1 000 kWh", "J.J.W ≈ 1 121 MWh par an"],
    ["GWh", "Énergie", "1 000 MWh = 1 000 000 kWh", "Tous les prospects ≈ 1 218 GWh par an"],
    ["t CO₂", "Émissions", "Tonne de dioxyde de carbone", "J.J.W évite ≈ 668 t par an"],
], [24, 22, 74, 50]))

# ---------- formule 1 ----------
story.append(P("2. Formule 1 : la puissance installable (kWc)", "h1"))
story.append(formule("Puissance (kWc) = Surface du toit × 70 % ÷ 2 m² × 0,5 kWc",
                     "Le nombre de panneaux est arrondi vers le bas : un demi-panneau ne se pose pas."))
story.append(tableau([
    ["Étape", "Calcul", "Pourquoi"],
    ["① Surface utile", "Toit × 70 %", "On ne couvre pas tout le toit : bords, passages, accès, espacement des rangées."],
    ["② Nombre de panneaux", "Surface utile ÷ 2 m²", "Un panneau de référence occupe 2 m²."],
    ["③ Puissance", "Panneaux × 0,5 kWc", "Un panneau de référence fait 500 W, soit 0,5 kWc."],
], [40, 40, 90]))
story.append(P("Exemple simple : un toit de 1 000 m² donne 1 000 × 70 % = 700 m² utiles, "
               "700 ÷ 2 = 350 panneaux, 350 × 0,5 = <b>175 kWc</b>."))
story.append(P("Ordres de grandeur", "h2"))
story.append(tableau([
    ["Installation", "Taille"],
    ["Une maison", "3 à 10 kWc"],
    ["Un petit atelier", "30 à 100 kWc"],
    ["Une usine moyenne (Paframa)", "92 kWc"],
    ["Une grande usine (J.J.W)", "775 kWc"],
    ["Un très grand site (Maghreb Steel)", "≈ 20 366 kWc (20 MWc)"],
], [90, 80]))

# ---------- formule 2 ----------
story.append(PageBreak())
story.append(P("3. Formule 2 : la production par an (MWh)", "h1"))
story.append(formule("Production (MWh/an) = Puissance (kWc) × Productible (kWh/kWc/an) ÷ 1 000",
                     "÷ 1 000 pour passer des kWh aux MWh."))
story.append(P("Le <b>productible</b>, c'est ce que produit <b>1 kWc installé, en un an, à cet "
               "endroit</b>. Il se lit aussi comme un nombre d'heures : 1 kWc qui produit "
               "1 455 kWh par an, c'est comme s'il avait tourné à fond pendant 1 455 heures."))
story.append(formule("Productible = Ensoleillement reçu (kWh/m²/an) × Rendement réel",
                     "Le rendement réel tient compte des pertes : chaleur (un panneau produit moins "
                     "quand il chauffe), câbles, onduleur, poussière."))
story.append(P(f"À Casablanca, avec les réglages retenus : ensoleillement reçu "
               f"<b>{nb(ENSOLEILLEMENT)} kWh/m²/an</b> × rendement réel "
               f"<b>{nb(PRODUCTIBLE_CASA / ENSOLEILLEMENT * 100)} %</b> = productible "
               f"<b>{nb(PRODUCTIBLE_CASA)} kWh/kWc/an</b>."))

story.append(P("D'où vient le productible : PVGIS", "h2"))
story.append(P("Le productible est demandé à <b>PVGIS</b> [1] (re.jrc.ec.europa.eu/pvg_tools/fr), "
               "l'outil gratuit de la Commission européenne, qui couvre le Maroc. Il s'appuie sur "
               "les mesures satellite de l'ensoleillement de 2005 à 2023 (base SARAH-3) et sur les "
               "températures de la base ERA5 [2]. L'application interroge le "
               "même moteur que le site, pour 1 kWc. L'ensoleillement change peu sur quelques "
               "kilomètres : les entreprises sont regroupées par zone d'environ 10 km et chaque "
               "zone n'est demandée qu'une fois. Toute la base tient en 30 zones."))
story.append(P("Pour retrouver le chiffre sur le site : onglet « Système PV connecté au réseau », "
               "silicium cristallin, puissance crête 1 kWc, pertes 14 %, position de montage "
               "« intégré au bâtiment », inclinaison 0°, puis « Visualiser les résultats ».", "petit"))
story.append(Spacer(1, 4))

story.append(P("Réglages retenus : le cas le plus prudent", "h2"))
story.append(P("L'image satellite est vue du dessus : elle ne montre ni la pente ni l'orientation "
               "du toit. On retient donc les réglages qui donnent la production la plus basse, "
               "pour ne jamais annoncer à un client plus que ce qu'il produira."))
story.append(tableau([
    ["Réglage", "Valeur retenue", "Pourquoi"],
    ["Inclinaison", "0° (panneaux à plat)", "Pire cas : à plat, le panneau capte le moins de soleil."],
    ["Orientation", "Sud (sans effet à plat)", "À plat, l'orientation ne compte pas."],
    ["Position de montage", "Intégré au bâtiment", "Panneaux près de la toiture, peu ventilés, donc plus chauds."],
    ["Pertes de l'installation", "14 %", "Valeur proposée par défaut par PVGIS [1] (câbles, onduleur, salissure)."],
], [42, 46, 82]))
story.append(P("Effet de l'inclinaison à Casablanca (intégré au bâtiment, plein sud, 14 % de pertes)", "h2"))
story.append(tableau([
    ["Inclinaison", "Productible", "Écart avec 0°"],
    ["<b>0° (retenu)</b>", "<b>1 455 kWh/kWc/an</b>", "—"],
    ["10°", "1 555 kWh/kWc/an", "+ 6,9 %"],
    ["32° (optimale)", "1 646 kWh/kWc/an", "+ 13,1 %"],
], [55, 60, 55]))
story.append(P("Le productible selon le lieu", "h2"))
story.append(tableau([
    ["Zone", "Productible (réglages retenus)"],
    ["La plus faible : près de Mohammédia (33,8 ; -7,3)", "1 423 kWh/kWc/an"],
    ["Casablanca (33,6 ; -7,6)", "1 455 kWh/kWc/an"],
    ["La plus forte : près d'Agadir (28,3 ; -9,1)", "1 654 kWh/kWc/an"],
    ["Moyenne sur toutes les entreprises", "1 452 kWh/kWc/an"],
], [110, 60]))
story.append(P("À puissance égale, une usine près d'Agadir produit donc environ 16 % de plus qu'une "
               "usine près de Mohammédia.", "petit"))

story.append(P("La production mois par mois (Casablanca, pour 1 kWc)", "h2"))
mois = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]
story.append(tableau([mois + ["Année"], [nb(v, 1) for v in mensuel] + [f"<b>{nb(sum(mensuel), 1)}</b>"]],
                     [12.5] * 12 + [20], marge=2.5))
story.append(P("En kWh. L'été produit plus du double de l'hiver (170 kWh en juillet contre 70 en "
               "décembre) : le soleil est plus haut et les jours plus longs.", "petit"))

# ---------- CO2 ----------
story.append(PageBreak())
story.append(P("4. Formule 3 : le CO₂ évité par an (tonnes)", "h1"))
story.append(formule("CO₂ évité (t/an) = Production (MWh/an) × Facteur d'émission du réseau (t CO₂/MWh)",
                     "Chaque MWh produit sur le toit est un MWh que l'entreprise n'achète plus au réseau."))
story.append(P("Produire de l'électricité sur le réseau marocain émet du CO₂, surtout à cause des "
               "centrales à charbon. Le <b>facteur d'émission</b> dit combien de CO₂ émet en moyenne "
               "chaque MWh du réseau. Au Maroc, il est d'environ <b>596 g de CO₂ par kWh</b>, soit "
               "<b>0,596 tonne par MWh</b> (2025) [3][4] : l'électricité marocaine vient encore à 76 % des "
               "énergies fossiles, dont 62 % de charbon [3]."))
story.append(tableau([
    ["Entreprise", "Production par an", "× facteur", "CO₂ évité par an"],
    ["J.J.W", "1 121,4 MWh", "× 0,596", f"<b>{nb(1121.4 * CO2, 1)} t</b>"],
    ["IEN MAROC", "377,7 MWh", "× 0,596", f"<b>{nb(377.7 * CO2, 1)} t</b>"],
    ["Toute la base", "1 217 652 MWh", "× 0,596", "<b>≈ 725 720 t</b>"],
], [50, 45, 30, 45]))
story.append(P("Le PDF de la roadmap Netis prend un exemple à 0,4 t/MWh (300 MWh pour 120 t), typique "
               "d'un réseau plus propre comme en Europe. Au Maroc, le facteur est plus élevé : pour un "
               "même MWh, une installation y évite donc plus de CO₂. Le facteur baisse d'année en année, "
               "à mesure que le pays ajoute de l'éolien et du solaire : pour un document officiel ou un "
               "client, reprendre le chiffre publié par l'ONEE ou le ministère de la Transition "
               "énergétique.", "petit"))

# ---------- exemples ----------
story.append(P("5. Exemples avec des entreprises de la base", "h1"))
lignes = [["Entreprise", "Toit", "Panneaux", "Puissance", "Productible", "Production par an", "CO₂ évité"]]
for nom, ville, surface, prod in entreprises:
    n, k = puissance(surface)
    mwh = round(k * prod / 1000, 1)
    lignes.append([f"{nom}<br/><font size='7.5' color='#47585f'>{ville}</font>", f"{nb(surface)} m²",
                   nb(n), f"{nb(k, 1 if k % 1 else 0)} kWc", nb(prod, 1), f"<b>{nb(mwh, 1)} MWh</b>",
                   f"{nb(mwh * CO2)} t"])
story.append(tableau(lignes, [29, 20, 21, 22, 25, 29, 24], marge=3.5))
story.append(P("Détail du calcul pour IEN MAROC", "h2"))
n, k = puissance(1492)
story.append(tableau([
    ["Étape", "Calcul", "Résultat"],
    ["① Surface utile", "1 492 × 70 %", f"{nb(1492 * FRACTION, 1)} m²"],
    ["② Panneaux", f"{nb(1492 * FRACTION, 1)} ÷ 2, arrondi vers le bas", f"{nb(n)}"],
    ["③ Puissance", f"{nb(n)} × 0,5", f"{nb(k)} kWc"],
    ["④ Production", f"{nb(k)} × 1 447 ÷ 1 000", f"<b>{nb(k * 1447 / 1000, 1)} MWh/an</b>"],
    ["⑤ CO₂ évité", f"{nb(k * 1447 / 1000, 1)} × 0,596", f"<b>{nb(round(k * 1447 / 1000, 1) * CO2, 1)} t/an</b>"],
], [40, 80, 50]))

story.append(P("Pour toute la base", "h2"))
story.append(tableau([
    ["Indicateur", "Valeur"],
    ["Entreprises avec un productible", "2 235 sur 2 238 (3 ont des coordonnées en mer)"],
    ["Puissance totale (toits distincts, non équipés)", "840 073 kWc ≈ 840 MWc"],
    ["Production totale par an", "1 217 652 MWh ≈ 1 218 GWh"],
    ["CO₂ évité par an", "≈ 725 720 t (environ 726 000 t)"],
], [85, 85]))
story.append(P("Un toit partagé par plusieurs entreprises n'est compté qu'une fois, et les "
               "entreprises déjà équipées sont exclues.", "petit"))

# ---------- limites ----------
story.append(P("6. Hypothèses à valider, et leurs sources", "h1"))
story.append(tableau([
    ["Valeur", "Utilisée pour", "Source", "À savoir"],
    ["Surface utile : 70 % du toit", "Puissance", "Hypothèse Netis (réglage de l'application)",
     "La même pour tous les toits. Une formule selon la forme du toit est prête ; elle sera calibrée "
     "sur des chantiers Netis réels."],
    ["Panneau : 2 m², 500 W", "Puissance", "Hypothèse Netis (panneau de référence)",
     "Un panneau récent de 500 W fait plutôt 2,2 à 2,6 m² (fiches techniques du marché) : la puissance "
     "est donc un peu optimiste."],
    ["Surface du toit", "Puissance", "OpenStreetMap [6], Microsoft Building Footprints [7], détection IA "
     "MobileSAM [8], tracés manuels", "Dépend de la position Google de l'entreprise [9] : une position "
     "approximative donne un mauvais toit (14 entreprises sur le point « GCM8+8J8 »)."],
    ["Productible (1 423 à 1 654 kWh/kWc/an)", "Production", "PVGIS v5.3 [1], ensoleillement "
     "SARAH-3 et températures ERA5, 2005–2023 [2]", "Calculé pour chaque zone de 10 km."],
    ["Inclinaison 0°, intégré au bâtiment", "Production", "Choix Netis : cas le plus prudent",
     "La vraie pente pourra être saisie après une visite."],
    ["Pertes : 14 %", "Production", "Valeur par défaut de PVGIS [1]",
     "Suppose des panneaux nettoyés ; avec la poussière, les pertes réelles sont plus fortes."],
    ["Facteur d'émission : 0,596 t CO₂/MWh", "CO₂ évité", "Maroc, 2025, d'après les données de "
     "l'Agence internationale de l'énergie [3][4]", "Baisse chaque année ; à confirmer avec le chiffre "
     "de l'ONEE [5] pour un document officiel."],
], [36, 21, 50, 63], marge=4))
story.append(P("Tous les réglages se trouvent à un seul endroit de l'application "
               "(<font name='DVB'>services/solar.py</font>) et se modifient sans toucher au code."))

story.append(P("7. Sources", "h1"))
story.append(P("Consultées le 7 octobre 2026.", "petit"))
sources = [
    ("[1]", "PVGIS – Photovoltaic Geographical Information System, Commission européenne, Centre commun "
     "de recherche (JRC). Outil en ligne et API v5.3 (calcul « PVcalc »).",
     "https://re.jrc.ec.europa.eu/pvg_tools/fr/"),
    ("[2]", "Données utilisées par PVGIS pour le Maroc, indiquées dans chaque réponse de l'API : "
     "ensoleillement PVGIS-SARAH3 (satellite), températures ERA5, années 2005 à 2023.",
     "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"),
    ("[3]", "Power Atlas – Morocco (inzonex) : intensité carbone du réseau, 596 g CO₂/kWh (2025), "
     "mix électrique (76 % fossile, 62 % charbon), d'après l'Agence internationale de l'énergie.",
     "https://inzonex.co.uk/poweratlas/morocco/"),
    ("[4]", "Climatiq – facteurs d'émission de l'électricité, Maroc (données de l'Agence internationale "
     "de l'énergie).", "https://www.climatiq.io/data/region/ma"),
    ("[5]", "ONEE – Office national de l'électricité et de l'eau potable : chiffres officiels du "
     "réseau marocain, à privilégier pour un document client.", "https://www.one.org.ma"),
    ("[6]", "OpenStreetMap – contours des bâtiments.", "https://www.openstreetmap.org"),
    ("[7]", "Microsoft Global ML Building Footprints – bâtiments détectés par IA sur images satellite.",
     "https://github.com/microsoft/GlobalMLBuildingFootprints"),
    ("[8]", "MobileSAM – segmentation des toits sur l'image satellite (Esri).",
     "https://github.com/ChaoningZhang/MobileSAM"),
    ("[9]", "Google Maps, via Apify (acteur compass/crawler-google-places) – nom, adresse, téléphone "
     "et position des entreprises.", "https://apify.com/compass/crawler-google-places"),
]
story.append(tableau([[r, f"{t}<br/><font color='#077ea3' size='7.5'>{u}</font>"] for r, t, u in sources],
                     [12, 158], entete=False, marge=4))

# Un titre ne reste jamais seul en bas de page : il part avec ce qui le suit
# (les deux blocs suivants pour une section, le suivant pour un sous-titre).
groupee, i = [], 0
while i < len(story):
    el = story[i]
    style = getattr(getattr(el, "style", None), "name", None)
    n = {"h1": 1, "h2": 1}.get(style, 0)
    if n and i + 1 < len(story):
        suite = [x for x in story[i + 1:i + 1 + n] if not isinstance(x, PageBreak)]
        groupee.append(KeepTogether([el, *suite]))
        i += 1 + len(suite)
    else:
        groupee.append(el)
        i += 1
story = groupee

doc = SimpleDocTemplate(SORTIE, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                        topMargin=16 * mm, bottomMargin=18 * mm,
                        title="Calculs du potentiel solaire", author="Netis")
doc.build(story, onFirstPage=pied, onLaterPages=pied)
print("écrit :", SORTIE)
