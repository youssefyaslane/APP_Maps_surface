"""Import Excel des entreprises : pas de doublon en base.

Même règle que le chatbot (services/doublons.py). Ce qui casserait en
silence : une fiche Google en double (même téléphone, même site, nom proche
au même endroit) écrite comme une nouvelle entreprise, une entreprise déjà
connue par son place_id refusée au lieu d'être mise à jour, ou deux lignes de
la même société dans un fichier écrites toutes les deux. La base est simulée.
"""
import openpyxl

from scripts import import_companies as imp

ENTETE = ["title", "categoryName", "address", "city", "phone", "website", "totalScore",
          "location/lat", "location/lng", "placeId"]


def classeur(chemin, lignes):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(ENTETE)
    for ligne in lignes:
        ws.append(ligne)
    wb.save(chemin)


class Curseur:
    def __init__(self, base):
        self.base, self._r = base, []

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT DISTINCT trim(city)"):
            self._r = [("Casablanca",)]
        elif sql.startswith("SELECT id, name, lat, lon, phone, website FROM companies"):
            self._r = list(self.base["existantes"])
        elif sql.startswith("SELECT place_id FROM companies WHERE place_id IS NOT NULL"):
            self._r = [(p,) for p in self.base["place_ids"]]
        elif sql.startswith("INSERT INTO companies"):
            self.base["ecrits"].append(params[0])
            self._r = [(1000 + len(self.base["ecrits"]),)]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self._r

    def fetchone(self):
        return self._r[0] if self._r else None


class Connexion:
    def __init__(self, base):
        self.base = base

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def cursor(self):
        return Curseur(self.base)

    def close(self):
        pass


def test_l_import_n_ecrit_pas_les_doublons(tmp_path, monkeypatch, capsys):
    base = {
        "existantes": [(6024, "JJWASHING usine", 33.5324, -7.4912, "+212 5 22 21 88 09", "http://jjwashing.ma")],
        "place_ids": {"ChIJ-connu"},
        "ecrits": [],
    }
    monkeypatch.setattr(imp.db, "connect", lambda: Connexion(base))
    fichier = tmp_path / "export.xlsx"
    classeur(fichier, [
        # Déjà connue par son place_id : mise à jour, pas un doublon.
        ["JJWASHING usine", "Usine", "Tit Mellil", "Casablanca", "+212 5 22 21 88 09", None, 4.1,
         33.5324, -7.4912, "ChIJ-connu"],
        # Autre fiche Google de la même société : même téléphone, écartée.
        ["J.J.W", "Usine textile", "Bd Mohammed VI", "Casablanca", "0522218809", None, 4.0,
         33.5263, -7.5641, "ChIJ-jjw"],
        # Nouvelle entreprise : écrite.
        ["Usine Atlas", "Fabricant", "Ain Sebaa", "Casablanca", "0522000000", None, 4.5,
         33.60, -7.53, "ChIJ-atlas"],
        # Même société, deuxième ligne du fichier (nom proche, même endroit) : écartée.
        ["USINE ATLAS SARL", "Fabricant", "Ain Sebaa", "Casablanca", None, None, None,
         33.6001, -7.5301, "ChIJ-atlas-2"],
    ])
    imp.import_companies(str(fichier))
    assert base["ecrits"] == ["JJWASHING usine", "Usine Atlas"]
    sortie = capsys.readouterr().out
    assert "Doublons écartés, non écrits : 2" in sortie
    assert "J.J.W — même téléphone que n° 6024" in sortie
    assert "USINE ATLAS SARL — même nom, à moins de 50 m" in sortie


def test_la_verification_annonce_les_doublons_sans_rien_ecrire(tmp_path, monkeypatch, capsys):
    base = {
        "existantes": [(6024, "JJWASHING usine", 33.5324, -7.4912, "+212 5 22 21 88 09", None)],
        "place_ids": set(),
        "ecrits": [],
    }
    monkeypatch.setattr(imp.db, "connect", lambda: Connexion(base))
    fichier = tmp_path / "export.xlsx"
    classeur(fichier, [["J.J.W", None, None, "Casablanca", "0522218809", None, None, 33.52, -7.56, "ChIJ-jjw"]])
    imp.import_companies(str(fichier), check_only=True)
    assert base["ecrits"] == []
    assert "Doublons seraient écartés : 1" in capsys.readouterr().out
