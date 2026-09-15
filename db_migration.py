"""Tester une base cible, et la rendre identique à la base active.

Utilisé par la page d'administration de la base. La source est toujours la base
active : elle n'est que lue, dans un instantané unique (REPEATABLE READ), pour
que les tables recopiées restent cohérentes entre elles — un toit ne peut pas
désigner un compte qui n'aurait pas suivi.

La synchronisation rapproche les lignes par clé primaire : celles de la base
active sont ajoutées ou mises à jour dans la cible, celles qui n'existent plus
dans la base active en sont retirées. Une ligne ne peut donc jamais s'y
retrouver en double, et la synchronisation marche dans les deux sens — vers une
base vide comme vers une base qui a déjà servi.

Côté cible, tout se fait dans une seule transaction, vérifiée avant d'être
validée : interrompue, elle n'y laisse aucune trace, et se relance telle quelle.
"""
import re
import tempfile
import threading
import time

import psycopg2
import psycopg2.extensions
from psycopg2 import sql

import db
import db_configs
import schema

CONNECT_TIMEOUT_S = 5
# Au-delà, le tampon de copie d'une table passe de la mémoire au disque.
SPOOL_MAX_BYTES = 64 * 1024 * 1024


class MigrationError(Exception):
    """Refus ou échec, formulé pour être montré tel quel à l'administrateur."""


_ERRORS = (
    ("password authentication failed", "Identifiant ou mot de passe refusé par le serveur."),
    ("no password supplied", "Le serveur demande un mot de passe."),
    ("could not translate host name", "Serveur introuvable : vérifiez le nom ou l'adresse."),
    ("name or service not known", "Serveur introuvable : vérifiez le nom ou l'adresse."),
    ("connection refused", "Le serveur refuse la connexion sur ce port."),
    ("timeout expired", f"Le serveur ne répond pas (délai de {CONNECT_TIMEOUT_S} s dépassé)."),
    ("server does not support ssl", "Ce serveur n'accepte pas SSL : choisissez le mode « prefer » ou « disable »."),
    ("no pg_hba.conf entry", "Le serveur n'autorise pas les connexions depuis cette machine (pg_hba.conf) — à ouvrir par son administrateur."),
    ("permission denied", "Droits insuffisants pour cet utilisateur sur ce schéma."),
)


def explain(exc):
    """Message d'erreur PostgreSQL traduit en une phrase actionnable."""
    text = str(exc).strip()
    low = text.lower()
    m = re.search(r'database "([^"]+)" does not exist', text)
    if m:
        return f"La base « {m.group(1)} » n'existe pas sur ce serveur."
    m = re.search(r'role "([^"]+)" does not exist', text)
    if m:
        return f"L'utilisateur « {m.group(1)} » n'existe pas sur ce serveur."
    for needle, message in _ERRORS:
        if needle in low:
            return message
    return text.splitlines()[0] if text else exc.__class__.__name__


def copyable(columns):
    """Colonnes que COPY peut écrire, à partir de (nom, is_generated).

    Les colonnes générées — geom_hash de ms_buildings — sont refusées en
    écriture : la cible les recalcule elle-même à partir du polygone.
    """
    return [name for name, generated in columns if generated != "ALWAYS"]


def blocked_reason(diff):
    """Motif de refus d'une synchronisation, ou None.

    L'application ne fait qu'ajouter des lignes au journal (audit_log), et
    n'écrit jamais dans une base inactive — sauf par une synchronisation, qui
    la rend identique à la base active. Des lignes de journal présentes dans la
    cible et absentes de la base active prouvent donc que la cible a servi plus
    récemment : la rendre identique effacerait ce travail. C'est le mauvais
    sens — il faut activer la base la plus récente et synchroniser depuis elle.
    """
    orphans = diff.get("audit_log", {}).get("removed", 0)
    if orphans:
        return (
            f"La base cible contient {orphans} action(s) absente(s) de la base active : "
            "elle a servi plus récemment, et la synchroniser effacerait ce travail. "
            "Activez d'abord la base la plus récente, puis synchronisez depuis elle."
        )
    return None


def is_identical(diff):
    return all(not (d["added"] or d["changed"] or d["removed"]) for d in diff.values())


