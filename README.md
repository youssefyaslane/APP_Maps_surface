# App Maps Surface — Maroc

Outil de prospection B2B pour la vente de panneaux solaires au Maroc : identifier les toits d'entreprises, estimer leur potentiel photovoltaïque, et prioriser les prospects commerciaux.

L'application combine une **carte interactive** (surface des bâtiments au survol, à partir d'[OpenStreetMap](https://www.openstreetmap.org/), du dataset Microsoft, ou d'une détection IA par MobileSAM) et un **tableau de bord commercial** (`/dashboard`) listant les entreprises classées par puissance installable, avec export CSV pour les équipes de vente.

## Fonctionnalités

- Carte du Maroc (Leaflet), avec bascule entre vue **Plan** (tuiles OpenStreetMap) et vue **Satellite** (imagerie Esri), zoom jusqu'au niveau 22
- Sélecteur de villes (Casablanca, Rabat, Marrakech, Fès, Tanger, Agadir) pour centrer rapidement la carte
- **Barre de recherche** : coordonnées (`lat, lon`) pour un déplacement direct, ou nom de lieu (géocodé via Nominatim/OSM, restreint au Maroc)
- Récupération des bâtiments réels via l'API [Overpass](https://overpass-api.de/) (OSM), avec surface au sol calculée géométriquement — couche activable/désactivable via le contrôle de calques
- Survol d'un bâtiment (contour OSM, en vert) → nom, type, nombre d'étages, surface (m²) et estimation du nombre de panneaux solaires installables
- **Détection IA au clic** : clic n'importe où sur la carte → segmentation du bâtiment visible sur l'imagerie satellite via [MobileSAM](https://github.com/ChaoningZhang/MobileSAM) et calcul de sa surface, même hors couverture OSM (contour affiché en rouge pointillé)
- **Détection IA par zone** : bouton « 🔲 Sélectionner une zone (IA) » → glisser un rectangle sur la carte pour segmenter automatiquement tous les toits visibles dans la zone (sans avoir à cliquer bâtiment par bâtiment)
- **Tracé manuel d'un toit** : bouton « ✏️ Tracer un toit » → placer soi-même les sommets du contour au clic (sans IA), aperçu en direct, puis valider pour calculer la surface et l'enregistrer (affiché en bleu pointillé)
- **Bâtiments IA Microsoft** : couche violette optionnelle basée sur le dataset ouvert [Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints) (détection par IA sur imagerie satellite, complète les zones peu/pas couvertes par OSM)
- **Estimation panneaux solaires** : pour chaque toit (OSM, IA, tracé manuel ou Microsoft), estimation du nombre de panneaux solaires installables et de la puissance correspondante (kWc), affichée au survol
- **Persistance en base (PostgreSQL)** : tous les toits détectés par IA ou tracés manuellement sont sauvegardés, rechargés automatiquement sur la carte au fil de la navigation (survit aux rechargements de page et redémarrages), et supprimables d'un clic (avec confirmation) ; les couches « Bâtiments détectés par IA » (rouge) et « Toits tracés manuellement » (bleu) sont activables/désactivables séparément via le contrôle de calques
- **Entreprises** : import en masse depuis un ou plusieurs exports scraper Google Maps (`.xlsx`, plusieurs formats de colonnes supportés), affichées en marqueurs sur toute la carte (indépendamment du niveau de zoom) ; survol → aperçu rapide ; clic → panneau latéral avec toutes les coordonnées (adresse, téléphone, email, site web, note) ; couche activable/désactivable via le contrôle de calques
- **Code couleur des entreprises** : marqueur **vert** si un toit a été identifié sous l'entreprise (prospect qualifié, présent au tableau de bord), **rouge** si aucun toit n'a été trouvé — ces derniers sont les candidats au tracé manuel. La couleur se met à jour immédiatement après l'ajout ou la suppression d'un toit, sans recharger la page ; l'infobulle affiche la surface et le potentiel solaire, ou l'avertissement « Aucun toit identifié »
- **Liaison entreprise ↔ toit** : à l'ouverture du panneau détaillé d'une entreprise, recherche automatique du toit sous ses coordonnées et affichage de sa surface et de son potentiel solaire directement dans le panneau. Les autres couches sont temporairement masquées pour isoler visuellement le toit concerné (surbrillance jaune), puis restaurées à la fermeture du panneau
- **Tableau de bord commercial** (`/dashboard`) : liste des prospects classés par puissance installable décroissante, statistiques globales, filtres (recherche par nom/adresse, ville, catégorie, puissance minimale), export CSV pour Excel, et lien direct vers chaque toit sur la carte
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

- Choisissez une ville dans le menu déroulant, utilisez la barre de recherche (coordonnées ou nom de lieu), ou zoomez/déplacez la carte (niveau de zoom ≥ 16) pour charger les bâtiments OSM d'une zone, puis survolez un bâtiment pour voir sa surface et son potentiel solaire.
- Basculez en vue **Satellite**, et activez/désactivez les couches (Bâtiments OpenStreetMap, Bâtiments détectés par IA, Toits tracés manuellement, Entreprises, Bâtiments IA Microsoft) via le contrôle de calques en haut à droite de la carte.
- Cliquez n'importe où sur la carte pour lancer une détection IA du bâtiment sous le curseur (quelques secondes de calcul, plus rapide une fois le modèle préchargé).
- Bouton **🔲 Sélectionner une zone (IA)** puis glisser un rectangle pour détecter automatiquement tous les toits de la zone (peut prendre de 30s à plusieurs minutes selon la taille, calcul CPU, sans limite de taille).
- Bouton **✏️ Tracer un toit** puis cliquer les sommets du contour, **✓ Valider** pour enregistrer ou **✗ Annuler** pour effacer.
- Cliquer sur un contour déjà détecté/tracé (rouge ou bleu) pour le supprimer (confirmation demandée).
- Les entreprises importées (marqueurs orange) sont visibles/masquables via le contrôle de calques ; survolez un marqueur pour un aperçu rapide, ou cliquez dessus pour ouvrir le panneau détaillé (adresse, téléphone, email, site web, note, surface du toit et potentiel solaire).
- Bouton **☀️ Prospects solaires** pour ouvrir le tableau de bord commercial (voir plus bas).

### Importer des entreprises

Placez un ou plusieurs exports scraper Google Maps (`.xlsx`) dans le dossier `Data_clients/`. Plusieurs formats de colonnes sont reconnus automatiquement :
- **Exports scraper (anglais)** : `title`, `latitude`/`longitude` (ou `location/lat`/`location/lng`), `category`/`categories/0`, `address`, `city`, `phone`/`phones/0`, `email`/`emails/0`, `website`, `rating`/`totalScore`, `placeId`
- **Format français** : `Nom`, `Latitude`, `Longitude`, `Catégorie`, `Adresse`, `Téléphone`, `Site Web`, `Note`, `Place ID`

Puis lancez :

```bash
python import_companies.py
```

Sans argument, le script importe **tous** les `.xlsx` trouvés dans `Data_clients/` (ou passez un ou plusieurs chemins explicites : `python import_companies.py fichier1.xlsx fichier2.xlsx`). L'import est idempotent : relancer le script met à jour les entreprises déjà importées (dédoublonnage par `placeId`, y compris entre plusieurs fichiers) plutôt que de créer des doublons.

Avec Docker, `Data_clients/` n'étant pas monté en volume (dossier exclu du dépôt), copiez d'abord le fichier dans le conteneur :

```bash
docker compose cp Data_clients/mon_fichier.xlsx web:/app/Data_clients/mon_fichier.xlsx
docker compose exec web python import_companies.py
```

Après un import, lancez `compute_solar_potential.py` pour calculer le potentiel solaire des nouvelles entreprises et les faire apparaître au tableau de bord.

### Importer des bâtiments Microsoft (Global ML Building Footprints)

Pour compléter les zones peu couvertes par OSM avec des empreintes de bâtiments détectées par IA :

1. Téléchargez la [liste des tuiles](https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv) et repérez le(s) `quadkey` couvrant votre zone (`RegionName=Morocco`).
2. Téléchargez et décompressez la tuile correspondante (fichier `.geojsonl`, une Feature GeoJSON par ligne).
3. Importez-la :

```bash
python import_ms_buildings.py chemin/vers/fichier.geojsonl
```

Avec Docker (copier le fichier dans le conteneur d'abord) :

```bash
docker compose cp fichier.geojsonl web:/tmp/fichier.geojsonl
docker compose exec web python import_ms_buildings.py /tmp/fichier.geojsonl
```

Ce dataset ne contient ni nom ni adresse — uniquement la géométrie du toit (aucune notion sémantique de « bâtiment », c'est un modèle de vision par ordinateur).

## Tableau de bord commercial (`/dashboard`)

Accessible depuis la carte (bouton « ☀️ Prospects solaires ») ou directement sur `/dashboard`. Il transforme les données de la carte en **liste de prospects qualifiés** pour les commerciaux :

- Entreprises classées par **puissance installable décroissante** (kWc)
- Statistiques globales : nombre de prospects, potentiel total, panneaux estimés, surface moyenne, et **cibles prioritaires** (≥ 100 kWc, seuil configurable via `BIG_PROSPECT_KWC` dans `app.py`) — cette dernière carte est cliquable pour filtrer directement
- Filtres : recherche par nom/adresse, ville, catégorie, puissance minimale
- **Export CSV** (séparateur `;`, BOM UTF-8 pour Excel) respectant les filtres actifs
- Lien « 🗺️ Voir » par prospect, qui recentre la carte sur son toit

### Calculer le potentiel solaire

Le tableau de bord s'alimente d'un calcul en masse qui, pour chaque entreprise, cherche le toit sous ses coordonnées et en déduit le nombre de panneaux installables :

```bash
docker compose exec web python compute_solar_potential.py
```

Sans argument, seules les entreprises pas encore calculées sont traitées — le script est **interruptible et reprend où il s'est arrêté**. Avec `--all`, tout est recalculé.

Au démarrage, il réinitialise aussi les entreprises dont le toit IA/tracé a été supprimé entre-temps, pour qu'elles soient réévaluées.

**Ordre de priorité des sources** (le premier toit contenant le point de l'entreprise l'emporte) :

1. **OpenStreetMap** — données cartographiées par des humains, les plus fiables
2. **`ia_segments`** — toits détectés par IA ou tracés manuellement dans l'app
3. **Microsoft** — dataset Global ML Building Footprints (détection IA générique)

### Synchronisation automatique

Tracer un toit, le détecter par IA, ou le supprimer met à jour le tableau de bord **immédiatement**, sans relancer le script :

- **Ajout d'un toit** → les entreprises situées dessous reçoivent aussitôt leur potentiel solaire (sauf si elles sont déjà rattachées à un bâtiment OSM, prioritaire)
- **Suppression d'un toit** → les entreprises concernées sont recalculées sur-le-champ (un autre toit peut exister dessous : OSM, Microsoft…)

`compute_solar_potential.py` reste utile après un import en masse de nouvelles entreprises, ou pour un recalcul global.

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
import_companies.py     Import en masse des entreprises depuis un/des export(s) .xlsx vers PostgreSQL
import_ms_buildings.py  Import des empreintes de bâtiments Microsoft (.geojsonl) vers PostgreSQL
compute_solar_potential.py  Calcul en masse du potentiel solaire des entreprises (alimente /dashboard)
templates/index.html    Page principale (carte Leaflet)
templates/dashboard.html    Tableau de bord commercial (liste de prospects)
static/app.js           Logique frontend (couches, tooltip, recherche, clic/zone/tracé IA, entreprises)
static/dashboard.js     Logique du tableau de bord (stats, filtres, tableau, export)
static/style.css        Styles de la carte
static/dashboard.css    Styles du tableau de bord
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
- `GET /api/companies?south=&west=&north=&east=` — entreprises importées dans la zone visible (GeoJSON de points), alimenté par `import_companies.py` ; chaque point porte aussi `has_roof`, `roof_area_m2` et `solar_kwc`, utilisés pour colorer le marqueur (vert/rouge) et alimenter l'infobulle
- `GET /api/ms_buildings?south=&west=&north=&east=` — empreintes de bâtiments Microsoft dans la zone visible (GeoJSON, limité à 3000 résultats), alimenté par `import_ms_buildings.py`
- `GET /api/company_roof?lon=&lat=` — cherche le toit (OSM, toit détecté/tracé, ou bâtiment Microsoft) contenant ces coordonnées (test point-dans-polygone par ray casting sur les candidats dans un rayon de ~300m), retourne sa surface, sa source et son polygone
- `GET /api/geocode?q=` — géocode un nom de lieu via Nominatim/OSM (restreint au Maroc), utilisé par la barre de recherche
- `GET /dashboard` — tableau de bord commercial
- `GET /api/prospects?min_kwc=&city=&category=&search=&limit=` — prospects avec leur potentiel solaire, triés par puissance décroissante, accompagnés des statistiques globales (tous les filtres sont optionnels ; sans `limit`, tous les prospects sont retournés)
- `GET /api/prospects.csv?...` — même liste au format CSV (séparateur `;`, BOM UTF-8 pour Excel), mêmes filtres

Les bâtiments OSM sont récupérés depuis Overpass, découpés en tuiles de grille (`TILE_SIZE_DEG`) pour permettre un cache fin et des requêtes parallèles. La surface de chaque bâtiment (OSM, détecté par IA, ou tracé manuellement) est calculée par projection équirectangulaire locale puis formule du lacet (shoelace).

La table `companies` porte aussi le résultat du calcul de potentiel solaire (`roof_area_m2`, `roof_source`, `solar_panels`, `solar_kwc`, `solar_computed_at`), alimenté par `compute_solar_potential.py` et tenu à jour automatiquement à chaque ajout/suppression de toit.

Le cache des bâtiments OSM est doublé en mémoire et sur disque (`tile_cache.json`), avec une durée de validité de 30 minutes. Il est **partagé** entre la carte, la recherche de toit d'entreprise et le calcul en masse : les entreprises d'une même zone (tuiles de 0,03°, ~3 km) se partagent les mêmes données OSM, ce qui accélère fortement `compute_solar_potential.py` (~13× mesuré). Un verrou par tuile évite que plusieurs recherches simultanées déclenchent chacune leur propre appel Overpass. Les toits détectés par IA ou tracés manuellement sont persistés indéfiniment dans la table PostgreSQL `ia_segments` (colonne `source` = `ia-segmentation` ou `manual-trace`), avec dédoublonnage par proximité de centroïde. Le chemin de cache local (bâtiments OSM et poids du modèle IA) est configurable via la variable d'environnement `CACHE_DIR` (par défaut : racine du projet).

### Segmentation IA (`segmentation.py`)

Au clic, une grille de tuiles satellite Esri (haute résolution, zoom 19) est assemblée autour du point cliqué, puis [MobileSAM](https://github.com/ChaoningZhang/MobileSAM) (version allégée de Segment Anything, ~40 Mo, tourne sur CPU) segmente la forme sous le point. Pour la sélection de zone, `SamAutomaticMaskGenerator` (grille de 20×20 points d'échantillonnage) détecte automatiquement toutes les formes de la zone. Deux filtres écartent ensuite les faux positifs :

- **Taille plausible** (15–4000 m²)
- **Couleur moyenne du masque** : rejet de la végétation (teinte verte) et de l'asphalte (gris sombre peu saturé) — routes, parkings, jardins. Seuils ajustables en tête de `segmentation.py` (`VEGETATION_HUE_RANGE`, `ASPHALT_MAX_VALUE`…)

Le masque retenu est converti en polygone géoréférencé et sa surface est calculée.

La densité de la grille de points (`points_per_side`) est le principal levier de compromis précision/vitesse sur CPU : plus dense détecte plus de petits toits mais ralentit fortement (le coût augmente au carré du paramètre) ; augmenter le niveau de zoom des tuiles seul n'améliore pas la détection sans densifier la grille en proportion, car il faut alors plus de tuiles pour couvrir la même zone.

**Limites à connaître** : le modèle segmente une forme visuellement cohérente, pas spécifiquement un « bâtiment » — en tissu urbain dense (toits accolés) il peut regrouper plusieurs bâtiments en un seul contour, et en mode zone il peut aussi capter des cours, terrains de sport ou parkings (aucune notion sémantique de « bâtiment »). Aucun fine-tuning spécifique aux toits marocains n'a été effectué ; c'est un modèle généraliste. Le tracé manuel permet de contourner ces limites quand la précision importe.

## Notes

- Certains miroirs Overpass publics peuvent être bloqués selon le réseau utilisé (pare-feu d'entreprise, etc.) ; l'app essaie plusieurs miroirs en cascade.
- Les données OSM proviennent de contributions collaboratives : la couverture et la précision varient selon les zones (meilleure en centre-ville, plus partielle en périphérie/zones rurales) — la détection IA vise à combler ces zones non cartographiées.
- Le premier clic pour la détection IA après démarrage du serveur peut être plus lent (téléchargement du modèle + chargement en mémoire) ; les clics suivants sont plus rapides.
- La sélection de zone n'a pas de limite de taille : une très grande zone peut prendre plusieurs minutes à analyser (calcul CPU). Le serveur Flask tourne en mode `threaded=True` afin qu'une détection de zone longue ne bloque pas les autres requêtes (chargement des bâtiments, entreprises, etc.) pendant son exécution.
- L'estimation de panneaux solaires suppose des panneaux de 1.7 m² (1.0m × 1.7m), 400 W chacun, sur 70% de la surface du toit (le reste = accès/marges/obstacles) — hypothèses simplificatrices, ajustables dans `static/app.js` (constantes `SOLAR_PANEL_AREA_M2`, `SOLAR_PANEL_POWER_W`, `SOLAR_USABLE_ROOF_FRACTION`), sans tenir compte de l'orientation/inclinaison réelle du toit.
