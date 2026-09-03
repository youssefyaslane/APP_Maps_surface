# Architecture — Maps Surface

Prospection photovoltaïque sur toitures industrielles marocaines.

Ce document décrit **l'état actuel** du projet, **la structure cible**, et **l'ordre**
pour y parvenir. Il est tenu à jour au fil des refontes.

Dernière mise à jour : 1er septembre 2026, après l'ingestion d'OpenStreetMap
et le retrait d'Overture.

---

## 1. Ce que fait l'application

Trois métiers distincts, aujourd'hui mélangés dans `app.py`. Toute l'architecture
qui suit découle de leur séparation.

| Métier | Rôle |
|---|---|
| **Prospecter** | Importer des entreprises, leur rattacher un toit, estimer le potentiel solaire, classer et exporter |
| **Cartographier** | Afficher les toits des prospects ; les sources brutes restent disponibles en arrière-plan |
| **Corriger** | Permettre à un humain de tracer ou rectifier un toit quand les sources échouent |

---

## 2. État actuel

### Code

| Fichier | Lignes | Contenu |
|---|---:|---|
| `app.py` | 1 603 | 20 routes, accès base, Overpass, cascade de toits, tableau de bord |
| `static/app.js` | 948 | Carte, calques, segmentation interactive, fiche entreprise |
| `segmentation.py` | 370 | MobileSAM, cache d'embeddings, sessions interactives |
| `static/dashboard.js` | 218 | Tableau de bord |
| `compute_solar_potential.py` | 170 | Recalcul du potentiel |

### Base — 151 Mo, 290 752 lignes

| Table | Lignes | Taille | Rôle |
|---|---:|---:|---|
| `ms_buildings` | 193 253 | 108 Mo | Empreintes Microsoft, lecture seule |
| `osm_buildings` | 95 006 | 41 Mo | Bâtiments OSM ingérés depuis Geofabrik |
| `companies` | 1 592 | 1,5 Mo | Les prospects — seule table écrite par le calcul |
| `ia_segments` | 900 | 1,0 Mo | Toits produits dans l'app : détections validées, tracés manuels |

### Mesures de fiabilité

Relevées sur échantillons aléatoires de prospects réels, non estimées :

- **63 %** des toits sont rattachés parce que le point GPS est *dans* le polygone
- **37 %** le sont par rattrapage de proximité (20 m, distance médiane 5 m)
- **27 %** des cas où OSM et Microsoft couvrent le même point présentent un écart de surface supérieur à 50 %
- **213 entreprises** partagent leur toit avec au moins une autre (corrigé par `roof_key`)

---

## 3. Les deux décisions structurantes

### A — OpenStreetMap est ingéré, plus interrogé à chaud

**Fait.** Un extrait Geofabrik du Maroc (232 Mo, régénéré quotidiennement)
remplace les appels Overpass : 95 006 bâtiments importés, les 648 `roof_key`
existants tous préservés.

Le recalcul complet passe de **13 à 8 minutes** — pas les quelques secondes
espérées. Overpass n'était pas le seul goulot : la boucle Python
point-dans-polygone domine désormais. Descendre plus bas demande PostGIS
(étape 5). Le gain réel est ailleurs : plus aucune dépendance réseau dans le
calcul, et des résultats reproductibles.

### B — La carte n'affiche que les toits des prospects

1 409 toits utiles au lieu de 290 752 bâtiments. Les couches sources restent
activables, **décochées par défaut**, pour continuer à repérer les grands toits
sans propriétaire connu.

**B dépend de A** : aujourd'hui `roof_key` dit *quel* polygone, pas *où* il est.
Pour un toit OSM, le contour n'est pas en base — il vient d'Overpass à chaque
affichage. Une fois OSM ingéré, la carte des prospects devient une jointure.

---

## 4. Les quatre règles de structure

| | Règle | Conséquence |
|---|---|---|
| **R1** | Le domaine ne connaît ni base ni réseau | Surface, kWc, coefficient de pose : fonctions pures, testables sans rien démarrer |
| **R2** | Une source de bâtiments = un import, pas un appel | Chaque source arrive par un script hors ligne et vit en base ; l'application ne fait que lire |
| **R3** | Aucune logique métier dans une route | La couche HTTP valide, appelle, sérialise. Rien d'autre |
| **R4** | Toute configuration passe par l'environnement | Un seul `config.py`, aucune URL ni identifiant en dur ailleurs |

---

## 5. Vue d'ensemble du système

