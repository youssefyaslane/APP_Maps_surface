"""Tables de l'application — une seule définition, pour le démarrage et la migration.

Le schéma était écrit dans _init_db (app.py), et osm_buildings dans son script
d'import. La page d'administration de la base doit créer ces mêmes tables sur un
autre serveur avant d'y recopier les données : la définition vit donc ici, et
s'applique à n'importe quel curseur, sans dépendre du pool de l'application.

Toutes les instructions sont en IF NOT EXISTS : les rejouer sur une base déjà
prête ne change rien.
"""

# Ordre de copie : une table n'arrive qu'après celles qu'elle référence
# (ia_segments.created_by et audit_log.user_id pointent sur users).
COPY_ORDER = ("users", "companies", "ia_segments", "ms_buildings", "osm_buildings", "audit_log",
              "pv_detections", "crm_notes", "opportunites", "opportunite_etapes", "decideurs", "visites")

# Sans elles, l'application ne fonctionne pas. osm_buildings est facultative :
# tant qu'elle est vide ou absente, les bâtiments viennent d'Overpass.
REQUIRED_TABLES = ("users", "companies", "ia_segments", "ms_buildings", "audit_log")

# Tables à identifiant SERIAL. Une copie qui conserve les identifiants laisse
# leur séquence à 1 : sans recalage, le premier compte ou le premier toit créé
# sur la nouvelle base réutiliserait un identifiant déjà copié.
SERIAL_TABLES = ("users", "companies", "ia_segments", "ms_buildings", "audit_log", "crm_notes",
                 "opportunites", "opportunite_etapes", "decideurs", "visites")

# Clé par laquelle la synchronisation rapproche une ligne de sa copie. `id`
# partout, sauf pour les bâtiments OSM, identifiés par leur numéro OSM, et les
# détections de panneaux, rattachées au toit.
PRIMARY_KEYS = {"osm_buildings": "osm_id", "pv_detections": "roof_key"}


def primary_key(table):
    return PRIMARY_KEYS.get(table, "id")


