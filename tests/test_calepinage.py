"""Placement des panneaux sur un toit.

Ce qui casserait en silence : des panneaux posés hors du toit ou sur la bande
de sécurité, un toit en L compté comme son rectangle englobant, ou un toit
tourné de 30° qui perdrait la moitié de ses panneaux faute d'aligner la grille.
"""
import math

from services import calepinage as c

LAT0, LON0 = 33.59, -7.60
KX, KY = 111_320.0 * math.cos(math.radians(LAT0)), 110_540.0


def toit(points_m, angle_deg=0.0):
    """Polygone lon/lat à partir de points en mètres (tourné de angle_deg)."""
    a = math.radians(angle_deg)
    out = []
    for x, y in points_m:
        xr, yr = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
        out.append([LON0 + xr / KX, LAT0 + yr / KY])
    return out


def nombre_attendu(largeur, hauteur):
    """Rectangle simple, calculé indépendamment : colonnes x rangées (avec une
    allée toutes les RANGEES_PAR_BLOC rangées), meilleure des deux poses."""
    def grille(w, h, lu, hu):
        if lu < w or hu < h:
            return 0
        colonnes = int((lu - w) // (w + c.ECART_M)) + 1
        rangees, y = 0, 0.0
        while y + h <= hu + 1e-9:
            rangees += 1
            y += h + (c.ALLEE_M if rangees % c.RANGEES_PAR_BLOC == 0 else c.ECART_M)
        return colonnes * rangees
    lu, hu = largeur - 2 * c.MARGE_M, hauteur - 2 * c.MARGE_M
    return max(grille(c.PANNEAU_LARGEUR_M, c.PANNEAU_LONGUEUR_M, lu, hu),
               grille(c.PANNEAU_LONGUEUR_M, c.PANNEAU_LARGEUR_M, lu, hu))


def test_un_rectangle_donne_la_grille_exacte():
    r = c.calepiner(toit([(0, 0), (40, 0), (40, 20), (0, 20)]))
    assert r["panneaux"] == nombre_attendu(40, 20)
    assert len(r["formes"]) == r["panneaux"]
    assert r["kwc"] == round(r["panneaux"] * c.SOLAR_PANEL_POWER_W / 1000, 1)


def test_un_toit_tourne_garde_ses_panneaux():
    droit = c.calepiner(toit([(0, 0), (40, 0), (40, 20), (0, 20)]))["panneaux"]
    tourne = c.calepiner(toit([(0, 0), (40, 0), (40, 20), (0, 20)], angle_deg=31))["panneaux"]
    assert tourne == droit


def test_un_toit_en_l_n_est_pas_compte_comme_son_rectangle():
    l_forme = c.calepiner(toit([(0, 0), (40, 0), (40, 10), (10, 10), (10, 30), (0, 30)]))["panneaux"]
    assert 0 < l_forme < nombre_attendu(40, 30) / 2


def test_aucun_panneau_sur_la_bande_de_securite():
    r = c.calepiner(toit([(0, 0), (40, 0), (40, 20), (0, 20)]))
    for panneau in r["formes"]:
        for lat, lon in panneau:
            x, y = (lon - LON0) * KX, (lat - LAT0) * KY
            assert c.MARGE_M - 0.05 <= x <= 40 - c.MARGE_M + 0.05
            assert c.MARGE_M - 0.05 <= y <= 20 - c.MARGE_M + 0.05


def test_un_toit_trop_petit_ne_recoit_rien():
    assert c.calepiner(toit([(0, 0), (2.5, 0), (2.5, 3), (0, 3)]))["panneaux"] == 0
    assert c.calepiner([])["panneaux"] == 0


def test_un_tres_grand_toit_est_dessine_par_rangees():
    r = c.calepiner(toit([(0, 0), (300, 0), (300, 200), (0, 200)]))
    assert r["panneaux"] > c.MAX_DESSINES
    assert r["formes"] == []
    # Les rangées couvrent exactement les panneaux posés, en bien moins de formes.
    assert sum(g["n"] for g in r["rangees"]) == r["panneaux"]
    assert len(r["rangees"]) < r["panneaux"] / 50


def test_un_toit_en_l_coupe_ses_rangees_aux_renfoncements():
    grand = toit([(0, 0), (300, 0), (300, 100), (100, 100), (100, 200), (0, 200)])
    r = c.calepiner(grand)
    assert r["panneaux"] > c.MAX_DESSINES
    assert sum(g["n"] for g in r["rangees"]) == r["panneaux"]


def test_les_allees_laissent_une_part_realiste_du_toit():
    # Sans allées, ~90 % du toit serait couvert ; avec, on retombe vers 70 %.
    r = c.calepiner(toit([(0, 0), (100, 0), (100, 60), (0, 60)]))
    part = r["panneaux"] * c.PANNEAU_LARGEUR_M * c.PANNEAU_LONGUEUR_M / (100 * 60)
    assert 0.6 <= part <= 0.8