```
NAVIGATEUR
  Leaflet · toits des prospects (défaut)      Tableau de bord · liste, filtres, export
           sources brutes (à la demande)
        │                                          │
        └───────────── HTTP / JSON ────────────────┘
                          │
FLASK  (conteneur web, port 5000)
  api/            validation des paramètres, sérialisation
  sources/        lecture en base uniquement      ← plus aucun appel externe
  domain/         géométrie, kWc, PVGIS          (fonctions pures)
  segmentation/   MobileSAM, embeddings, sessions
        │                     │
POSTGRESQL (conteneur db)   SERVICES EXTERNES
  companies                   Esri       tuiles satellite — segmentation seule
  buildings  (unifiée)        PVGIS      production kWh — enrichissement
    osm · ms · ia · manual    Nominatim  géocodage — confort

HORS LIGNE  (scripts, jamais dans le chemin critique)
  Geofabrik   extrait OSM du Maroc, 232 Mo, quotidien
  Microsoft   Global ML Building Footprints
```

Après la décision A, **aucun service extérieur n'intervient dans le calcul du
potentiel solaire**. Esri reste nécessaire à la segmentation interactive, mais
c'est une action humaine ponctuelle, pas un traitement de masse.

---

## 6. Backend — arborescence cible

```
app/
  __init__.py          create_app() — assemble les blueprints
  config.py            NOUVEAU — toute la config, par variables d'environnement
  db.py                pool de connexions, schéma, migrations

  domain/              ── fonctions pures, aucune I/O ──
    geometry.py        NOUVEAU — surface, point-dans-polygone, centroïde
    solar.py           kWc, coefficient de pose, espacement des rangées
    pvgis.py           NOUVEAU — production annuelle en kWh

  sources/             ── lecture en base, plus aucun client HTTP ──
    buildings.py       NOUVEAU — accès à la table unifiée, filtré par source
    roofs.py           la cascade : find_roof_at_point()
    prospects.py       NOUVEAU — toits rattachés aux entreprises
    imagery.py         NOUVEAU — métadonnées Esri : date, résolution, disponibilité

  segmentation/        ── MobileSAM, isolé du reste ──
    model.py           chargement du modèle, verrou d'accès concurrent
    embeddings.py      cache LRU de 16 entrées, indexé par tuile
    sessions.py        session interactive multi-points, expiration 15 min

  api/                 ── couche HTTP mince, blueprints Flask ──
    pages.py           2 routes — / et /dashboard
    map.py             7 routes — toits prospects, sources brutes, géocodage
    segment.py         8 routes — session, tracé manuel, suppression
    prospects.py       3 routes — liste, statistiques, exports CSV

scripts/
  import_osm_buildings.py        NOUVEAU — extrait Geofabrik → base
  import_ms_buildings.py
  import_companies.py
  compute_solar_potential.py
  export_unmatched_roofs.py

tests/                 NOUVEAU
  test_geometry.py     surface connue, point-dans-polygone, cas limites
  test_solar.py        kWc, coefficient de pose
  test_roofs.py        ordre de la cascade, rattrapage de proximité
  test_api.py          les 20 routes répondent
```

---

## 7. Frontend — arborescence cible

Modules ES natifs, chargés par `<script type="module">`.
**Aucune étape de build** — ni bundler, ni npm, ni Node.

```
static/js/
  main.js              point d'entrée, câble les modules
  api.js               NOUVEAU — tous les appels fetch réunis ici
  map.js               carte, fonds de plan, contrôle de calques
  ui.js                infobulles, bandeau de statut, aide contextuelle
  segment.js           session interactive : clic, correction, validation
  layers/
    prospects.js       NOUVEAU — toits des entreprises, activé par défaut
    sources.js         OSM · Microsoft — décochés par défaut
    companies.js       marqueurs et fiche entreprise
    segments.js        toits IA (rouge) et tracés manuels (bleu)
  dashboard/
    main.js  table.js  stats.js  filters.js

static/css/
  tokens.css           NOUVEAU — couleurs, espacements, typographie
  map.css  dashboard.css
```

---

## 8. Base — cinq défauts structurels

| | Défaut | Gravité |
|---|---|---|
| **D1** | La géométrie vit en JSONB, pas en PostgreSQL. Le test point-dans-polygone tourne en Python après chargement de tous les candidats d'un rayon de 300 m | racine |
| **D2** | L'index sur `(lat, lon)` est un btree, pas un index spatial : sur 193 000 lignes, une recherche par emprise lit bien plus que nécessaire | performance |
| **D3** | `roof_key` est une référence polymorphe (`osm:123`) qu'aucune clé étrangère ne protège | intégrité |
| ~~**D4**~~ | ~~Import Microsoft non rejouable~~ | **corrigé** — clé générée `geom_hash` |
| ~~**D5**~~ | ~~OSM interrogé à chaud~~ | **corrigé** — table `osm_buildings` |

