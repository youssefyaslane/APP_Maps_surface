"""Placement des panneaux solaires sur un toit (calepinage), sans production.

Le toit arrive en polygone lon/lat. On le projette en mètres autour de son
centre, on le tourne pour aligner les rangées sur ses murs (un toit
industriel est presque toujours rectangulaire), puis on pose des rangées de
panneaux, groupées par blocs séparés par une allée d'entretien, et on ne garde
que ceux qui tiennent entièrement dans le toit, à MARGE_M du bord (accès,
sécurité). On essaie les deux sens de pose (panneau
en long ou en large) et les orientations des plus grands murs, et on garde
la pose qui place le plus de panneaux.

Limites : pas de 3D. Les lanterneaux, climatiseurs et ombres ne sont pas
évités ; c'est une estimation pour un premier rendez-vous, pas un plan
d'installation.
"""
import math
import os

import numpy as np

from services.solar import SOLAR_PANEL_AREA_M2, SOLAR_PANEL_POWER_W


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


PANNEAU_LARGEUR_M = _env_float("SOLAR_PANEL_WIDTH_M", 1.0)
PANNEAU_LONGUEUR_M = SOLAR_PANEL_AREA_M2 / PANNEAU_LARGEUR_M     # 2 m² → 1 m x 2 m
ECART_M = _env_float("SOLAR_PANEL_GAP_M", 0.02)                 # joint entre panneaux
MARGE_M = _env_float("SOLAR_ROOF_SETBACK_M", 1.0)                # bande libre le long des bords
# Allée d'entretien entre les blocs de rangées : sans elle, les panneaux
# couvriraient ~90 % d'un toit plat, ce qu'aucune installation ne fait
# (nettoyage, onduleurs, sécurité). 1 m toutes les 2 rangées donne ~70 %,
# la part retenue par l'estimation du tableau de bord.
ALLEE_M = _env_float("SOLAR_AISLE_M", 1.0)
RANGEES_PAR_BLOC = max(1, int(_env_float("SOLAR_ROWS_PER_AISLE", 2)))
MAX_DESSINES = 8000          # au-delà, le navigateur peine : on renvoie le nombre, pas les formes
EPS = 0.01                   # 1 cm : la projection et la rotation laissent les murs penchés de ~1e-4°


def _projeter(polygon):
    """lon/lat → mètres autour du centre ; renvoie aussi la fonction inverse."""
    pts = np.asarray(polygon, dtype=float)
    lon0, lat0 = pts[:, 0].mean(), pts[:, 1].mean()
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    xy = np.column_stack(((pts[:, 0] - lon0) * kx, (pts[:, 1] - lat0) * ky))

    def inverse(m):
        return np.column_stack((m[:, 0] / kx + lon0, m[:, 1] / ky + lat0))

    return xy, inverse


def _angles_candidats(xy, n=3):
    """Orientations des plus longs murs (modulo 90°), les plus longs d'abord."""
    a, b = xy, np.roll(xy, -1, axis=0)
    d = b - a
    longueurs = np.hypot(d[:, 0], d[:, 1])
    angles = np.mod(np.arctan2(d[:, 1], d[:, 0]), math.pi / 2)
    garde = []
    for i in np.argsort(-longueurs):
        if longueurs[i] < 1e-6:
            continue
        if all(abs(angles[i] - g) > math.radians(3) for g in garde):
            garde.append(float(angles[i]))
        if len(garde) >= n:
            break
    return garde or [0.0]


def _rotation(xy, angle):
    c, s = math.cos(angle), math.sin(angle)
    return xy @ np.array([[c, -s], [s, c]])     # tourne de -angle : le mur devient horizontal


def _dedans(points, poly):
    """Pour chaque point (N,2) : vrai s'il est dans le polygone (règle pair-impair)."""
    x, y = points[:, 0][:, None], points[:, 1][:, None]
    x1, y1 = poly[:, 0][None, :], poly[:, 1][None, :]
    x2, y2 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    croise = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / np.where(y2 - y1 == 0, 1e-12, y2 - y1) + x1)
    return croise.sum(axis=1) % 2 == 1


