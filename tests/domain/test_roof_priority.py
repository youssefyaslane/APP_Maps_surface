"""Ordre de priorité des sources de toiture.

Règle métier, pas détail d'implémentation : la surface retenue pour une
entreprise — donc sa puissance installable, donc son rang au tableau de bord —
dépend de la source choisie quand plusieurs bâtiments se superposent. Un
retour silencieux à « OSM d'abord » ferait varier des chiffres commerciaux
sans qu'aucun test ne tombe.

`_find_roof_at_point` interroge la base et le cache Overpass : les trois
sources sont remplacées ici, ce qui laisse tester l'arbitrage seul, sans base
ni réseau.
"""
import pytest

import app as app_module

# Carré de 100 m de côté environ, centré sur (0, 0) : contient (0, 0).
CARRE_CENTRE = [(-0.0005, -0.0005), (0.0005, -0.0005), (0.0005, 0.0005), (-0.0005, 0.0005)]
# Même forme, décalée : ne contient pas (0, 0) mais reste dans le rayon de repli.
CARRE_VOISIN = [(0.0010, 0.0010), (0.0020, 0.0010), (0.0020, 0.0020), (0.0010, 0.0020)]


def _pose_sources(monkeypatch, osm=(), segments=(), ms=()):
    monkeypatch.setattr(app_module, "_cached_osm_buildings_at", lambda bbox: list(osm))
    monkeypatch.setattr(app_module, "_query_ia_segments", lambda bbox: list(segments))
    monkeypatch.setattr(app_module, "_query_ms_buildings", lambda bbox: list(ms))


def _osm(id_, polygon, area):
    return {
        "id": id_,
        "properties": {"area_m2": area},
        "geometry": {"coordinates": [polygon]},
    }


def _segment(id_, polygon, area, source):
    return {"id": id_, "polygon": polygon, "area_m2": area, "source": source}


TOUTES_LES_SOURCES = dict(
    osm=[_osm(1, CARRE_CENTRE, 100.0)],
    segments=[
        _segment(2, CARRE_CENTRE, 200.0, "ia-segmentation"),
        _segment(3, CARRE_CENTRE, 300.0, "manual-trace"),
    ],
    ms=[{"id": 4, "polygon": CARRE_CENTRE, "area_m2": 400.0}],
)


@pytest.mark.parametrize(
    "retirees, source_attendue, surface_attendue",
    [
        ((), "manual-trace", 300.0),
        (("manual",), "ia-segmentation", 200.0),
        (("manual", "ia"), "osm", 100.0),
        (("manual", "ia", "osm"), "ms-buildings", 400.0),
    ],
)
def test_priorite_des_sources(monkeypatch, retirees, source_attendue, surface_attendue):
    """Quatre polygones superposés : la source la plus fiable disponible gagne.

    La surface volontairement différente d'une source à l'autre sert de preuve
    que c'est bien le bon polygone qui ressort, et pas seulement le bon libellé.
    """
    segments = list(TOUTES_LES_SOURCES["segments"])
    osm = list(TOUTES_LES_SOURCES["osm"])
    if "manual" in retirees:
        segments = [s for s in segments if s["source"] != "manual-trace"]
    if "ia" in retirees:
        segments = [s for s in segments if s["source"] == "manual-trace"]
    if "osm" in retirees:
        osm = []

    _pose_sources(monkeypatch, osm=osm, segments=segments, ms=TOUTES_LES_SOURCES["ms"])

    toit = app_module._find_roof_at_point(0.0, 0.0)
    assert toit["source"] == source_attendue
    assert toit["area_m2"] == surface_attendue


def test_tracé_manuel_gagne_meme_plus_loin(monkeypatch):
    """Repli « bâtiment le plus proche » : la priorité prime sur la distance.

    Sans cette règle, un tracé manuel fait exprès pour corriger un contour
    serait ignoré dès qu'un polygone OSM se trouve quelques mètres plus près
    du point GPS de l'entreprise.
    """
    _pose_sources(
        monkeypatch,
        osm=[_osm(1, CARRE_CENTRE, 100.0)],
        segments=[_segment(3, CARRE_VOISIN, 300.0, "manual-trace")],
    )

    # Point hors de tout polygone : les deux sources passent par le repli.
    toit = app_module._find_roof_at_point(0.0009, 0.0009)
    assert toit["source"] == "manual-trace"


def test_aucune_source_ne_donne_rien(monkeypatch):
    _pose_sources(monkeypatch)
    assert app_module._find_roof_at_point(0.0, 0.0) is None