---

## 9. Base — schéma cible

Une fois OSM ingéré, les quatre sources ont exactement la même forme. La table
unifiée devient l'évidence.

```sql
-- L'image Docker passe de postgres:16-alpine à postgis/postgis
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE buildings (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,              -- osm | ms | ia | manual
    source_id   TEXT,                       -- way/123, GERS, id du segment…
    geom        geometry(Polygon, 4326) NOT NULL,
    area_m2     DOUBLE PRECISION NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)              -- rend tout import rejouable → D4
);

CREATE INDEX idx_buildings_geom ON buildings USING GIST (geom);   -- → D2

-- La référence devient une vraie clé étrangère → D3
ALTER TABLE companies
    ADD COLUMN building_id       BIGINT REFERENCES buildings(id) ON DELETE SET NULL,
    ADD COLUMN roof_match        TEXT,     -- contained | nearby | none
    ADD COLUMN roof_match_dist_m DOUBLE PRECISION;
```

`roof_match` et `roof_match_dist_m` matérialisent l'indice de confiance : aujourd'hui
rien ne distingue les 63 % de toits réellement contenus des 37 % rattrapés.

### Ce que ça change sur la requête centrale

**Aujourd'hui** : un appel Overpass (réseau) + deux requêtes SQL, puis chargement
de tous les candidats d'un rayon de 300 m en mémoire, puis une boucle Python
point-dans-polygone, puis une seconde boucle pour la distance.

**Cible** :

```sql
SELECT id, source, area_m2
FROM buildings
WHERE ST_Contains(geom, ST_SetSRID(ST_Point(%s, %s), 4326))
ORDER BY CASE source
    WHEN 'manual' THEN 1
    WHEN 'osm'    THEN 2
    WHEN 'ia'     THEN 3
    WHEN 'ms'     THEN 4 END
LIMIT 1;
```

La priorité entre sources cesse d'être une boucle Python pour devenir une clause
`ORDER BY` lisible en une ligne.

---

## 10. Ingestion OpenStreetMap

| | Overpass en direct (actuel) | Extrait ingéré (cible) |
|---|---|---|
| Recalcul complet | 13 min | **quelques secondes** |
| Réseau au moment du calcul | requis | **aucun** |
| Quota, panne, lenteur | subis | sans objet |
| Fraîcheur | 7 jours (cache) | 1 jour au rafraîchissement |
| Reproductibilité | variable | **instantané figé** |

```bash
# 1. Extrait du Maroc — 232 Mo, régénéré quotidiennement par Geofabrik
wget https://download.geofabrik.de/africa/morocco-latest.osm.pbf

# 2. Découpe sur l'emprise utile — inutile d'ingérer tout le pays
osmium extract --bbox=-8.10,33.20,-6.80,34.00 morocco-latest.osm.pbf -o zone.pbf

# 3. Ne garder que les bâtiments
osmium tags-filter zone.pbf w/building -o batiments.pbf

# 4. Convertir au format qu'attend déjà import_ms_buildings.py
osmium export batiments.pbf -f geojsonseq -o batiments.geojsonl

# 5. Ingérer, en conservant l'identifiant OSM pour le roof_key
python -m scripts.import_osm_buildings batiments.geojsonl
```

**Deux points d'attention.** Le volume : Casablanca seule compte 47 000 bâtiments
OSM, découper à l'étape 2 évite d'ingérer le pays entier. Le rafraîchissement
devient une responsabilité : un bâtiment nouvellement cartographié n'apparaîtra
qu'au prochain import — mensuel suffit pour du bâti industriel.

---

## 11. Flux — rattacher un toit

L'opération la plus critique : elle décide quelle surface, donc quel chiffre
d'affaires potentiel, est attribuée à chaque prospect.

1. **Une requête spatiale**, toutes sources confondues, via l'index GiST
2. **Le point est-il dans un polygone ?** Priorité par `ORDER BY` : tracé manuel,
   OSM, détection IA, Microsoft — 63 % des cas
3. **Sinon, rattrapage par proximité (20 m)** — 37 % des cas, médiane 5 m,
   enregistré dans `roof_match` comme moins fiable
4. **Écrire** `building_id`, `roof_match` et la distance

---

## 12. Flux — segmentation interactive

Le coût d'un clic est presque entièrement dans l'encodeur d'image. En gardant
l'embedding en cache, les corrections deviennent quasi instantanées.