def create_osm_buildings(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS osm_buildings (
            osm_id BIGINT PRIMARY KEY,
            polygon JSONB NOT NULL,
            holes JSONB,
            area_m2 DOUBLE PRECISION NOT NULL,
            centroid_lon DOUBLE PRECISION NOT NULL,
            centroid_lat DOUBLE PRECISION NOT NULL,
            name TEXT,
            building_type TEXT,
            levels TEXT,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_osm_buildings_centroid "
        "ON osm_buildings (centroid_lat, centroid_lon)"
    )


def create_pv_detections(cur):
    """Panneaux solaires déjà posés, détectés sur l'image satellite du toit
    (scripts/panneaux/classer_panneaux.sh, modèle YOLO). Une ligne par toit, pas par
    entreprise : un toit partagé ne s'examine qu'une fois.

    Toutes les confiances du modèle sont gardées, pas un oui/non figé : le seuil
    d'affichage (PV_SEUIL dans app.py) peut changer sans relancer la détection.
    Pas de clé étrangère : roof_key désigne un polygone de trois tables
    différentes (osm:, ms:, ia:)."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pv_detections (
            roof_key TEXT PRIMARY KEY,
            has_image BOOLEAN NOT NULL,
            scores REAL[] NOT NULL DEFAULT '{}',
            max_score REAL NOT NULL DEFAULT 0,
            model TEXT NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Meilleure confiance parmi les cadres assez sombres pour être des panneaux
    # (règle dans scripts/panneaux/classer_panneaux_yolo.py). Détecté mais non
    # confirmé par cette règle : le toit s'affiche « Non ».
    cur.execute("ALTER TABLE pv_detections ADD COLUMN IF NOT EXISTS dark_score REAL NOT NULL DEFAULT 0")


# Colonnes du CRM ajoutées un temps à companies (9 octobre 2026), avant qu'il
# n'ait ses propres tables.
ANCIENNES_COLONNES_CRM = (
    "crm_statut", "crm_raison_perte", "crm_commercial_id", "crm_pris_le", "crm_relance_le",
    "crm_relance_objet", "crm_maj_le", "decideur_nom", "decideur_fonction", "decideur_telephone",
    "decideur_email", "visite_conso_kwh_an", "visite_etat_toiture", "visite_inclinaison",
    "visite_orientation", "visite_productible",
)


def _migrer_crm_hors_de_companies(cur):
    """Déplace le suivi commercial des anciennes colonnes de companies vers
    ses tables, puis retire ces colonnes. Dans la transaction du démarrage :
    tout passe, ou rien. Sans effet une fois fait (les colonnes n'existent plus)."""
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'companies' AND column_name = 'crm_statut'
        """
    )
    if cur.fetchone() is None:
        return
    cur.execute(
        """
        INSERT INTO opportunites (company_id, commercial_id, statut, raison_perte, relance_le,
                                  relance_objet, pris_le, maj_le)
        SELECT id, crm_commercial_id, COALESCE(crm_statut, 'a_contacter'), crm_raison_perte,
               crm_relance_le, crm_relance_objet, crm_pris_le, COALESCE(crm_maj_le, now())
        FROM companies
        WHERE crm_commercial_id IS NOT NULL OR crm_statut IS NOT NULL OR crm_relance_le IS NOT NULL
        ON CONFLICT (company_id) DO NOTHING
        """
    )
    # Historique du pipeline : repris du journal (prise, puis chaque changement d'étape).
    cur.execute(
        """
        INSERT INTO opportunite_etapes (opportunite_id, etape, raison_perte, user_id, le)
        SELECT o.id,
               CASE WHEN a.action = 'crm_pris' THEN 'a_contacter' ELSE a.details->>'vers' END,
               CASE WHEN a.action = 'crm_statut' THEN a.details->>'raison' END,
               a.user_id, a.created_at
        FROM audit_log a JOIN opportunites o ON o.company_id = a.entity_id
        WHERE a.entity = 'companies' AND a.action IN ('crm_pris', 'crm_statut')
          AND NOT EXISTS (SELECT 1 FROM opportunite_etapes e WHERE e.opportunite_id = o.id)
        """
    )
    cur.execute(
        """
        INSERT INTO decideurs (company_id, nom, fonction, telephone, email)
        SELECT id, decideur_nom, decideur_fonction, decideur_telephone, decideur_email FROM companies
        WHERE COALESCE(decideur_nom, decideur_fonction, decideur_telephone, decideur_email) IS NOT NULL
        """
    )
    cur.execute(
        """
        INSERT INTO visites (company_id, conso_kwh_an, etat_toiture, inclinaison, orientation, productible)
        SELECT id, visite_conso_kwh_an, visite_etat_toiture, visite_inclinaison, visite_orientation,
               visite_productible
        FROM companies
        WHERE COALESCE(visite_conso_kwh_an, visite_inclinaison, visite_orientation) IS NOT NULL
           OR visite_etat_toiture IS NOT NULL
        """
    )
    cur.execute(
        "ALTER TABLE companies " + ", ".join(f"DROP COLUMN IF EXISTS {c}" for c in ANCIENNES_COLONNES_CRM)
    )


def create_all(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ia_segments (
            id SERIAL PRIMARY KEY,
            polygon JSONB NOT NULL,
            area_m2 DOUBLE PRECISION NOT NULL,
            centroid_lon DOUBLE PRECISION NOT NULL,
            centroid_lat DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        "ALTER TABLE ia_segments ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'ia-segmentation'"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ia_segments_centroid "
        "ON ia_segments (centroid_lat, centroid_lon)"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            address TEXT,
            city TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            rating DOUBLE PRECISION,
            lon DOUBLE PRECISION NOT NULL,
            lat DOUBLE PRECISION NOT NULL,
            place_id TEXT UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_companies_coords ON companies (lat, lon)"
    )
    # Potentiel solaire calculé par compute_solar_potential.py (toit
    # trouvé sous l'entreprise + estimation panneaux/puissance).
    cur.execute(
        """
        ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS roof_area_m2 DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS roof_source TEXT,
            ADD COLUMN IF NOT EXISTS solar_panels INTEGER,
            ADD COLUMN IF NOT EXISTS solar_kwc DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS solar_computed_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS roof_key TEXT
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_companies_solar_kwc "
        "ON companies (solar_kwc DESC NULLS LAST)"
    )
    # Sert au regroupement des entreprises partageant un même toit.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_companies_roof_key ON companies (roof_key)"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ms_buildings (
            id SERIAL PRIMARY KEY,
            polygon JSONB NOT NULL,
            area_m2 DOUBLE PRECISION NOT NULL,
            centroid_lon DOUBLE PRECISION NOT NULL,
            centroid_lat DOUBLE PRECISION NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ms_buildings_centroid "
        "ON ms_buildings (centroid_lat, centroid_lon)"
    )
    # Clé naturelle : la source Microsoft ne fournit aucun identifiant,
    # seule la géométrie distingue deux bâtiments. Colonne générée, donc
    # toujours cohérente avec le polygone, et unique pour rendre
    # l'import rejouable sans dupliquer les 193 000 empreintes.
    cur.execute(
        "ALTER TABLE ms_buildings ADD COLUMN IF NOT EXISTS geom_hash TEXT "
        "GENERATED ALWAYS AS (md5(polygon::text)) STORED"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ms_buildings_geom_hash "
        "ON ms_buildings (geom_hash)"
    )
    # Comptes nominatifs des commerciaux/opérateurs. Créés via
    # `python -m scripts.create_user`, pas d'inscription en ligne : c'est
    # un outil interne, pas un service public.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Seul un compte admin peut créer d'autres comptes (page /admin/users).
    # Le tout premier compte se crée en ligne de commande
    # (`scripts.create_user --admin`) : une interface qui exige d'être
    # admin pour créer un compte ne peut pas créer le premier admin.
    cur.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false"
    )
    # Qui a détecté/tracé ce toit. Nullable : les segments créés avant
    # l'ajout des comptes n'ont pas d'auteur connu, et ça ne doit pas les
    # invalider. ON DELETE SET NULL plutôt que RESTRICT : supprimer un
    # compte ne doit pas bloquer sur les toits qu'il a laissés derrière lui.
    cur.execute(
        "ALTER TABLE ia_segments ADD COLUMN IF NOT EXISTS created_by INTEGER "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    # Entreprise déjà équipée de panneaux : elle sort de la liste des
    # prospects sans être supprimée. Une suppression ne tiendrait pas — le
    # prochain import la recréerait, puisqu'il reconnaît les entreprises par
    # leur identifiant Google. Placée après users, qu'elle référence.
    cur.execute(
        """
        ALTER TABLE companies
            ADD COLUMN IF NOT EXISTS equipped_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS equipped_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        """
    )
    # Trace qui a créé ou supprimé un toit. Une ligne d'ia_segments
    # disparaît à la suppression et emporterait son auteur avec elle ;
    # cette table existe précisément pour que la suppression, elle,
    # reste traçable après coup.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            entity TEXT NOT NULL,
            entity_id INTEGER,
            details JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log (entity, entity_id)"
    )
    # Créée ici aussi, et plus seulement par son script d'import : une base
    # cible doit recevoir toutes les tables avant la copie. Vide, elle ne
    # change rien au fonctionnement (repli sur Overpass, comme absente).
    create_osm_buildings(cur)
    create_pv_detections(cur)
    # Productible solaire du lieu (kWh par kWc et par an), donné par PVGIS
    # (services/pvgis.py). La production annuelle s'en déduit à la lecture
    # (puissance × productible) : rien à resynchroniser quand un toit change.
    cur.execute(
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS solar_yield_kwh_kwc DOUBLE PRECISION"
    )
    # Réponses de PVGIS, par case de 0,1° et par réglage de pose. Simple cache,
    # absent de COPY_ORDER : une base cible le reconstitue à la demande.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pvgis_cache (
            case_lat DOUBLE PRECISION NOT NULL,
            case_lon DOUBLE PRECISION NOT NULL,
            inclinaison DOUBLE PRECISION NOT NULL,
            orientation DOUBLE PRECISION NOT NULL,
            pertes DOUBLE PRECISION NOT NULL,
            montage TEXT NOT NULL,
            productible DOUBLE PRECISION NOT NULL,
            mensuel JSONB NOT NULL,
            ensoleillement DOUBLE PRECISION,
            demande_le TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (case_lat, case_lon, inclinaison, orientation, pertes, montage)
        )
        """
    )
    # CRM des commerciaux (services/crm.py), dans ses propres tables : la
    # table companies ne décrit que l'entreprise et son potentiel.
    #   opportunites        une par entreprise prise : commercial, étape,
    #                       raison de perte, prochaine relance ;
    #   opportunite_etapes  historique du pipeline (étape, qui, quand) ;
    #   decideurs           contacts de l'entreprise ;
    #   visites             chaque visite : consommation lue sur la facture,
    #                       état de la toiture, vraie pente et orientation.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunites (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
            commercial_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            statut TEXT NOT NULL DEFAULT 'a_contacter',
            raison_perte TEXT,
            relance_le DATE,
            relance_objet TEXT,
            pris_le TIMESTAMPTZ,
            cree_le TIMESTAMPTZ NOT NULL DEFAULT now(),
            maj_le TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_opportunites_commercial ON opportunites (commercial_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_opportunites_relance ON opportunites (relance_le)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunite_etapes (
            id SERIAL PRIMARY KEY,
            opportunite_id INTEGER NOT NULL REFERENCES opportunites(id) ON DELETE CASCADE,
            etape TEXT NOT NULL,
            raison_perte TEXT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            le TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Chaque ligne garde aussi la prochaine action prévue à ce moment, et le
    # commentaire du commercial (ajoutés le 9 octobre 2026).
    cur.execute(
        """
        ALTER TABLE opportunite_etapes
            ADD COLUMN IF NOT EXISTS relance_le DATE,
            ADD COLUMN IF NOT EXISTS relance_objet TEXT,
            ADD COLUMN IF NOT EXISTS commentaire TEXT
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunite_etapes_opp ON opportunite_etapes (opportunite_id, le)"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS decideurs (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            nom TEXT,
            fonction TEXT,
            telephone TEXT,
            email TEXT,
            cree_le TIMESTAMPTZ NOT NULL DEFAULT now(),
            maj_le TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_decideurs_company ON decideurs (company_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS visites (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            le TIMESTAMPTZ NOT NULL DEFAULT now(),
            conso_kwh_an DOUBLE PRECISION,
            etat_toiture TEXT,
            inclinaison DOUBLE PRECISION,
            orientation DOUBLE PRECISION,
            productible DOUBLE PRECISION
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_visites_company ON visites (company_id, le)")
    _migrer_crm_hors_de_companies(cur)
    # Notes des commerciaux (appels, visites, échanges), datées et signées.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_notes (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            texte TEXT NOT NULL,
            cree_le TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crm_notes_company ON crm_notes (company_id)")
