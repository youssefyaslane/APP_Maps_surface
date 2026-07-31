# App Maps Surface — Maroc

Application Flask affichant une carte interactive du Maroc : au survol d'un bâtiment, une infobulle affiche sa surface réelle en m², calculée à partir des données [OpenStreetMap](https://www.openstreetmap.org/). Une détection par intelligence artificielle (MobileSAM) permet aussi d'estimer la surface d'un bâtiment n'importe où sur la carte, même s'il n'est pas encore cartographié sur OSM. Les toits détectés (par IA ou tracés manuellement) sont persistés dans PostgreSQL.

## Fonctionnalités

- Carte du Maroc (Leaflet), avec bascule entre vue **Plan** (tuiles OpenStreetMap) et vue **Satellite** (imagerie Esri), zoom jusqu'au niveau 22
- Sélecteur de villes (Casablanca, Rabat, Marrakech, Fès, Tanger, Agadir) pour centrer rapidement la carte
- Récupération des bâtiments réels via l'API [Overpass](https://overpass-api.de/) (OSM), avec surface au sol calculée géométriquement
- Survol d'un bâtiment (contour OSM, en vert) → nom, type, nombre d'étages et surface (m²)
- **Détection IA au clic** : clic n'importe où sur la carte → segmentation du bâtiment visible sur l'imagerie satellite via [MobileSAM](https://github.com/ChaoningZhang/MobileSAM) et calcul de sa surface, même hors couverture OSM (contour affiché en rouge pointillé)
- **Détection IA par zone** : bouton « 🔲 Sélectionner une zone (IA) » → glisser un rectangle sur la carte pour segmenter automatiquement tous les toits visibles dans la zone (sans avoir à cliquer bâtiment par bâtiment)
- **Tracé manuel d'un toit** : bouton « ✏️ Tracer un toit » → placer soi-même les sommets du contour au clic (sans IA), aperçu en direct, puis valider pour calculer la surface et l'enregistrer (affiché en bleu pointillé)
- **Persistance en base (PostgreSQL)** : tous les toits détectés par IA ou tracés manuellement sont sauvegardés, rechargés automatiquement sur la carte au fil de la navigation (survit aux rechargements de page et redémarrages), et supprimables d'un clic (avec confirmation)
- Cache par tuile (mémoire + disque) et récupération parallélisée pour des temps de réponse rapides
- Préchargement automatique des grandes villes et du modèle IA au démarrage du serveur

## Prérequis

- Python 3.10+ et une base PostgreSQL accessible
- ou Docker / Docker Compose (inclut PostgreSQL)

## Installation (locale)

```bash
python -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # macOS/Linux

pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

`torch` est installé séparément (index CPU dédié, plus léger que la version par défaut avec support CUDA).

Définir la variable d'environnement `DATABASE_URL` vers votre instance PostgreSQL (par défaut : `postgresql://maps:maps@db:5432/maps`, adapté à Docker Compose).

## Lancer l'application

```bash
python -m flask run
```

Puis ouvrir [http://127.0.0.1:5000](http://127.0.0.1:5000) dans un navigateur.

- Choisissez une ville dans le menu déroulant ou zoomez/déplacez la carte (niveau de zoom ≥ 16) pour charger les bâtiments OSM d'une zone, puis survolez un bâtiment pour voir sa surface.
- Basculez en vue **Satellite** via le contrôle de calques en haut à droite de la carte.
- Cliquez n'importe où sur la carte pour lancer une détection IA du bâtiment sous le curseur (quelques secondes de calcul, plus rapide une fois le modèle préchargé).
- Bouton **🔲 Sélectionner une zone (IA)** puis glisser un rectangle pour détecter automatiquement tous les toits de la zone (peut prendre de 30s à plusieurs minutes selon la taille, calcul CPU).
- Bouton **✏️ Tracer un toit** puis cliquer les sommets du contour, **✓ Valider** pour enregistrer ou **✗ Annuler** pour effacer.
- Cliquer sur un contour déjà détecté/tracé (rouge ou bleu) pour le supprimer (confirmation demandée).

## Lancer avec Docker

```bash
docker compose up --build
```

Puis ouvrir [http://127.0.0.1:5000](http://127.0.0.1:5000).

`docker-compose.yml` démarre deux services :
- `web` — l'application Flask, avec un volume (`cache_data`) monté sur `/app/cache` afin que le cache des bâtiments OSM (`tile_cache.json`) et les poids du modèle IA (`mobile_sam.pt`, ~40 Mo, téléchargés au premier démarrage) survivent aux redémarrages du conteneur
- `db` — PostgreSQL 16, avec un volume (`pg_data`) pour la persistance des toits détectés/tracés

Pour explorer la base de données :

```bash
docker compose exec db psql -U maps -d maps
```

## Architecture

```
app.py                  Serveur Flask + logique Overpass/cache/calcul de surface + persistance PostgreSQL
segmentation.py         Segmentation IA des bâtiments (extraction imagerie satellite + MobileSAM)
templates/index.html    Page principale (carte Leaflet)
static/app.js           Logique frontend (chargement des bâtiments, tooltip, clic/zone/tracé IA)
static/style.css        Styles
requirements.txt        Dépendances Python (hors torch, installé séparément)
Dockerfile               Image de l'application
docker-compose.yml      Orchestration (web + PostgreSQL) + volumes persistants
```

### Backend (`app.py`)

- `GET /` — page principale
- `GET /api/cities` — liste des villes disponibles (nom, centre, zoom)
- `GET /api/buildings?south=&west=&north=&east=` — bâtiments OSM (GeoJSON) dans la zone demandée
- `GET /api/segment?lon=&lat=` — détection IA du bâtiment sous le point cliqué (GeoJSON + surface), persisté en base
- `GET /api/segment_zone?south=&west=&north=&east=` — détection IA automatique de tous les toits dans la zone (GeoJSON), persisté en base
- `POST /api/roof_manual` — enregistre un toit tracé manuellement (`{"points": [[lon, lat], ...]}`, ≥ 3 points), calcule la surface et persiste en base
- `GET /api/ia_segments?south=&west=&north=&east=` — recharge les toits déjà détectés/tracés dans la zone visible (GeoJSON)
- `DELETE /api/ia_segments/<id>` — supprime un toit détecté/tracé

Les bâtiments OSM sont récupérés depuis Overpass, découpés en tuiles de grille (`TILE_SIZE_DEG`) pour permettre un cache fin et des requêtes parallèles. La surface de chaque bâtiment (OSM, détecté par IA, ou tracé manuellement) est calculée par projection équirectangulaire locale puis formule du lacet (shoelace).

Le cache des bâtiments OSM est doublé en mémoire et sur disque (`tile_cache.json`), avec une durée de validité de 30 minutes. Les toits détectés par IA ou tracés manuellement sont persistés indéfiniment dans la table PostgreSQL `ia_segments` (colonne `source` = `ia-segmentation` ou `manual-trace`), avec dédoublonnage par proximité de centroïde. Le chemin de cache local (bâtiments OSM et poids du modèle IA) est configurable via la variable d'environnement `CACHE_DIR` (par défaut : racine du projet).

### Segmentation IA (`segmentation.py`)

Au clic, une grille de tuiles satellite Esri (haute résolution, zoom 19) est assemblée autour du point cliqué, puis [MobileSAM](https://github.com/ChaoningZhang/MobileSAM) (version allégée de Segment Anything, ~40 Mo, tourne sur CPU) segmente la forme sous le point. Pour la sélection de zone, `SamAutomaticMaskGenerator` détecte automatiquement toutes les formes de la zone, filtrées par taille plausible (15–4000 m²) pour ne garder que des toits probables. Le masque obtenu est converti en polygone géoréférencé et sa surface est calculée.

**Limites à connaître** : le modèle segmente une forme visuellement cohérente, pas spécifiquement un « bâtiment » — en tissu urbain dense (toits accolés) il peut regrouper plusieurs bâtiments en un seul contour, et en mode zone il peut aussi capter des cours, terrains de sport ou parkings (aucune notion sémantique de « bâtiment »). Aucun fine-tuning spécifique aux toits marocains n'a été effectué ; c'est un modèle généraliste. Le tracé manuel permet de contourner ces limites quand la précision importe.

## Notes

- Certains miroirs Overpass publics peuvent être bloqués selon le réseau utilisé (pare-feu d'entreprise, etc.) ; l'app essaie plusieurs miroirs en cascade.
- Les données OSM proviennent de contributions collaboratives : la couverture et la précision varient selon les zones (meilleure en centre-ville, plus partielle en périphérie/zones rurales) — la détection IA vise à combler ces zones non cartographiées.
- Le premier clic pour la détection IA après démarrage du serveur peut être plus lent (téléchargement du modèle + chargement en mémoire) ; les clics suivants sont plus rapides.
- La sélection de zone n'a pas de limite de taille : une très grande zone peut prendre plusieurs minutes à analyser (calcul CPU).
