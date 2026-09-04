"""Géométrie : surface, point-dans-polygone, distance.

C'est le calcul dont tout le reste dépend. La documentation d'architecture le
dit : la précision finale ne tient pas à la physique du panneau mais à la
justesse de la surface — 30 % d'erreur sur le toit, 30 % sur les dirhams.
"""
import math

import pytest

import app


def carre(lon0, lat0, cote_deg):
    return [
        [lon0, lat0],
        [lon0 + cote_deg, lat0],
        [lon0 + cote_deg, lat0 + cote_deg],
        [lon0, lat0 + cote_deg],
    ]


# --- Surface ---------------------------------------------------------------


def test_surface_d_un_carre_connu_a_casablanca():
    # 0,001° de côté vers 33,5° de latitude : 111,32 m du nord au sud, et autant
    # multiplié par le cosinus de la latitude d'ouest en est. Le cosinus est pris
    # à la latitude moyenne des sommets (33,5005 ici), pas à celle du coin bas.
    # Vérifie l'ordre de grandeur réel, ce qu'un test purement relatif laisserait
    # passer : confusion mètres/kilomètres, ou cosinus de latitude oublié.
    cote = math.radians(0.001) * 6378137
    lat_moyenne = 33.5005
    attendu = cote * math.cos(math.radians(lat_moyenne)) * cote

    # 1e-6 reste quinze fois plus fin que l'écart qu'introduirait un cosinus pris
    # à la mauvaise latitude, tout en laissant passer les arrondis de la somme
    # du lacet, qui manipule des produits de l'ordre de 10^12.
    assert app._polygon_area_m2(carre(-7.6, 33.5, 0.001)) == pytest.approx(attendu, rel=1e-6)
    assert attendu == pytest.approx(10333, rel=0.01)


def test_le_sens_de_parcours_ne_change_pas_la_surface():
    # Les sources ne s'accordent pas sur l'orientation des anneaux : OSM,
    # Microsoft et un contour issu d'OpenCV peuvent tourner dans deux sens.
    # Une surface négative ferait un prospect à 0 kWc.
    horaire = carre(-7.6, 33.5, 0.001)
    assert app._polygon_area_m2(list(reversed(horaire))) == pytest.approx(
        app._polygon_area_m2(horaire)
    )


def test_un_contour_degenere_ne_vaut_aucune_surface():
    assert app._polygon_area_m2([]) == 0.0
    assert app._polygon_area_m2([[-7.6, 33.5], [-7.6, 33.6]]) == 0.0


def test_un_triangle_vaut_la_moitie_de_son_carre():
    # Égalité approchée, et non exacte : la projection prend pour référence la
    # latitude moyenne des sommets, qui n'est pas la même pour trois points que
    # pour quatre. L'écart qui en résulte vaut 2 millionièmes — sans effet sur
    # une surface de toiture, mais il interdit un test d'égalité stricte.
    c = carre(-7.6, 33.5, 0.001)
    triangle = [c[0], c[1], c[2]]
    assert app._polygon_area_m2(triangle) == pytest.approx(
        app._polygon_area_m2(c) / 2, rel=1e-4
    )


def test_doubler_le_cote_quadruple_la_surface():
    petit = app._polygon_area_m2(carre(-7.6, 33.5, 0.001))
    grand = app._polygon_area_m2(carre(-7.6, 33.5, 0.002))
    assert grand == pytest.approx(4 * petit, rel=1e-3)


# --- Point dans polygone ---------------------------------------------------


def test_point_dedans_et_dehors():
    c = carre(-7.6, 33.5, 0.001)
    assert app._point_in_polygon(-7.5995, 33.5005, c) is True
    assert app._point_in_polygon(-7.61, 33.5005, c) is False
    assert app._point_in_polygon(-7.5995, 33.52, c) is False


def test_forme_en_l_le_creux_est_bien_dehors():
    # Cas réel des toitures en L : le centroïde géométrique tombe dans le creux,
    # hors du bâtiment. C'est ce qui justifie _point_inside_polygon_guaranteed.
    forme_l = [
        [0.000, 0.000],
        [0.002, 0.000],
        [0.002, 0.001],
        [0.001, 0.001],
        [0.001, 0.002],
        [0.000, 0.002],
    ]
    assert app._point_in_polygon(0.0005, 0.0005, forme_l) is True  # dans la branche
    assert app._point_in_polygon(0.0015, 0.0015, forme_l) is False  # dans le creux


def test_le_point_garanti_tombe_toujours_dans_la_forme():
    forme_l = [
        [0.000, 0.000],
        [0.002, 0.000],
        [0.002, 0.001],
        [0.001, 0.001],
        [0.001, 0.002],
        [0.000, 0.002],
    ]
    centroide = app._polygon_centroid(forme_l)
    assert app._point_in_polygon(*centroide, forme_l) is False  # le centroïde échoue

    garanti = app._point_inside_polygon_guaranteed(forme_l)
    assert app._point_in_polygon(*garanti, forme_l) is True


def test_le_point_garanti_reste_le_centroide_sur_une_forme_convexe():
    c = carre(-7.6, 33.5, 0.001)
    assert app._point_inside_polygon_guaranteed(c) == app._polygon_centroid(c)


# --- Distance --------------------------------------------------------------


def test_distance_nulle_pour_un_point_interieur():
    c = carre(-7.6, 33.5, 0.001)
    assert app._distance_to_polygon_m(-7.5995, 33.5005, c) == 0.0


def test_distance_a_un_bord_proche():
    # Le point GPS d'une entreprise pointe souvent le trottoir : c'est cette
    # distance que le rattrapage de 20 m compare à son seuil.
    c = carre(-7.6, 33.5, 0.001)
    # 0,0001° au sud du bord inférieur ≈ 11,13 m.
    d = app._distance_to_polygon_m(-7.5995, 33.5 - 0.0001, c)
    assert d == pytest.approx(11.13, rel=0.02)
    assert d < app.ROOF_NEARBY_RADIUS_M


def test_un_point_lointain_depasse_le_rayon_de_rattrapage():
    c = carre(-7.6, 33.5, 0.001)
    # ~55 m au sud : au-delà du seuil, on n'attribue pas le bâtiment du voisin.
    d = app._distance_to_polygon_m(-7.5995, 33.5 - 0.0005, c)
    assert d > app.ROOF_NEARBY_RADIUS_M
