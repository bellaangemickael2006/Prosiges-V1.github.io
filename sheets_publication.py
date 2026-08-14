"""
Posiges V1 — publication du classeur de suivi vers Google Sheets.

Le classeur créé à l'installation d'une entreprise reprend la structure
arrêtée avec le cabinet :

  1. « Journal de bord »        — date, libellé, catégorie, entrée, sortie,
                                  mode de paiement, solde cumulé, pièce
  2. « Compte d'exploitation »  — Mois 1 à Mois 12, trois colonnes par mois
                                  (Prévu, Réalisé, Écart), plus le total
  3. « Compte de trésorerie »   — même présentation, côté liquidité
  4. « Produits et services »   — catalogue et familles de produits

Un cinquième onglet « Données » conserve le journal à plat : une ligne par
opération, sans sous-total ni cellule fusionnée. C'est le format qui se
prête aux tris, aux filtres et aux tableaux croisés du tableur.

Tous les montants sont recalculés à la publication : le classeur reflète
toujours l'état exact de l'application au moment où il est produit.
"""
import comptes
import google_integration
from models import (Article, FamilleProduit, ModePaiement, NatureArticle,
                    TypeBudget)


# --------------------------------------------------------------------------
# 1. Journal de bord
# --------------------------------------------------------------------------

def _journal_de_bord(annee, scope):
    valeurs = [['JOURNAL DE BORD'], []]
    valeurs.append(['Date', 'Libellé', 'Catégorie', 'Entrée', 'Sortie',
                    'Mode de paiement', 'Solde', 'Pièce justificative'])

    total_entrees = total_sorties = 0.0
    for ligne in comptes.journal_de_bord(annee, **scope):
        total_entrees += ligne['entree']
        total_sorties += ligne['sortie']
        valeurs.append([
            ligne['date'].strftime('%d/%m/%Y'),
            ligne['libelle'],
            ligne['categorie'],
            round(ligne['entree']) or '',
            round(ligne['sortie']) or '',
            ligne['mode_paiement'],
            round(ligne['solde']),
            ligne['preuve'],
        ])

    valeurs.append([])
    valeurs.append(['TOTAL', '', '', round(total_entrees), round(total_sorties),
                    '', round(total_entrees - total_sorties), ''])
    return valeurs


# --------------------------------------------------------------------------
# 2 et 3. Comptes détaillés — trois colonnes par mois
# --------------------------------------------------------------------------

def _entetes_compte(compte):
    """Deux lignes d'en-tête : les mois, puis Prévu / Réalisé / Écart."""
    ligne_mois = ['LIBELLE']
    ligne_colonnes = ['']
    for mois in compte['mois']:
        ligne_mois += [mois['libelle'], '', '']
        ligne_colonnes += ['Prévu', 'Réalisé', 'Écart']
    ligne_mois += ['TOTAL ANNÉE', '', '']
    ligne_colonnes += ['Prévu', 'Réalisé', 'Écart']
    return ligne_mois, ligne_colonnes


def _ligne_compte(ligne):
    """Une rubrique : trois cellules par mois, puis le total de l'année."""
    cellules = [ligne['libelle']]
    for index in range(12):
        cellules += [round(ligne['prevu'][index]),
                     round(ligne['realise'][index]),
                     round(ligne['ecart'][index])]
    cellules += [round(ligne['total_prevu']), round(ligne['total_realise']),
                 round(ligne['total_ecart'])]
    return cellules


def _compte_detaille(compte, titre):
    valeurs = [[titre], [f"Exercice {compte['annee']}"], []]
    ligne_mois, ligne_colonnes = _entetes_compte(compte)
    valeurs.append(ligne_mois)
    valeurs.append(ligne_colonnes)

    for section in compte['sections']:
        valeurs.append([section['titre']])
        for ligne in section['lignes']:
            valeurs.append(_ligne_compte(ligne))
        valeurs.append(_ligne_compte(section['total']))
        valeurs.append([])

        # La marge brute s'intercale entre les charges variables et les fixes
        if section['code'] == 'variables' and 'marge_brute' in compte:
            valeurs.append(_ligne_compte(compte['marge_brute']))
            valeurs.append([])

    if 'resultat' in compte:
        valeurs.append(_ligne_compte(compte['resultat']))
    if 'solde_debut' in compte:
        valeurs.append(_ligne_compte(compte['solde_debut']))
        valeurs.append(_ligne_compte(compte['solde_fin']))

    valeurs.append([])
    valeurs.append(["Écart = Réalisé − Prévu. Le prévu provient du budget "
                    "saisi par le gérant ; le réalisé est calculé "
                    "automatiquement à partir du journal de bord."])
    return valeurs


# --------------------------------------------------------------------------
# 4. Produits et services
# --------------------------------------------------------------------------

def _produits_et_services(entreprise_id):
    valeurs = [['LISTE DES PRODUITS ET SERVICES'], []]
    valeurs.append(['Référence', 'Désignation', 'Nature', 'Famille',
                    'Unité', 'Prix de vente', "Prix d'achat",
                    'Stock', "Seuil d'alerte", 'Valeur du stock', 'Actif'])

    articles = (Article.query.filter_by(entreprise_id=entreprise_id)
                .order_by(Article.designation).all())
    for article in articles:
        valeurs.append([
            article.reference or '',
            article.designation,
            NatureArticle.LABELS.get(article.nature, article.nature),
            article.famille.nom if article.famille else '',
            article.unite or '',
            round(article.prix_vente or 0),
            round(article.prix_achat or 0),
            round(article.quantite_stock or 0) if article.suit_stock else '',
            round(article.seuil_alerte or 0) if article.suit_stock else '',
            round(article.valeur_stock),
            'Oui' if article.actif else 'Non',
        ])

    familles = (FamilleProduit.query.filter_by(entreprise_id=entreprise_id)
                .order_by(FamilleProduit.ordre, FamilleProduit.nom).all())
    if familles:
        valeurs += [[], ['FAMILLES DE PRODUITS'], [],
                    ['Famille', 'Description', "Nombre d'articles"]]
        for famille in familles:
            valeurs.append([famille.nom, famille.description or '',
                            len(famille.articles)])
    return valeurs