| Étape | Route | Durée |
|---|---|---|
| Premier clic — 9 tuiles Esri, encodage, masque initial | `segment/start` | ~6 s |
| Correction — clic gauche étend, clic droit retire | `segment/refine` | **0,2 s** |
| Annulation du dernier point | `segment/undo` | 0,2 s |
| Validation — seul moment où la base est écrite | `segment/commit` | — |

**Deux protections à ne pas perdre au découpage** : un *verrou* sur le prédicteur,
dont `set_image` et `predict` mutent un état partagé (sans lui, deux requêtes
simultanées se mélangent) ; et un *cache LRU de 16 embeddings* indexé par tuile,
qui fait partager le calcul entre deux clics sur le même bâtiment.

---

## 13. Flux — potentiel solaire

```
surface du toit  →  kWc  →  kWh/an  →  dirhams
   ±30% ⚠           exact    ±5%       tarif client

surface × coefficient de pose ÷ 1,7 m² × 400 W        = kWc
kWc × productible PVGIS (1 720 kWh/kWc/an Casablanca) = kWh/an
```

La précision finale ne dépend jamais de la physique, mais uniquement de la
justesse de la surface. Une erreur de 30 % sur le toit donne 30 % d'erreur sur
les dirhams annoncés.

**Le coefficient de pose.** La constante actuelle de `0.7` ne correspond à aucune
configuration réelle. Sur toiture-terrasse : **100 %** de couverture à plat,
**51 %** sur châssis inclinés à 30° — l'espacement anti-ombrage consomme le reste.
Selon la pose retenue, les kWc actuels sont sous-estimés d'un tiers ou
surestimés de moitié.

---

## 14. Surface HTTP — 20 routes

| Blueprint | Routes | Rôle |
|---|---|---|
| `pages` | `/`, `/dashboard` | Rendu des deux gabarits |
| `map` | `/api/prospect_roofs` *(nouveau)*, `/api/buildings`, `/api/companies`, `/api/ms_buildings`, `/api/company_roof`, `/api/geocode`, `/api/cities` | Toits des prospects par défaut, sources brutes à la demande |
| `segment` | `/api/segment/{start,refine,undo,commit,cancel}`, `/api/roof_manual`, `/api/ia_segments`, `/api/ia_segments/<id>` (DELETE) | Session interactive, tracé manuel, suppression |
| `prospects` | `/api/prospects`, `/api/prospects.csv`, `/api/unmatched_roofs.csv` | Liste filtrée, statistiques, exports |

Supprimées : `/api/segment_zone` (mode zone) et `/api/overture_buildings`.

---

## 15. Scripts hors application

| Script | Source | Rejouable |
|---|---|---|
| `import_osm_buildings.py` | Extrait Geofabrik, quotidien | oui — `ON CONFLICT (osm_id)` |
| `import_ms_buildings.py` | Microsoft Global ML Footprints | oui — `ON CONFLICT (geom_hash)` |
| `import_companies.py` | Fichiers Excel du scraping | oui — `ON CONFLICT (place_id)` |
| `compute_solar_potential.py` | — | oui — `--all` pour tout reprendre |
| `export_unmatched_roofs.py` | — | lecture seule |

---

## 16. Dépendances externes

Après la décision A, aucune n'intervient plus dans le calcul du potentiel solaire.

| Service | Usage | Moment | Criticité |
|---|---|---|---|
| Geofabrik | Extrait OSM | import hors ligne | nulle |
| Microsoft | Empreintes ML | import hors ligne | nulle |
| Esri World Imagery | Tuiles satellite | segmentation, à la demande | moyenne |
| PVGIS | Production kWh | enrichissement, cachable | basse |
| Nominatim | Géocodage | confort | basse |
| MobileSAM | Poids du modèle | premier démarrage | **haute** |

L'application doit **refuser de segmenter** quand la tuile Esri est absente,
plutôt que d'analyser une dalle grise. Ce cas existe : au moins un prospect de
18 173 m² est dans une zone sans imagerie.

---

## 17. Déploiement

```
docker compose
  web   python:3.12-slim · Flask · port 5000
        volume  cache_data → /app/cache     (poids MobileSAM, caches disque)
        torch/torchvision en version CPU    (index PyTorch dédié)
        libgl1 + libglib2.0-0               (requis par opencv-headless)

  db    postgis/postgis                     (remplace postgres:16-alpine)
        volume  pg_data → /var/lib/postgresql/data
```

**Trois points à corriger avant une mise en production réelle** : le serveur tourne
avec le serveur de développement Flask (remplacer par Gunicorn) ; les identifiants
de base sont en clair dans `docker-compose.yml` ; le port 5000 est exposé sans
reverse proxy ni TLS.

---

