"""Configurations de base enregistrées depuis la page d'administration.

Elles vivent dans un fichier du volume de cache, et non dans la base : la page
fait passer l'application d'une base à l'autre, elle ne peut pas ranger la
liste de ses destinations dans celle qu'elle s'apprête à quitter.

Les mots de passe y sont chiffrés (Fernet) avec DB_CONFIG_KEY, lue dans
l'environnement et jamais écrite sur disque par l'application : une copie du
volume ne suffit pas à les lire.

Sans configuration active, l'application se connecte comme avant, avec les
variables du .env. C'est l'état par défaut, et le point de retour.
"""
import base64
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

FILENAME = "db_configs.json"
# Identifiant réservé à la base du .env dans les listes et les routes.
ENV_ID = "env"
DEFAULT_PORT = 5432
SSL_MODES = ("prefer", "require", "disable", "allow", "verify-ca", "verify-full")


class ConfigError(Exception):
    """Refus ou incident, formulé pour être montré tel quel à l'administrateur."""


def _path():
    # Lu à chaque appel et non à l'import : les tests et le script de secours
    # peuvent ainsi viser un autre dossier.
    cache = os.environ.get("CACHE_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(cache, FILENAME)


def key_configured():
    return bool(os.environ.get("DB_CONFIG_KEY", "").strip())


def _fernet():
    from cryptography.fernet import Fernet

    raw = os.environ.get("DB_CONFIG_KEY", "").strip()
    if not raw:
        raise ConfigError(
            "DB_CONFIG_KEY est absente de l'environnement : les mots de passe "
            "enregistrés ne peuvent être ni chiffrés ni relus."
        )
    # N'importe quelle phrase convient : on en dérive les 32 octets que Fernet attend.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def _encrypt(password):
    return _fernet().encrypt(password.encode()).decode()


def _decrypt(token):
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ConfigError(
            "Mot de passe illisible : DB_CONFIG_KEY a changé depuis l'enregistrement "
            "de cette configuration. Modifiez-la pour ressaisir le mot de passe."
        ) from None


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"active": None, "configs": []}
    except json.JSONDecodeError:
        raise ConfigError(
            f"{_path()} est illisible. Pour revenir à la base du .env : "
            "python -m scripts.db_config use-env"
        ) from None
    data.setdefault("active", None)
    data.setdefault("configs", [])
    return data


def _save(data):
    path = _path()
    # Écriture dans un fichier temporaire puis renommage : une coupure au
    # milieu ne laisse jamais un fichier à moitié écrit, qui empêcherait
    # l'application de démarrer. mkstemp le crée lisible du seul propriétaire.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".db_configs.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _validate(fields, require_password):
    host = (fields.get("host") or "").strip()
    dbname = (fields.get("dbname") or "").strip()
    user = (fields.get("user") or "").strip()
    schema = (fields.get("schema") or "").strip()
    label = (fields.get("label") or "").strip()
    sslmode = (fields.get("sslmode") or "prefer").strip()
    password = fields.get("password") or ""

    missing = [
        name for name, value in (
            ("le serveur", host),
            ("la base", dbname),
            ("l'utilisateur", user),
            ("le mot de passe", password if require_password else "x"),
        )
        if not value
    ]
    if missing:
        raise ConfigError("Champs obligatoires manquants : " + ", ".join(missing) + ".")

    try:
        port = int(str(fields.get("port") or DEFAULT_PORT).strip())
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ConfigError("Port invalide : il faut un nombre entre 1 et 65535.")

    if sslmode not in SSL_MODES:
        raise ConfigError("Mode SSL inconnu.")

    clean = {
        "label": label or f"{host} · {dbname}",
        "host": host,
        "port": port,
        "dbname": dbname,
        "schema": schema,
        "user": user,
        "sslmode": sslmode,
    }
    return clean, password


def _find(data, config_id):
    for cfg in data["configs"]:
        if cfg["id"] == config_id:
            return cfg
    raise ConfigError("Cette configuration n'existe plus.")


def public(cfg):
    """La configuration sans son mot de passe, même chiffré — pour l'affichage."""
    return {k: v for k, v in cfg.items() if k != "password"}


def list_public():
    data = load()
    return [public(c) for c in data["configs"]], data["active"]


def active_id():
    return load()["active"]


def add(fields):
    clean, password = _validate(fields, require_password=True)
    token = _encrypt(password)
    data = load()
    cfg = {
        "id": uuid.uuid4().hex[:12],
        "db_type": "postgresql",
        **clean,
        "password": token,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data["configs"].append(cfg)
    _save(data)
    return cfg["id"]


def update(config_id, fields):
    data = load()
    cfg = _find(data, config_id)
    if data["active"] == config_id:
        raise ConfigError(
            "Impossible de modifier la configuration active : activez d'abord une autre base."
        )
    clean, password = _validate(fields, require_password=False)
    cfg.update(clean)
    # Champ laissé vide au formulaire : le mot de passe enregistré est conservé.
    if password:
        cfg["password"] = _encrypt(password)
    _save(data)


def delete(config_id):
    data = load()
    _find(data, config_id)
    if data["active"] == config_id:
        raise ConfigError("Impossible de supprimer la configuration active.")
    data["configs"] = [c for c in data["configs"] if c["id"] != config_id]
    _save(data)


def set_active(config_id):
    """Active une configuration ; None revient à la base du .env."""
    data = load()
    if config_id is not None:
        _find(data, config_id)
    data["active"] = config_id
    _save(data)


DEFAULT_ENV_LABEL = "Fichier .env"


def env_label():
    """Nom affiché de la base du .env — celui choisi sur la page, sinon « Fichier .env »."""
    try:
        return load().get("env_label") or DEFAULT_ENV_LABEL
    except ConfigError:
        return DEFAULT_ENV_LABEL


def set_env_label(label):
    """Renomme la base du .env. Seul son nom se règle ici : sa connexion vient
    du fichier .env. Un nom vide revient au nom par défaut."""
    label = (label or "").strip()
    if len(label) > 80:
        raise ConfigError("Nom trop long : 80 caractères au plus.")
    data = load()
    data["env_label"] = label or None
    _save(data)


def use_env():
    """Revient à la base du .env.

    Un fichier de configurations illisible est mis de côté plutôt que détruit ;
    renvoie alors son nouveau chemin, sinon None.
    """
    try:
        set_active(None)
    except ConfigError:
        path = _path()
        aside = path + ".illisible"
        os.replace(path, aside)
        return aside
    return None


def target(config_id):
    """Configuration déchiffrée, prête pour db.connect()."""
    cfg = _find(load(), config_id)
    return {**public(cfg), "password": _decrypt(cfg["password"])}


def active_target():
    """Configuration active déchiffrée, ou None quand c'est le .env qui s'applique.

    Aucun repli silencieux : une configuration active introuvable ou illisible
    lève une erreur plutôt que de laisser l'application écrire dans une autre
    base que celle qu'on croit active.
    """
    data = load()
    if not data["active"]:
        return None
    cfg = next((c for c in data["configs"] if c["id"] == data["active"]), None)
    if cfg is None:
        raise ConfigError(
            "La configuration active a disparu de la liste. Pour revenir à la base "
            "du .env : python -m scripts.db_config use-env"
        )
    return {**public(cfg), "password": _decrypt(cfg["password"])}
