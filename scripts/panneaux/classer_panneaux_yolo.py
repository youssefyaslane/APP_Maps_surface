"""Étape 2 de la détection des panneaux : oui ou non, toit par toit (modèle YOLO).

Tourne DANS un conteneur jetable et coupé d'Internet (scripts/panneaux/yolo_conteneur.sh).
Modèle : Cyrille37/solar-panels-IGN-bdortho (MIT), photos IGN. Rien n'est écrit
sur le disque : ni image, ni résultat.

Chaque image est découpée en morceaux de 960 px (environ 240 m) ramenés à 640 px,
la taille d'entraînement du modèle : il voit alors environ 37 cm/pixel. Mesuré
sur Batifer, sa confiance monte quand on réduit (morceaux de 512 px : 0,55 ;
640 : 0,65 ; 800 : 0,75 ; 960 : 0,83) sans faux « oui » sur les toits sans
panneaux : il a sans doute appris sur des tuiles IGN plus grandes, réduites à
640 px. Sans découpage, YOLO réduirait un grand toit entier à 640 px et ses
panneaux deviendraient invisibles. CHIP=... change la taille des morceaux.
Seuls comptent les panneaux dont le centre tombe dans le contour du toit.

Deux usages :
  python classer_panneaux_yolo.py --flux
      lit les toits envoyés par extraire_toits_panneaux.py sur l'entrée
      standard, écrit un verdict JSON par toit sur la sortie standard (vers
      importer_panneaux.py) et affiche l'avancement sur la sortie d'erreur ;
  python classer_panneaux_yolo.py <dossier_images>
      essai sur des images (tests/panneaux/img) : si le nom contient
      « avec_panneaux » ou « sans_panneaux », la réponse est notée.

Un toit est « oui » si au moins un cadre détecté passe la règle des pixels
sombres (voir confirmes), « non » sinon.
Le verdict affiché utilise SEUIL (0,25, choisi en vérifiant à l'œil les toits
de chaque tranche de confiance) ; toutes les confiances sont transmises, pour
que le seuil puisse changer sans relancer la détection.
"""
import io
import json
import os
import struct
import sys
import time

import numpy as np
import torch
from PIL import Image, ImageDraw
from torchvision.ops import nms
from ultralytics import YOLO

MODELE = "/modele.pt"
SEUIL = float(os.environ.get("SEUIL", "0.25"))
ENTREE = 640                                  # taille d'entraînement du modèle
CHIP = int(os.environ.get("CHIP", "960"))     # morceau, en pixels d'origine (voir plus haut)
ECHELLE = ENTREE / CHIP
PAS = CHIP * 7 // 8                           # recouvrement pour ne pas couper un panneau
LOT = 8                                       # morceaux passés ensemble au modèle


def positions(taille):
    if taille <= CHIP:
        return [0]
    pos = list(range(0, taille - CHIP, PAS))
    return pos + [taille - CHIP]


def detect(model, img):
    """Cadres (x0, y0, x1, y1, confiance) en pixels de l'image d'origine."""
    w, h = img.size
    if w < CHIP or h < CHIP:  # petit toit : on complète en noir jusqu'à un morceau entier
        fond = Image.new("RGB", (max(w, CHIP), max(h, CHIP)))
        fond.paste(img, (0, 0))
        img = fond
    morceaux, origines = [], []
    for y in positions(img.height):
        for x in positions(img.width):
            morceaux.append(img.crop((x, y, x + CHIP, y + CHIP)).resize((ENTREE, ENTREE), Image.LANCZOS))
            origines.append((x, y))
    cadres, scores = [], []
    for i in range(0, len(morceaux), LOT):
        for res, (ox, oy) in zip(model.predict(morceaux[i:i + LOT], conf=0.05, verbose=False), origines[i:i + LOT]):
            for (x0, y0, x1, y1), c in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist()):
                cadres.append([ox + x0 / ECHELLE, oy + y0 / ECHELLE, ox + x1 / ECHELLE, oy + y1 / ECHELLE])
                scores.append(c)
    if not cadres:
        return []
    b, s = torch.tensor(cadres), torch.tensor(scores)
    garde = nms(b, s, 0.5)  # un même panneau vu dans deux morceaux qui se recouvrent
    return [(*b[i].tolist(), float(s[i])) for i in garde if b[i][0] < w and b[i][1] < h]


def dans_contour(cadres, contour, taille):
    if not contour:
        return cadres
    masque = Image.new("L", taille, 0)
    ImageDraw.Draw(masque).polygon([tuple(p) for p in contour], fill=1)
    m = np.array(masque)
    garde = []
    for x0, y0, x1, y1, c in cadres:
        cx, cy = int((x0 + x1) / 2), int((y0 + y1) / 2)
        if 0 <= cy < m.shape[0] and 0 <= cx < m.shape[1] and m[cy, cx]:
            garde.append((x0, y0, x1, y1, c))
    return garde