# --------------------------------------------------------------------------
# Onglets de repli pour le périmètre interne du cabinet
# --------------------------------------------------------------------------

def _synthese(exploitation, tresorerie, titre, annee):
    return [
        ['Indicateur', 'Valeur'],
        ['Périmètre', titre],
        ['Exercice', annee],
        ["Chiffre d'affaires", round(exploitation['chiffre_affaires'])],
        ['Total des charges', round(exploitation['charges'])],
        ['Résultat net', round(exploitation['resultat_net'])],
        ['Taux de marge (%)', round(exploitation['taux_marge'], 1)],
        ['Trésorerie disponible', round(tresorerie['total_disponible'])],
        ['Créances à encaisser', round(tresorerie['creances'])],
        ['Total des entrées', round(tresorerie['entrees'])],
        ['Total des sorties', round(tresorerie['sorties'])],
    ]


def _mensuel(resume):
    valeurs = [['Mois', 'Entrées', 'Sorties', 'Résultat']]
    total_v = total_a = 0.0
    for cle in sorted(resume.keys()):
        ligne = resume[cle]
        total_v += ligne['ventes']
        total_a += ligne['achats']
        valeurs.append([ligne['libelle'], round(ligne['ventes']),
                        round(ligne['achats']), round(ligne['resultat'])])
    valeurs.append(['TOTAL', round(total_v), round(total_a),
                    round(total_v - total_a)])
    return valeurs


# --------------------------------------------------------------------------
# Assemblage
# --------------------------------------------------------------------------

def construire_onglets(titre, annee, scope):
    """Prépare le contenu de tous les onglets du classeur."""
    entreprise_id = scope.get('entreprise_id')
    cree_par_id = scope.get('cree_par_id')

    # Le journal de bord et l'onglet à plat ne portent que sur le périmètre,
    # sans les bornes de dates du scope annuel, que l'on gère ici.
    scope_journal = {k: v for k, v in scope.items()
                     if k in ('entreprise_id', 'departement',
                              'interne_seulement', 'cree_par_id')}

    entetes, lignes_plates = comptes.donnees_plates(**scope)
    onglets = [
        ('Journal de bord', _journal_de_bord(annee, scope_journal)),
    ]

    if entreprise_id:
        exploitation = comptes.compte_exploitation_detaille(
            entreprise_id, annee, cree_par_id=cree_par_id)
        tresorerie = comptes.compte_tresorerie_detaille(
            entreprise_id, annee, cree_par_id=cree_par_id)
        onglets += [
            ("Compte d'exploitation",
             _compte_detaille(exploitation, "COMPTE D'EXPLOITATION")),
            ('Compte de tresorerie',
             _compte_detaille(tresorerie, 'COMPTE DE TRESORERIE')),
            ('Produits et services', _produits_et_services(entreprise_id)),
        ]
    else:
        # Périmètre interne du cabinet : pas de budget ni de catalogue,
        # on publie la synthèse et l'évolution mensuelle.
        onglets += [
            ('Synthese', _synthese(comptes.compte_exploitation(**scope),
                                   comptes.compte_tresorerie(**scope),
                                   titre, annee)),
            ('Mensuel', _mensuel(comptes.resume_mensuel(annee, **scope_journal))),
        ]

    onglets.append(('Donnees', [entetes] + lignes_plates))
    return onglets


def publier(titre, annee, scope, fichier_credentials, sheet_id=None,
            dossier_drive=None):
    """Publie le classeur complet vers Google Sheets.

    Si `sheet_id` est fourni, le classeur existant est mis à jour — son
    adresse reste donc stable, ce qui compte : c'est le lien communiqué au
    client. Sinon, un nouveau classeur est créé.

    Retourne (sheet_id, url, nombre_de_lignes, erreur).
    En cas de succès, `erreur` vaut None ; en cas d'échec, il contient la
    raison exacte, destinée à être affichée à l'utilisateur. Aucune
    exception n'est levée.
    """
    indisponible = google_integration.raison_indisponibilite(fichier_credentials)
    if indisponible:
        return None, None, 0, f"Google n'est pas utilisable : {indisponible}"

    onglets = construire_onglets(titre, annee, scope)
    # Nombre d'opérations publiées : l'onglet « Données », hors en-tête.
    nombre_lignes = max(0, len(onglets[-1][1]) - 1)

    if not sheet_id:
        nom = f"Posiges — {titre} — {annee}"
        sheet_id, url, erreur = google_integration.creer_classeur(
            nom, fichier_credentials, dossier_drive)
        if not sheet_id:
            return None, None, 0, erreur
    else:
        url = google_integration.url_classeur(sheet_id)

    reussis, erreur = google_integration.publier_classeur_complet(
        sheet_id, onglets, fichier_credentials)
    if reussis == 0:
        return None, None, 0, erreur

    return sheet_id, url, nombre_lignes, None
