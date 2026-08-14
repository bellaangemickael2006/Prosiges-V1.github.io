"""
Posiges V1 — rendu des graphiques du tableau de bord en images, pour les
intégrer aux rapports PDF.

Les cinq graphiques et leurs couleurs reprennent exactement ceux de
`static/js/graphiques.js` (Évolution mensuelle, Trésorerie cumulée,
Structure des charges, Répartition par mode de paiement, Principaux
clients) : un rapport imprimé doit montrer les mêmes chiffres que le
tableau de bord consulté en ligne, tous deux construits à partir de
`comptes.donnees_graphiques()`.
"""
from io import BytesIO

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

BLEU = '#1c3d5a'
BLEU_CLAIR = '#4a7ba7'
VERT = '#2e8b57'
ROUGE = '#c0392b'
GRIS = '#6b7a8c'

PALETTE_CHAUDE = ['#c0392b', '#c8912b', '#a0522d', '#8f4a5e',
                  '#b5651d', '#7d3c4a', '#d4844a', '#6b4423']
PALETTE_FROIDE = ['#1c3d5a', '#2e8b57', '#4a7ba7', '#3d8b8b',
                  '#7d6b9e', '#5a8a9e', '#2f6f4f', '#6b7a8c']

plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.size': 9,
    'text.color': GRIS,
    'axes.edgecolor': '#d5dbe2',
    'axes.labelcolor': GRIS,
    'xtick.color': GRIS,
    'ytick.color': GRIS,
})


def _formater(valeur, _position=None):
    return f'{valeur:,.0f}'.replace(',', ' ')


def _figure_vide(titre):
    """Une image de substitution quand la série est vide, plutôt qu'un
    graphique blanc muet dans le rapport."""
    fig, ax = plt.subplots(figsize=(6, 3), dpi=150)
    ax.axis('off')
    ax.text(0.5, 0.5, 'Pas encore assez de données pour ce graphique.',
            ha='center', va='center', fontsize=10, color=GRIS)
    return _exporter(fig)


def _exporter(fig):
    tampon = BytesIO()
    fig.tight_layout()
    fig.savefig(tampon, format='png', bbox_inches='tight')
    plt.close(fig)
    tampon.seek(0)
    return tampon


def _serie_vide(valeurs):
    return not valeurs or not any(valeurs)


def evolution_mensuelle(donnees):
    """Ventes et achats en barres, résultat du mois en courbe."""
    if _serie_vide(donnees.get('ventes')) and _serie_vide(donnees.get('achats')):
        return _figure_vide('evolution')

    mois = donnees['mois']
    x = range(len(mois))
    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=150)
    largeur = 0.38
    ax.bar([i - largeur / 2 for i in x], donnees['ventes'], largeur,
          label='Ventes', color=VERT, zorder=2)
    ax.bar([i + largeur / 2 for i in x], donnees['achats'], largeur,
          label='Achats', color=ROUGE, zorder=2)
    ax2 = ax.twinx()
    ax2.plot(x, donnees['resultats'], color=BLEU, marker='o', markersize=3,
             linewidth=2, label='Résultat du mois', zorder=3)
    ax2.set_yticks([])
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)

    ax.set_xticks(list(x))
    ax.set_xticklabels(mois, fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(_formater))
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', color='#eeeeee', linewidth=0.8, zorder=0)
    lignes1, libelles1 = ax.get_legend_handles_labels()
    lignes2, libelles2 = ax2.get_legend_handles_labels()
    ax.legend(lignes1 + lignes2, libelles1 + libelles2, loc='upper center',
             bbox_to_anchor=(0.5, -0.16), ncol=3, frameon=False, fontsize=8)
    return _exporter(fig)


def tresorerie_cumulee(donnees):
    """Trésorerie cumulée sur l'année, en aire."""
    if _serie_vide(donnees.get('cumul')):
        return _figure_vide('cumul')

    mois = donnees['mois']
    x = range(len(mois))
    fig, ax = plt.subplots(figsize=(5, 3), dpi=150)
    ax.plot(x, donnees['cumul'], color=BLEU, linewidth=2.2, marker='o', markersize=3)
    ax.fill_between(x, donnees['cumul'], color=BLEU, alpha=0.12)
    ax.set_xticks(list(x))
    ax.set_xticklabels(mois, fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(_formater))
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', color='#eeeeee', linewidth=0.8, zorder=0)
    return _exporter(fig)


def _beignet(labels, valeurs, palette, titre_vide):
    if _serie_vide(valeurs):
        return _figure_vide(titre_vide)

    couleurs = [palette[i % len(palette)] for i in range(len(valeurs))]
    fig, ax = plt.subplots(figsize=(5, 3), dpi=150)
    total = sum(valeurs) or 1
    parts = ax.pie(
        valeurs, colors=couleurs, startangle=90, counterclock=False,
        wedgeprops={'width': 0.44, 'edgecolor': 'white', 'linewidth': 2},
        autopct=lambda p: f'{p:.0f} %' if p >= 5 else '',
        pctdistance=0.78, textprops={'fontsize': 8, 'color': 'white'})
    ax.legend(parts[0], labels, loc='center left', bbox_to_anchor=(1.0, 0.5),
              frameon=False, fontsize=8)
    ax.axis('equal')
    return _exporter(fig)


def structure_charges(donnees):
    return _beignet(donnees.get('charges_labels', []), donnees.get('charges_valeurs', []),
                    PALETTE_CHAUDE, 'charges')


def repartition_modes_paiement(donnees):
    return _beignet(donnees.get('modes_labels', []), donnees.get('modes_valeurs', []),
                    PALETTE_FROIDE, 'modes')


def principaux_clients(donnees):
    """Barres horizontales, le plus gros client en haut."""
    labels = donnees.get('clients_labels', [])
    valeurs = donnees.get('clients_valeurs', [])
    if _serie_vide(valeurs):
        return _figure_vide('clients')

    fig, ax = plt.subplots(figsize=(6, 3), dpi=150)
    ordre = list(range(len(labels)))[::-1]
    ax.barh([labels[i] for i in ordre], [valeurs[i] for i in ordre], color=BLEU_CLAIR)
    ax.xaxis.set_major_formatter(FuncFormatter(_formater))
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.grid(axis='x', color='#eeeeee', linewidth=0.8, zorder=0)
    ax.tick_params(axis='y', labelsize=8)
    return _exporter(fig)


def generer_planches(donnees):
    """Les cinq graphiques du tableau de bord, dans l'ordre où ils y sont
    présentés, prêts à être insérés dans un PDF."""
    return [
        ("Évolution mensuelle — entrées, sorties et résultat", evolution_mensuelle(donnees)),
        ("Trésorerie cumulée sur l'année", tresorerie_cumulee(donnees)),
        ('Structure des charges', structure_charges(donnees)),
        ('Répartition par mode de paiement', repartition_modes_paiement(donnees)),
        ('Principaux clients', principaux_clients(donnees)),
    ]
