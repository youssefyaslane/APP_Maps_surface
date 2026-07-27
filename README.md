# App Maps Surface — Maroc

Application Flask affichant une carte interactive du Maroc : au survol d'un bâtiment, une infobulle affiche sa surface réelle en m², calculée à partir des données [OpenStreetMap](https://www.openstreetmap.org/).

## Fonctionnalités

- Carte du Maroc (Leaflet + tuiles OpenStreetMap), zoomable sur tout le territoire
- Sélecteur de villes (Casablanca, Rabat, Marrakech, Fès, Tanger, Agadir) pour centrer rapidement la carte
- Récupération des bâtiments réels via l'API [Overpass](https://overpass-api.de/) (OSM), avec surface au sol calculée géométriquement
- Survol d'un bâtiment → nom, type, nombre d'étages et surface (m²)
- Cache par tuile (mémoire + disque) et récupération parallélisée pour des temps de réponse rapides
- Préchargement automatique des grandes villes au démarrage du serveur

## Prérequis

- Python 3.10+

## Installation

```bash
python -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

## Lancer l'application

```bash
python -m flask run
```

Puis ouvrir [http://127.0.0.1:5000](http://127.0.0.1:5000) dans un navigateur.

Choisissez une ville dans le menu déroulant ou zoomez/déplacez la carte (niveau de zoom ≥ 16) pour charger les bâtiments d'une zone, puis survolez un bâtiment pour voir sa surface.

## Architecture

```
app.py                  Serveur Flask + logique Overpass/cache/calcul de surface
templates/index.html    Page principale (carte Leaflet)
static/app.js           Logique frontend (chargement des bâtiments, tooltip au survol)
static/style.css        Styles
requirements.txt        Dépendances Python
```

### Backend (`app.py`)

- `GET /` — page principale
- `GET /api/cities` — liste des villes disponibles (nom, centre, zoom)
- `GET /api/buildings?south=&west=&north=&east=` — bâtiments (GeoJSON) dans la zone demandée

Les bâtiments sont récupérés depuis Overpass, découpés en tuiles de grille (`TILE_SIZE_DEG`) pour permettre un cache fin et des requêtes parallèles. La surface de chaque bâtiment est calculée par projection équirectangulaire locale puis formule du lacet (shoelace).

Le cache est doublé en mémoire et sur disque (`tile_cache.json`, ignoré par Git) afin de survivre aux redémarrages du serveur, avec une durée de validité de 30 minutes.

## Notes

- Certains miroirs Overpass publics peuvent être bloqués selon le réseau utilisé (pare-feu d'entreprise, etc.) ; l'app essaie plusieurs miroirs en cascade.
- Les données proviennent de contributions OpenStreetMap : la couverture et la précision varient selon les zones (meilleure en centre-ville, plus partielle en périphérie/zones rurales).
