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


# Panneau de référence : 1,0 m x 1,7 m, 400 Wc.
SOLAR_PANEL_AREA_M2 = _env_float("SOLAR_PANEL_AREA_M2", 1.7)
SOLAR_PANEL_POWER_W = _env_float("SOLAR_PANEL_POWER_W", 400.0)

# Part de la toiture réellement couverte : le reste est consommé par les accès,
# les marges de sécurité, les édicules techniques et l'espacement anti-ombrage.
# Hypothèse simplificatrice assumée — elle ne tient compte ni de l'orientation
# ni de l'inclinaison réelles, et reste le principal facteur d'incertitude de
# la puissance annoncée.
SOLAR_USABLE_ROOF_FRACTION = _env_float("SOLAR_USABLE_ROOF_FRACTION", 0.7)


def estimate_solar(area_m2):
    """(nombre de panneaux, puissance en kWc) installables sur cette surface.

    Renvoie (0, 0.0) pour une surface absente ou nulle, afin qu'un toit
    introuvable ne se distingue pas d'un toit vide côté appelant.
    """
    if not area_m2 or area_m2 <= 0:
        return 0, 0.0
    n_panels = int(area_m2 * SOLAR_USABLE_ROOF_FRACTION / SOLAR_PANEL_AREA_M2)
    return n_panels, round(n_panels * SOLAR_PANEL_POWER_W / 1000, 2)


def config():
    """Hypothèses transmises au navigateur, pour que la carte calcule la même
    chose que le serveur au lieu de redéfinir ses propres constantes."""
    return {
        "panel_area_m2": SOLAR_PANEL_AREA_M2,
        "panel_power_w": SOLAR_PANEL_POWER_W,
        "usable_roof_fraction": SOLAR_USABLE_ROOF_FRACTION,
    }