def _columns(cur, table):
    cur.execute(
        """
        SELECT column_name, is_generated
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return copyable(cur.fetchall())


def inspect(target=None, admin=None):
    """Diagnostic d'une base, sans rien y écrire.

    `target` : None pour la base active, db.ENV pour celle du .env, ou une
    configuration déchiffrée. `admin` ({id, username}) : vérifie en plus que
    ce compte administrateur existe sur la base, condition pour l'activer
    sans s'enfermer dehors.
    """
    report = {
        "ok": False,
        "error": None,
        "server_version": None,
        "ssl": None,
        "schema": None,
        "schema_exists": False,
        "can_create": False,
        "tables": {},
        "admin_present": None,
    }
    try:
        report["schema"] = db.schema_of(target)
        conn = db.connect(target, connect_timeout=CONNECT_TIMEOUT_S)
    except db_configs.ConfigError as exc:
        report["error"] = str(exc)
        return report
    except psycopg2.Error as exc:
        report["error"] = explain(exc)
        return report

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SHOW server_version")
            report["server_version"] = cur.fetchone()[0]
            try:
                cur.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
                row = cur.fetchone()
                report["ssl"] = bool(row[0]) if row else None
            except psycopg2.Error:
                report["ssl"] = None

            name = report["schema"]
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)", (name,))
            report["schema_exists"] = cur.fetchone()[0]
            if report["schema_exists"]:
                cur.execute("SELECT has_schema_privilege(%s, 'CREATE')", (name,))
                report["can_create"] = cur.fetchone()[0]
                for table in schema.COPY_ORDER:
                    ident = sql.Identifier(name, table)
                    cur.execute("SELECT to_regclass(%s)", (ident.as_string(cur),))
                    if cur.fetchone()[0] is None:
                        report["tables"][table] = None
                        continue
                    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(ident))
                    report["tables"][table] = cur.fetchone()[0]
                if admin and report["tables"].get("users") is not None:
                    cur.execute(
                        sql.SQL(
                            "SELECT EXISTS (SELECT 1 FROM {} WHERE id = %s AND username = %s AND is_admin)"
                        ).format(sql.Identifier(name, "users")),
                        (admin["id"], admin["username"]),
                    )
                    report["admin_present"] = cur.fetchone()[0]
        report["ok"] = True
    except psycopg2.Error as exc:
        report["error"] = explain(exc)
    finally:
        conn.close()
    return report


# --- Synchronisation ------------------------------------------------------


def _stage(src, dst, scur, dcur, table, cols):
    """Recopie la table de la base active dans une table temporaire de la cible.

    C'est côté cible, où les deux versions se côtoient, que se font ensuite la
    comparaison et la mise à jour — en SQL, sans rapatrier les lignes.
    """
    stage = sql.Identifier(f"_sync_{table}")
    dcur.execute(
        sql.SQL("CREATE TEMP TABLE {} (LIKE {}) ON COMMIT DROP").format(stage, sql.Identifier(table))
    )
    cols_sql = sql.SQL(", ").join(map(sql.Identifier, cols))
    with tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES) as buf:
        scur.copy_expert(
            sql.SQL("COPY {} ({}) TO STDOUT").format(sql.Identifier(table), cols_sql).as_string(src), buf
        )
        buf.seek(0)
        dcur.copy_expert(sql.SQL("COPY {} ({}) FROM STDIN").format(stage, cols_sql).as_string(dst), buf)
    dcur.execute(sql.SQL("ANALYZE {}").format(stage))
    return stage


def _diff(dcur, table, stage, cols):
    """Lignes à ajouter, à modifier et à retirer pour que la table égale sa copie."""
    pk = sql.Identifier(schema.primary_key(table))
    target = sql.Identifier(table)
    t_cols = sql.SQL(", ").join(sql.Identifier("t", c) for c in cols)
    s_cols = sql.SQL(", ").join(sql.Identifier("s", c) for c in cols)
    parts = {"stage": stage, "t": target, "pk": pk, "tc": t_cols, "sc": s_cols}
    dcur.execute(sql.SQL(
        "SELECT count(*) FROM {stage} s WHERE NOT EXISTS (SELECT 1 FROM {t} t WHERE t.{pk} = s.{pk})"
    ).format(**parts))
    added = dcur.fetchone()[0]
    dcur.execute(sql.SQL(
        "SELECT count(*) FROM {t} t WHERE NOT EXISTS (SELECT 1 FROM {stage} s WHERE s.{pk} = t.{pk})"
    ).format(**parts))
    removed = dcur.fetchone()[0]
    dcur.execute(sql.SQL(
        "SELECT count(*) FROM {t} t JOIN {stage} s ON s.{pk} = t.{pk} WHERE ({tc}) IS DISTINCT FROM ({sc})"
    ).format(**parts))
    changed = dcur.fetchone()[0]
    return {"added": added, "changed": changed, "removed": removed}


def _apply(dcur, table, stage, cols):
    """Rend la table identique à sa copie : retraits, puis ajouts et mises à jour."""
    pk_name = schema.primary_key(table)
    pk = sql.Identifier(pk_name)
    target = sql.Identifier(table)
    dcur.execute(sql.SQL(
        "DELETE FROM {t} t WHERE NOT EXISTS (SELECT 1 FROM {stage} s WHERE s.{pk} = t.{pk})"
    ).format(t=target, stage=stage, pk=pk))

    others = [c for c in cols if c != pk_name]
    cols_sql = sql.SQL(", ").join(map(sql.Identifier, cols))
    if not others:
        on_conflict = sql.SQL("DO NOTHING")
    else:
        # La mise à jour ne touche que les lignes réellement différentes.
        on_conflict = sql.SQL("DO UPDATE SET {set} WHERE ({mine}) IS DISTINCT FROM ({theirs})").format(
            set=sql.SQL(", ").join(
                sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in others
            ),
            mine=sql.SQL(", ").join(sql.Identifier("cible", c) for c in others),
            theirs=sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(c)) for c in others),
        )
    dcur.execute(sql.SQL(
        "INSERT INTO {t} AS cible ({cols}) SELECT {cols} FROM {stage} ON CONFLICT ({pk}) {conflict}"
    ).format(t=target, cols=cols_sql, stage=stage, pk=pk, conflict=on_conflict))


def _check_not_stale(target):
    """Refuse, sans rien écrire, si la cible a servi plus récemment que la base active.

    Ne compare que le journal, petit : c'est lui qui trahit une cible plus
    récente (voir blocked_reason), et il suffit de le lire pour trancher.
    """
    src = db.connect()
    try:
        dst = db.connect(target, connect_timeout=CONNECT_TIMEOUT_S)
    except BaseException:
        src.close()
        raise
    try:
        with src.cursor() as scur, dst.cursor() as dcur:
            dcur.execute("SELECT to_regclass('audit_log')")
            if dcur.fetchone()[0] is None:
                return  # cible sans journal : rien qu'elle puisse perdre
            cols = _columns(scur, "audit_log")
            stage = _stage(src, dst, scur, dcur, "audit_log", cols)
            reason = blocked_reason({"audit_log": _diff(dcur, "audit_log", stage, cols)})
    finally:
        dst.rollback()
        src.close()
        dst.close()
    if reason:
        raise MigrationError(reason)


def sync(target, apply=False, progress=lambda **_: None, before_apply=None):
    """Compare la base active à `target` ; avec apply, rend la cible identique.

    Renvoie {"diff": {table: {source, added, changed, removed}}, "blocked": motif
    ou None}. L'aperçu (apply=False) n'écrit rien : les tables temporaires
    disparaissent avec l'annulation de sa transaction.
    """
    if apply:
        # Le refus « mauvais sens » passe avant toute écriture : sinon une
        # synchronisation refusée laisserait au journal la trace d'une
        # synchronisation qui n'a jamais eu lieu.
        _check_not_stale(target)
        if before_apply:
            # Tracé dans la base active AVANT l'instantané : la cible en reçoit
            # la copie, et les deux bases restent identiques, journal compris.
            before_apply()

    src = db.connect()
    dst = None
    try:
        src.set_session(
            isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ, readonly=True
        )
        dst = db.connect(target, connect_timeout=CONNECT_TIMEOUT_S)
        if apply:
            progress(step="Préparation des tables sur la base cible")
            with dst, dst.cursor() as cur:
                schema.create_all(cur)

        diff = {}
        staged = {}
        with src.cursor() as scur, dst.cursor() as dcur:
            # Première requête de l'instantané : tout ce qui suit voit la base
            # active au même instant.
            tables = []
            for table in schema.COPY_ORDER:
                scur.execute("SELECT to_regclass(%s)", (table,))
                if scur.fetchone()[0] is None:
                    continue  # osm_buildings, tant que son import n'a pas été lancé
                scur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                tables.append((table, scur.fetchone()[0]))

            for index, (table, rows) in enumerate(tables, 1):
                progress(step=f"Comparaison de {table}", table=table, index=index,
                         total=len(tables), rows=rows)
                dcur.execute("SELECT to_regclass(%s)", (table,))
                if dcur.fetchone()[0] is None:
                    # Aperçu d'une cible sans cette table : tout y serait ajouté.
                    diff[table] = {"source": rows, "added": rows, "changed": 0, "removed": 0}
                    continue
                cols = _columns(scur, table)
                target_cols = set(_columns(dcur, table))
                lost = [c for c in cols if c not in target_cols]
                if lost:
                    raise MigrationError(
                        f"La table {table} de la cible n'a pas les colonnes " + ", ".join(lost)
                        + " : leurs données seraient perdues."
                    )
                stage = _stage(src, dst, scur, dcur, table, cols)
                staged[table] = (stage, cols)
                diff[table] = {"source": rows, **_diff(dcur, table, stage, cols)}

            reason = blocked_reason(diff)
            if not apply:
                dst.rollback()
                return {"diff": diff, "blocked": reason}
            if reason:
                raise MigrationError(reason)

            for index, (table, _) in enumerate(tables, 1):
                progress(step=f"Mise à jour de {table}", table=table, index=index,
                         total=len(tables), rows=None)
                stage, cols = staged[table]
                _apply(dcur, table, stage, cols)

            progress(step="Recalage des compteurs d'identifiants")
            for table, _ in tables:
                if table in schema.SERIAL_TABLES:
                    dcur.execute(
                        sql.SQL(
                            "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                            "COALESCE(max(id), 1), max(id) IS NOT NULL) FROM {}"
                        ).format(sql.Identifier(table)),
                        (table,),
                    )

            # Avant de valider : la cible doit maintenant égaler sa copie, ligne
            # pour ligne. Sinon tout est annulé.
            progress(step="Vérification")
            gaps = []
            for table, _ in tables:
                stage, cols = staged[table]
                left = _diff(dcur, table, stage, cols)
                if left["added"] or left["changed"] or left["removed"]:
                    gaps.append(
                        f"{table} : {left['added']} manquante(s), {left['changed']} différente(s), "
                        f"{left['removed']} en trop"
                    )
            if gaps:
                raise MigrationError("Écart après synchronisation — " + " ; ".join(gaps))
        dst.commit()
        return {"diff": diff, "blocked": None}
    except BaseException:
        if dst is not None and not dst.closed:
            dst.rollback()
        raise
    finally:
        src.close()
        if dst is not None:
            dst.close()


# --- Synchronisation en tâche de fond -------------------------------------
#
# Comparer puis recopier près de 300 000 lignes prend plus longtemps qu'une
# requête HTTP ne devrait durer : le travail tourne dans un fil à part, et la
# page interroge son avancement. L'état vit en mémoire du processus web, qui
# est unique.

_job = {"state": "idle"}
_job_lock = threading.Lock()


def job_status():
    with _job_lock:
        return dict(_job)


def _progress(**fields):
    with _job_lock:
        _job.update(fields)


def start(target, label, apply=False, before_apply=None):
    with _job_lock:
        if _job.get("state") == "running":
            raise MigrationError("Une synchronisation est déjà en cours.")
        _job.clear()
        _job.update(state="running", mode="apply" if apply else "preview", target=label,
                    step="Démarrage", index=0, total=None, started_at=time.time())
    threading.Thread(target=_run, args=(target, apply, before_apply), daemon=True).start()


def _run(target, apply, before_apply):
    try:
        result = sync(target, apply=apply, progress=_progress, before_apply=before_apply)
    except (MigrationError, db_configs.ConfigError) as exc:
        message = str(exc)
    except psycopg2.Error as exc:
        message = explain(exc)
    except Exception as exc:  # affiché sur la page plutôt que perdu dans un fil
        message = f"Erreur inattendue : {exc}"
    else:
        with _job_lock:
            _job.update(state="done", step="Terminé", finished_at=time.time(),
                        diff=result["diff"], blocked=result["blocked"])
        return
    with _job_lock:
        _job.update(state="error", error=message, finished_at=time.time())
