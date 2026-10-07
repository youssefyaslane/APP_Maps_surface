"""Hypothèses d'installation photovoltaïque — source unique de vérité.

La même estimation était écrite trois fois (serveur, calcul en masse, carte).
Trois copies d'une constante, c'est trois occasions de n'en corriger que deux :
un toit pouvait afficher une puissance sur la carte et une autre au tableau de
bord sans qu'aucune ligne ne paraisse fausse.

Le module ne fait aucune I/O — ni base, ni réseau — et se teste donc sans rien
démarrer (`tests/test_solar.py`).

Les valeurs sont surchargeables par l'environnement : une hypothèse de pose
n'est pas une décision de code, elle dépend du chantier.
"""
import os


def _env_float(name, default):
    """Valeur d'environnement, en retombant sur le défaut si elle est illisible.

    Une variable mal saisie ne doit pas empêcher l'application de démarrer :
    elle produirait sinon une panne au déploiement pour une faute de frappe.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Panneau de référence : 2 m², 500 Wc (auparavant 1,7 m² et 400 Wc).
SOLAR_PANEL_AREA_M2 = _env_float("SOLAR_PANEL_AREA_M2", 2.0)
SOLAR_PANEL_POWER_W = _env_float("SOLAR_PANEL_POWER_W", 500.0)

# Part de la toiture réellement couverte : le reste est consommé par les accès,
# les marges de sécurité, les édicules techniques et l'espacement anti-ombrage.
# Hypothèse simplificatrice assumée — elle ne tient compte ni de l'orientation
# ni de l'inclinaison réelles, et reste le principal facteur d'incertitude de
# la puissance annoncée.
SOLAR_USABLE_ROOF_FRACTION = _env_float("SOLAR_USABLE_ROOF_FRACTION", 0.7)


def _env_number(name, default):
    """Comme _env_float, mais accepte zéro et les valeurs négatives : une
    inclinaison de 0° ou une orientation de -90° (est) sont légitimes."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# Production annuelle, calculée par PVGIS (services/pvgis.py) pour chaque
# emplacement. La pente et l'orientation réelles du toit ne se voient pas sur
# l'image satellite : on retient le cas le plus prudent, des panneaux posés à
# plat (0°), pour ne jamais annoncer à un client plus que ce qu'il produira.
# Inclinaison en degrés (0 = à plat) ; orientation en degrés, convention
# PVGIS : 0 = plein sud, -90 = est, 90 = ouest (sans effet à plat).
SOLAR_TILT_DEG = _env_number("SOLAR_TILT_DEG", 0.0)
SOLAR_AZIMUTH_DEG = _env_number("SOLAR_AZIMUTH_DEG", 0.0)
# Pertes de l'installation hors chaleur (câbles, onduleur, salissure), en %.
# 14 % est la valeur par défaut de PVGIS ; sans nettoyage régulier des
# panneaux, la poussière les rend plus fortes au Maroc.
SOLAR_SYSTEM_LOSS_PCT = _env_float("SOLAR_SYSTEM_LOSS_PCT", 14.0)
# « building » : panneaux posés près de la toiture, peu ventilés, donc plus
# chauds que sur un support libre (« free ») — environ 4 % de production en
# moins, choix prudent pour une toiture d'usine.
SOLAR_MOUNTING = os.environ.get("SOLAR_MOUNTING", "building").strip() or "building"


# CO₂ évité : chaque MWh produit sur le toit n'est plus acheté au réseau, dont
# la production émet environ 0,596 t de CO₂ par MWh au Maroc (2025, d'après les
# données de l'Agence internationale de l'énergie : 76 % de fossiles, dont 62 %
# de charbon). Le facteur baisse à mesure que le réseau se verdit : à reprendre
# du chiffre publié par l'ONEE pour un document officiel.
SOLAR_CO2_T_PER_MWH = _env_float("SOLAR_CO2_T_PER_MWH", 0.596)


def estimate_solar(area_m2):
    """(nombre de panneaux, puissance en kWc) installables sur cette surface.

    Renvoie (0, 0.0) pour une surface absente ou nulle, afin qu'un toit
    introuvable ne se distingue pas d'un toit vide côté appelant.
    """
    if not area_m2 or area_m2 <= 0:
        return 0, 0.0
    n_panels = int(area_m2 * SOLAR_USABLE_ROOF_FRACTION / SOLAR_PANEL_AREA_M2)
    return n_panels, round(n_panels * SOLAR_PANEL_POWER_W / 1000, 2)


def production_mwh(kwc, yield_kwh_kwc):
    """Production annuelle (MWh) : puissance × productible du lieu. None tant
    que le productible n'est pas connu, pour ne pas afficher « 0 MWh »."""
    if not kwc or not yield_kwh_kwc:
        return None
    return round(kwc * yield_kwh_kwc / 1000, 1)


def co2_evite_t(production):
    """CO₂ évité par an (tonnes) pour une production annuelle en MWh."""
    if not production:
        return None
    return round(production * SOLAR_CO2_T_PER_MWH, 1)


def config():
    """Hypothèses transmises au navigateur, pour que la carte calcule la même
    chose que le serveur au lieu de redéfinir ses propres constantes."""
    return {
        "panel_area_m2": SOLAR_PANEL_AREA_M2,
        "panel_power_w": SOLAR_PANEL_POWER_W,
        "usable_roof_fraction": SOLAR_USABLE_ROOF_FRACTION,
        "co2_t_per_mwh": SOLAR_CO2_T_PER_MWH,
    }
