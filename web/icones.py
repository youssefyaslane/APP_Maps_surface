"""Icônes de l'interface : SVG au trait (style Lucide/Feather, licences ISC/MIT),
intégrés directement dans la page, sans dépendance externe.

Une seule source pour les gabarits et les scripts : `icone("telephone")` dans
un gabarit Jinja, `icone("telephone")` en JavaScript (window.icone, défini par
`script_icones()` en tête de page). L'icône prend la taille et la couleur du
texte qui l'entoure (1em, currentColor).
"""
import json

from markupsafe import Markup

TRACES = {
    "telephone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 '
                 '19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 '
                 '2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 '
                 '2.81.7A2 2 0 0 1 22 16.92z"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/>'
             '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "carte": '<polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/>'
             '<line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/>',
    "repere": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    "courriel": '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>'
                '<polyline points="22,6 12,13 2,6"/>',
    "immeuble": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/>'
                '<path d="M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01"/>',
    "maison": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "utilisateur": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "prendre": '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/>'
               '<line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/>',
    "valide": '<polyline points="20 6 9 17 4 12"/>',
    "fermer": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "horloge": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "dossier": '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>'
               '<rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>',
    "soleil": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/>'
              '<line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>'
              '<line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/>'
              '<line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>'
              '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "eclair": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "feuille": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/>'
               '<path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>',
    "billet": '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/>'
              '<path d="M6 12h.01M18 12h.01"/>',
    "alerte": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
              '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "oeil": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "menu": '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/>'
            '<line x1="3" y1="18" x2="21" y2="18"/>',
    "crayon": '<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
    "loupe": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "graphique": '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/>'
                 '<line x1="6" y1="20" x2="6" y2="16"/>',
    "base": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>'
            '<path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    "chevron": '<polyline points="9 18 15 12 9 6"/>',
    "exclamation": '<line x1="12" y1="5" x2="12" y2="14"/><line x1="12" y1="19" x2="12.01" y2="19"/>',
    "tiret": '<line x1="6" y1="12" x2="18" y2="12"/>',
}

# Gabarit commun : 1em et currentColor, abaissé pour s'aligner sur la ligne de texte.
_SVG = ('<svg class="icone{classe}" viewBox="0 0 24 24" width="1em" height="1em" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        'style="vertical-align:-0.125em;flex:none" aria-hidden="true">{trace}</svg>')


def icone(nom, classe=""):
    """L'icône `nom`, prête à insérer dans un gabarit."""
    return Markup(_SVG.format(classe=f" {classe}" if classe else "", trace=TRACES[nom]))


def script_icones():
    """Balise <script> qui définit window.icone(nom, classe) avec les mêmes tracés."""
    gabarit = json.dumps(_SVG)
    traces = json.dumps(TRACES)
    return Markup(
        "<script>window.ICONES=" + traces + ";window.icone=function(n,c){"
        "var t=window.ICONES[n];if(!t)return'';return " + gabarit +
        ".replace('{classe}',c?' '+c:'').replace('{trace}',t);};</script>"
    )