## 18. Portabilité

> **Aujourd'hui le projet n'est pas reproductible.** Sur neuf dépendances, quatre
> seulement portent une version. Et `git+https://github.com/ChaoningZhang/MobileSAM.git`
> ne pointe sur aucun commit : si ce dépôt change, la construction casse ou se
> comporte autrement, sans avertissement. Deux machines peuvent produire deux
> applications différentes à partir du même code.

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `DATABASE_URL` | `postgresql://maps:maps@db:5432/maps` | Base PostgreSQL |
| `CACHE_DIR` | `/app/cache` | Poids du modèle, caches disque |
| `SOLAR_PANEL_POWER_W` | `400` | Puissance unitaire d'un panneau |
| `SOLAR_MOUNTING` | `flat` \| `tilted` | Détermine le coefficient de couverture |
| `PVGIS_ENABLED` | `true` | Enrichissement production kWh |

`DATABASE_URL` vaut aujourd'hui `db:5432` dans l'application mais `localhost:5432`
dans les scripts. Cette divergence disparaît avec `config.py`.

---

## 19. Ordre d'exécution

La numérotation est un ordre réel : chaque étape suppose les précédentes.

| # | Étape | Risque | Effet |
|---:|---|---|---|
| ~~0~~ | ~~Retirer le mode zone, la zone de test, la couche Overture~~ | — | **fait** — 393 lignes supprimées |
| 1 | Ajouter `ON CONFLICT` à l'import Microsoft | nul | Referme le seul bug latent |
| 2 | Figer les dépendances, épingler MobileSAM sur un commit | nul | Le projet devient reproductible |
| 3 | Nettoyer les fichiers parasites, compléter `.gitignore` | nul | Dépôt propre |
| 4 | Extraire `config.py` et `domain/geometry.py` | faible | Fin des 4 copies de la formule de surface |
| 5 | Passer à PostGIS, créer `buildings`, y migrer les tables | moyen | Cohabite avec l'existant, réversible |
| 6 | **Ingérer OSM** depuis l'extrait Geofabrik *(décision A)* | moyen | Recalcul 13 min → secondes |
| 7 | Basculer la cascade sur `buildings`, comparer les résultats | moyen | Une requête au lieu de trois plus deux boucles |
| 8 | Remplir `building_id` et `roof_match` par un recalcul | recalcul | Indice de confiance disponible |
| 9 | **Couche « toits des prospects »** par défaut *(décision B)* | faible | 1 409 polygones au lieu de 290 752 |
| 10 | Supprimer les anciennes tables et le client Overpass | **non réversible** | Une seule source de vérité |
| 11 | Découper `api/` en blueprints, puis le frontend | moyen | `app.py` réduit à `create_app()` |
| 12 | Poser les tests sur le domaine et les routes | nul | Filet de sécurité |

**Méthode.** Les étapes 5 à 9 laissent l'ancien schéma intact : à tout moment on
revient en arrière. L'étape 7 est la garantie — on vérifie que la nouvelle requête
rattache *exactement les mêmes toits* que l'ancienne avant de rien supprimer à
l'étape 10. Rien n'est déplacé et modifié dans le même geste : on déplace, on
vérifie, puis on améliore.

---

## Annexe — décisions déjà tranchées

Pour éviter de rouvrir des questions déjà mesurées.

| Question | Verdict | Preuve |
|---|---|---|
| Overture remplace-t-il OSM + Microsoft ? | **Non** | Sur 160 592 empreintes de Casablanca : 113 454 de Microsoft, 47 138 d'OSM, **zéro** mélangeant les deux. Overture assemble, il ne fabrique rien |
| Un LLM peut-il produire les masques ? | **Non pour mesurer** | Le modèle suit réellement l'image, mais détoure des îlots et rend une taille différente de celle demandée |
| Peut-on obtenir la hauteur des bâtiments ? | **Non gratuitement** | OSM : absente. Overture : 0,66 %. DEM Copernicus 30 m : testé, 0,6 m pour une aciérie. Google Solar API : ne couvre pas le Maroc |
| Faut-il entraîner un modèle de segmentation ? | **Non** | La visite technique mesure sur place avant devis ; le coefficient `0.7` uniforme ne change pas le classement des prospects |
| Faut-il garder le script d'import Overture ? | **Non** | Table supprimée, couche retirée, et deux scripts font mieux : Geofabrik pour OSM, le dataset Microsoft d'origine pour les empreintes ML. Récupérable dans l'historique Git |
| Le mode zone est-il récupérable ? | **Non** | Retiré. Le raffinement multi-points le remplace : 0,2 s par correction, points positifs et négatifs |