def _distance_bord(points, poly):
    """Distance de chaque point (N,2) au bord le plus proche du polygone."""
    a = poly[None, :, :]
    b = np.roll(poly, -1, axis=0)[None, :, :]
    p = points[:, None, :]
    ab = b - a
    t = np.clip(((p - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
    proj = a + t[..., None] * ab
    return np.sqrt(((p - proj) ** 2).sum(-1)).min(axis=1)


def _poser(poly, largeur, longueur):
    """Panneaux (coin bas-gauche x, y) qui tiennent dans le polygone déjà tourné."""
    xmin, ymin = poly.min(axis=0) + MARGE_M
    xmax, ymax = poly.max(axis=0) - MARGE_M
    if xmax - xmin < largeur - EPS or ymax - ymin < longueur - EPS:
        return np.empty((0, 2))
    xs = np.arange(xmin, xmax - largeur + EPS, largeur + ECART_M)
    # Rangées parallèles au plus long mur (axe x après redressement), groupées
    # par RANGEES_PAR_BLOC et séparées par une allée.
    ys, y, k = [], ymin, 0
    while y + longueur <= ymax + EPS:
        ys.append(y)
        k += 1
        y += longueur + (ALLEE_M if k % RANGEES_PAR_BLOC == 0 else ECART_M)
    ys = np.array(ys)
    if not len(xs) or not len(ys):
        return np.empty((0, 2))
    gx, gy = np.meshgrid(xs, ys)
    coins = np.column_stack((gx.ravel(), gy.ravel()))
    # Quatre coins, milieux des côtés et centre : un panneau à cheval sur un
    # renfoncement du toit a au moins un de ces points dehors. Par paquets,
    # pour qu'un très grand toit au contour détaillé ne sature pas la mémoire.
    garde = np.zeros(len(coins), dtype=bool)
    paquet = max(500, 2_000_000 // max(len(poly), 1))
    for debut in range(0, len(coins), paquet):
        bloc = coins[debut:debut + paquet]
        ok_bloc = np.ones(len(bloc), dtype=bool)
        for fx in (0.0, 0.5, 1.0):
            for fy in (0.0, 0.5, 1.0):
                idx = np.flatnonzero(ok_bloc)
                if not len(idx):
                    break
                pts = bloc[idx] + np.array([fx * largeur, fy * longueur])
                ok = _dedans(pts, poly)
                if fx in (0.0, 1.0) or fy in (0.0, 1.0):
                    ok[ok] = _distance_bord(pts[ok], poly) >= MARGE_M - EPS
                ok_bloc[idx[~ok]] = False
        garde[debut:debut + paquet] = ok_bloc
    return coins[garde]


def _rangees(coins, larg):
    """Panneaux contigus d'une même rangée regroupés : [(x0, y, nombre)], dans
    l'ordre de pose (rangée par rangée)."""
    pas = larg + ECART_M
    out = []
    for y in np.unique(np.round(coins[:, 1], 6)):
        xs = np.sort(coins[np.abs(coins[:, 1] - y) < 1e-6, 0])
        debut, n = xs[0], 1
        for prec, x in zip(xs[:-1], xs[1:]):
            if abs(x - prec - pas) < 1e-3:
                n += 1
            else:
                out.append((debut, y, n))
                debut, n = x, 1
        out.append((debut, y, n))
    return out


def calepiner(polygon, avec_formes=True):
    """Panneaux posés sur le toit.

    {"panneaux": n, "kwc": …, "formes": [[[lat, lon] x4], …], "rangees": [...]}
    Jusqu'à MAX_DESSINES panneaux, chacun est renvoyé (« formes »). Au-delà,
    on renvoie des rangées : un bloc par suite de panneaux contigus, avec son
    origine, le pas d'un panneau et son nombre, de quoi en dessiner une partie
    quand la barre de réglage en retire. Un grand toit en compte quelques
    centaines au lieu de dizaines de milliers de panneaux."""
    vide = {"panneaux": 0, "kwc": 0.0, "formes": [], "rangees": [], "dessines": True}
    if not polygon or len(polygon) < 3:
        return vide
    xy, inverse = _projeter(polygon)
    meilleur = (np.empty((0, 2)), 0.0, PANNEAU_LARGEUR_M, PANNEAU_LONGUEUR_M)
    for angle in _angles_candidats(xy):
        tourne = _rotation(xy, angle)
        for larg, long_ in ((PANNEAU_LARGEUR_M, PANNEAU_LONGUEUR_M), (PANNEAU_LONGUEUR_M, PANNEAU_LARGEUR_M)):
            coins = _poser(tourne, larg, long_)
            if len(coins) > len(meilleur[0]):
                meilleur = (coins, angle, larg, long_)
    coins, angle, larg, long_ = meilleur
    n = int(len(coins))
    res = dict(vide, panneaux=n, kwc=round(n * SOLAR_PANEL_POWER_W / 1000, 1))
    if not avec_formes or n == 0:
        return res
    c, s = math.cos(angle), math.sin(angle)
    retour = np.array([[c, s], [-s, c]])                   # tourne de +angle : retour au repère du toit

    def en_latlon(pts_m):
        return [[round(float(la), 7), round(float(lo), 7)] for lo, la in inverse(np.asarray(pts_m) @ retour)]

    if n <= MAX_DESSINES:
        rect = np.array([[0, 0], [larg, 0], [larg, long_], [0, long_]])
        tous = (coins[:, None, :] + rect[None, :, :]).reshape(-1, 2)
        lonlat = inverse(tous @ retour).reshape(n, 4, 2)
        res["formes"] = [[[round(float(la), 7), round(float(lo), 7)] for lo, la in panneau] for panneau in lonlat]
        return res
    res["dessines"] = False
    pas = larg + ECART_M
    for x0, y, k in _rangees(coins, larg):
        x1 = x0 + k * pas - ECART_M
        o, fin, haut = en_latlon([(x0, y), (x0 + pas, y), (x0, y + long_)])
        res["rangees"].append({
            "coins": en_latlon([(x0, y), (x1, y), (x1, y + long_), (x0, y + long_)]),
            "pas": [round(fin[0] - o[0], 9), round(fin[1] - o[1], 9)],   # un panneau le long de la rangée
            "n": int(k),
        })
    return res