# Règle maison contre les fausses détections : un panneau est sombre. Un cadre
# « confirmé » a au moins PART_SOMBRE de ses pixels plus sombres que
# max(90, 0,62 x luminosité médiane du toit) — le second terme rattrape les
# images surexposées, où des panneaux paraissent gris moyen sur un toit très
# clair (Batifer). Réglée sur les 20 toits détectés, vérifiés à l'œil : elle
# garde 12 vrais sur 13 et écarte 5 erreurs sur 7 (tôle claire, verrière
# claire), mais la marge est mince et elle laisse passer un stade et un dôme
# vitré, sombres eux aussi. Un toit détecté mais non confirmé est « non » ;
# ses confiances brutes restent en base (scores), pour pouvoir revoir la règle.
PART_SOMBRE = 0.45
LUM_SOMBRE = 90.0
LUM_RELATIVE = 0.62


def confirmes(img, contour, cadres):
    """Les cadres assez sombres pour être des panneaux."""
    if not cadres:
        return []
    lum = np.asarray(img, dtype=np.float32).mean(axis=2)
    if contour:
        masque = Image.new("L", img.size, 0)
        ImageDraw.Draw(masque).polygon([tuple(p) for p in contour], fill=1)
        toit = np.array(masque, dtype=bool)
    else:
        toit = np.ones(lum.shape, dtype=bool)
    seuil = max(LUM_SOMBRE, LUM_RELATIVE * float(np.median(lum[toit]))) if toit.any() else LUM_SOMBRE
    garde = []
    for cadre in cadres:
        x0, y0, x1, y1 = max(int(cadre[0]), 0), max(int(cadre[1]), 0), min(int(cadre[2]), img.width), min(int(cadre[3]), img.height)
        if x1 > x0 and y1 > y0 and float((lum[y0:y1, x0:x1] < seuil).mean()) >= PART_SOMBRE:
            garde.append(cadre)
    return garde


def verdict(model, img, contour):
    cadres = dans_contour(detect(model, img), contour, img.size)
    scores = sorted((round(c[4], 3) for c in cadres), reverse=True)
    sombres = sorted((round(c[4], 3) for c in confirmes(img, contour, cadres)), reverse=True)
    nb = sum(1 for sc in scores if sc >= SEUIL)
    oui = any(sc >= SEUIL for sc in sombres)
    return {"panneaux": "oui" if oui else "non", "nb": nb if oui else 0,
            "confiance": sombres[0] if sombres else 0.0,
            "scores": scores, "confiance_sombre": sombres[0] if sombres else 0.0}


def lire_exactement(flux, n):
    data = b""
    while len(data) < n:
        morceau = flux.read(n - len(data))
        if not morceau:
            raise EOFError("flux coupé au milieu d'une image")
        data += morceau
    return data


def flux(model):
    entree, sortie = sys.stdin.buffer, sys.stdout
    debut, n, oui = time.time(), 0, 0
    while True:
        ligne = entree.readline()
        if not ligne:
            break
        entete = json.loads(ligne)
        if entete["image"]:
            (taille,) = struct.unpack(">I", lire_exactement(entree, 4))
            img = Image.open(io.BytesIO(lire_exactement(entree, taille))).convert("RGB")
            r = verdict(model, img, entete["contour"])
        else:
            r = {"panneaux": "pas d'image", "nb": 0, "confiance": 0.0, "scores": [], "confiance_sombre": 0.0}
        r["cle"] = entete["cle"]
        sortie.write(json.dumps(r) + "\n")
        sortie.flush()
        n += 1
        oui += r["panneaux"] == "oui"
        vitesse = n / max(time.time() - debut, 1e-6)
        print(f"[{n:4d}] {r['panneaux'].upper():11s} conf {r['confiance']:.2f} ({r['nb']:3d} panneaux)  "
              f"{entete['qui']}   · {oui} équipé(s), {vitesse:.1f} toit/s", file=sys.stderr, flush=True)


def essai(model, dossier):
    noms = sorted(f for f in os.listdir(dossier) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if not noms:
        sys.exit(f"Aucune image dans {dossier}")
    print(f"{len(noms)} image(s), seuil {SEUIL}\n")
    justes = notes = 0
    for nom in noms:
        img = Image.open(os.path.join(dossier, nom)).convert("RGB")
        r = verdict(model, img, None)
        attendu = "oui" if "avec_panneaux" in nom else "non" if "sans_panneaux" in nom else None
        marque = ""
        if attendu:
            notes += 1
            justes += r["panneaux"] == attendu
            marque = "✓" if r["panneaux"] == attendu else "✗"
        print(f"{nom:34s} {r['panneaux'].upper():4s} {marque:2s} conf {r['confiance']:.2f} ({r['nb']} panneaux)")
    if notes:
        print(f"\nRésultat : {justes}/{notes} juste(s) sur les images dont on connaît la réponse.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    modele = YOLO(MODELE)
    if sys.argv[1] == "--flux":
        flux(modele)
    else:
        essai(modele, sys.argv[1])
