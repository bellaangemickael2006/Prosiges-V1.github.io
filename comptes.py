"""
Calculs comptables automatiques à partir du journal d'opérations.

Rien n'est saisi manuellement : le compte d'exploitation, le compte de
trésorerie et les résumés sont des agrégations en direct de la table Operation.

Le "périmètre" (scope) est soit un département interne, soit une entreprise
accompagnée. On le passe systématiquement via `departement=` ou `entreprise_id=`.
"""
from calendar import monthrange
from datetime import date

from sqlalchemy import extract, func

from models import (Budget, CA_NON_VENTILE, FamilleProduit,
                    LIBELLE_VENTES_COMPTANT, ModePaiement, NatureCharge,
                    Operation, PREFIXE_CHARGE, PREFIXE_ENCAISSEMENT, Rubrique,
                    RUBRIQUE_VENTES_COMPTANT, TypeBudget, TypeOperation)

MOIS_FR = {
    '01': 'Janvier', '02': 'Février', '03': 'Mars', '04': 'Avril',
    '05': 'Mai', '06': 'Juin', '07': 'Juillet', '08': 'Août',
    '09': 'Septembre', '10': 'Octobre', '11': 'Novembre', '12': 'Décembre',
}


def requete_base(departement=None, entreprise_id=None, date_debut=None, date_fin=None,
                 interne_seulement=False, cree_par_id=None):
    """Construit la requête filtrée sur le périmètre demandé.

    `cree_par_id` restreint aux saisies d'un utilisateur donné : c'est ce qui
    donne à l'utilisateur standard un tableau de bord limité à ses propres
    opérations, sans qu'il voie celles de ses collègues.
    """
    q = Operation.query
    if entreprise_id is not None:
        q = q.filter(Operation.entreprise_id == entreprise_id)
    elif interne_seulement:
        q = q.filter(Operation.entreprise_id.is_(None))
    if departement:
        q = q.filter(Operation.departement == departement)
    if date_debut:
        q = q.filter(Operation.date_operation >= date_debut)
    if date_fin:
        q = q.filter(Operation.date_operation <= date_fin)
    if cree_par_id is not None:
        q = q.filter(Operation.cree_par_id == cree_par_id)
    return q


def compte_exploitation(**scope):
    """Chiffre d'affaires, charges, résultat net et détail par rubrique.

    Seules les rubriques de chiffre d'affaires (`ca:`) alimentent le chiffre
    d'affaires. Un emprunt bancaire, une subvention ou le recouvrement d'une
    créance sont des entrées d'argent, pas des ventes : ils font grossir la
    trésorerie sans rien produire. Les ranger dans le chiffre d'affaires
    donnerait une rentabilité fictive — exactement le piège que le compte de
    trésorerie sert à éviter.
    """
    lignes = (
        requete_base(**scope)
        .with_entities(
            Operation.categorie,
            Operation.type_operation,
            func.sum(Operation.montant).label('total'),
            func.count(Operation.id).label('nombre'),
        )
        .group_by(Operation.categorie, Operation.type_operation)
        .order_by(func.sum(Operation.montant).desc())
        .all()
    )

    # Les catégories sont stockées sous forme de codes du plan de rubriques :
    # on les rend lisibles ici, une bonne fois, pour tous les affichages.
    libelles = libelles_rubriques(scope.get('entreprise_id'))

    produits, charges, encaissements = [], [], []
    for code, type_operation, total, nombre in lignes:
        element = {'categorie': libelles.get(code, code), 'code': code,
                   'total': total or 0.0, 'nombre': nombre}
        if type_operation == TypeOperation.ACHAT:
            charges.append(element)
        elif Rubrique.est_encaissement(code):
            encaissements.append(element)
        else:
            produits.append(element)

    total_ca = sum(p['total'] for p in produits)
    total_charges = sum(c['total'] for c in charges)
    resultat = total_ca - total_charges
    marge = (resultat / total_ca * 100) if total_ca else 0.0

    return {
        'chiffre_affaires': total_ca,
        'charges': total_charges,
        'resultat_net': resultat,
        'taux_marge': marge,
        'produits': produits,
        'detail_charges': charges,
        # Entrées d'argent qui ne sont pas du chiffre d'affaires
        'autres_encaissements': encaissements,
        'total_autres_encaissements': sum(e['total'] for e in encaissements),
    }


def compte_tresorerie(**scope):
    """Soldes par mode de paiement. Ventes = entrées, achats = sorties."""
    soldes = {mode: 0.0 for mode in ModePaiement.CHOIX}
    entrees = 0.0
    sorties = 0.0

    for op in requete_base(**scope).all():
        soldes[op.mode_paiement] = soldes.get(op.mode_paiement, 0.0) + op.montant_signe()
        if op.montant_signe() >= 0:
            entrees += op.montant
        else:
            sorties += op.montant

    soldes['entrees'] = entrees
    soldes['sorties'] = sorties
    # Le crédit n'est pas de la trésorerie disponible : on l'isole du total
    soldes['total_disponible'] = (
        soldes[ModePaiement.CASH] + soldes[ModePaiement.MOBILE_MONEY] + soldes[ModePaiement.BANQUE]
    )
    soldes['creances'] = soldes[ModePaiement.CREDIT]
    return soldes


def resume_mensuel(annee, **scope):
    """Ventes / achats / résultat mois par mois pour une année donnée.

    L'année est filtrée par un intervalle de dates et le mois extrait avec
    `extract()` plutôt qu'avec `strftime()` : cette dernière n'existe que sous
    SQLite et le tableau serait resté vide une fois l'application déployée sur
    PostgreSQL.
    """
    scope_annuel = dict(scope)
    scope_annuel.setdefault('date_debut', date(annee, 1, 1))
    scope_annuel.setdefault('date_fin', date(annee, 12, 31))

    colonne_mois = extract('month', Operation.date_operation).label('mois')
    lignes = (
        requete_base(**scope_annuel)
        .with_entities(
            colonne_mois,
            Operation.type_operation,
            func.sum(Operation.montant).label('total'),
        )
        .group_by(colonne_mois, Operation.type_operation)
        .all()
    )

    resultat = {
        f'{m:02d}': {'libelle': MOIS_FR[f'{m:02d}'], 'ventes': 0.0, 'achats': 0.0, 'resultat': 0.0}
        for m in range(1, 13)
    }
    for mois, type_op, total in lignes:
        cle = 'achats' if type_op == TypeOperation.ACHAT else 'ventes'
        resultat[f'{int(mois):02d}'][cle] += total or 0.0

    for val in resultat.values():
        val['resultat'] = val['ventes'] - val['achats']
    return resultat


def top_clients(limite=10, **scope):
    """Meilleurs clients par chiffre d'affaires généré."""
    from models import Client

    lignes = (
        requete_base(**scope)
        .filter(Operation.type_operation == TypeOperation.VENTE)
        .filter(Operation.client_id.isnot(None))
        .with_entities(
            Operation.client_id,
            func.sum(Operation.montant).label('total'),
            func.count(Operation.id).label('nombre'),
        )
        .group_by(Operation.client_id)
        .order_by(func.sum(Operation.montant).desc())
        .limit(limite)
        .all()
    )

    resultat = []
    for client_id, total, nombre in lignes:
        client = Client.query.get(client_id)
        resultat.append({
            'nom': client.nom if client else 'Inconnu',
            'total': total,
            'nombre': nombre,
        })
    return resultat


def evolution_periode(annee_courante, **scope):
    """Compare l'année courante à l'année précédente (CA, charges, résultat)."""
    courante = compte_exploitation(
        date_debut=date(annee_courante, 1, 1),
        date_fin=date(annee_courante, 12, 31),
        **scope
    )
    precedente = compte_exploitation(
        date_debut=date(annee_courante - 1, 1, 1),
        date_fin=date(annee_courante - 1, 12, 31),
        **scope
    )

    def variation(actuel, ancien):
        if not ancien:
            return None
        return (actuel - ancien) / abs(ancien) * 100

    return {
        'courante': courante,
        'precedente': precedente,
        'var_ca': variation(courante['chiffre_affaires'], precedente['chiffre_affaires']),
        'var_charges': variation(courante['charges'], precedente['charges']),
        'var_resultat': variation(courante['resultat_net'], precedente['resultat_net']),
    }


def bornes_mois(annee, mois):
    """Retourne (premier_jour, dernier_jour) d'un mois donné."""
    dernier = monthrange(annee, mois)[1]
    return date(annee, mois, 1), date(annee, mois, dernier)


def tresorerie_cumulee(annee, **scope):
    """Solde de trésorerie cumulé mois par mois : montre si l'entreprise
    s'enrichit ou s'épuise au fil de l'année."""
    resume = resume_mensuel(annee, **scope)
    cumul = 0.0
    points = []
    for cle in sorted(resume.keys()):
        cumul += resume[cle]['resultat']
        points.append({'mois': resume[cle]['libelle'], 'cumul': cumul})
    return points


def repartition_modes_paiement(**scope):
    """Volume encaissé/décaissé par mode de paiement (valeurs absolues),
    pour visualiser le poids du cash, du mobile money et de la banque."""
    volumes = {mode: 0.0 for mode in ModePaiement.CHOIX}
    for op in requete_base(**scope).all():
        volumes[op.mode_paiement] = volumes.get(op.mode_paiement, 0.0) + op.montant
    return volumes


def donnees_graphiques(annee, **scope):
    """Rassemble en une seule structure toutes les séries affichées par les
    graphiques du tableau de bord. Transmise au navigateur en JSON.

    Tous les graphiques portent sur le MÊME exercice. Les agrégations qui ne
    prennent pas l'année en argument (structure des charges, modes de paiement,
    principaux clients) sont donc bornées explicitement au 1er janvier et au
    31 décembre de l'année demandée : sans cela elles cumulaient l'historique
    complet et affichaient des données alors que l'évolution mensuelle et la
    trésorerie cumulée, elles, restaient vides.
    """
    resume = resume_mensuel(annee, **scope)
    cles = sorted(resume.keys())

    scope_annuel = dict(scope)
    scope_annuel.setdefault('date_debut', date(annee, 1, 1))
    scope_annuel.setdefault('date_fin', date(annee, 12, 31))

    exploitation = compte_exploitation(**scope_annuel)
    modes = repartition_modes_paiement(**scope_annuel)
    cumul = tresorerie_cumulee(annee, **scope)
    clients = top_clients(limite=6, **scope_annuel)

    return {
        'mois': [resume[c]['libelle'][:4] for c in cles],
        'ventes': [round(resume[c]['ventes']) for c in cles],
        'achats': [round(resume[c]['achats']) for c in cles],
        'resultats': [round(resume[c]['resultat']) for c in cles],
        'cumul': [round(p['cumul']) for p in cumul],
        'charges_labels': [c['categorie'] for c in exploitation['detail_charges'][:7]],
        'charges_valeurs': [round(c['total']) for c in exploitation['detail_charges'][:7]],
        'produits_labels': [p['categorie'] for p in exploitation['produits'][:7]],
        'produits_valeurs': [round(p['total']) for p in exploitation['produits'][:7]],
        'modes_labels': [ModePaiement.LABELS[m] for m in ModePaiement.CHOIX],
        'modes_valeurs': [round(modes[m]) for m in ModePaiement.CHOIX],
        'clients_labels': [c['nom'][:24] for c in clients],
        'clients_valeurs': [round(c['total']) for c in clients],
    }


# ==========================================================================
#  COMPTE D'EXPLOITATION ET COMPTE DE TRÉSORERIE DÉTAILLÉS
#
#  Ce sont les deux tableaux remis par le cabinet. Ils partagent la même
#  ossature : des sections de rubriques, et pour CHAQUE MOIS trois colonnes
#  — Prévu (le budget saisi par le gérant), Réalisé (calculé en direct
#  depuis le journal) et Écart, qui vaut toujours Réalisé − Prévu.
#
#  Rien n'est stocké : tout est recalculé à la demande. Une opération
#  corrigée se répercute donc immédiatement sur les deux comptes, sur les
#  exports Excel et sur le classeur Google Sheets.
# ==========================================================================

MOIS_COURTS = ['Janv.', 'Févr.', 'Mars', 'Avr.', 'Mai', 'Juin',
               'Juil.', 'Août', 'Sept.', 'Oct.', 'Nov.', 'Déc.']


def _douze_zeros():
    return [0.0] * 12


def entetes_mois():
    """Les douze mois, pour les en-têtes de colonnes."""
    return [{'numero': m, 'libelle': MOIS_FR[f'{m:02d}'], 'court': MOIS_COURTS[m - 1]}
            for m in range(1, 13)]


def familles_de(entreprise_id):
    """Familles de produits d'une entreprise, dans l'ordre d'affichage."""
    if entreprise_id is None:
        return []
    return (FamilleProduit.query
            .filter_by(entreprise_id=entreprise_id)
            .order_by(FamilleProduit.ordre, FamilleProduit.nom).all())


def _realise_par_rubrique(annee, filtre_mode=None, **scope):
    """Montants réalisés, ventilés par rubrique et par mois.

    Retourne {code_rubrique: [12 montants]}. `filtre_mode` permet d'exclure
    les opérations à crédit : une vente à crédit compte dans le chiffre
    d'affaires mais pas dans la trésorerie, puisque l'argent n'est pas entré.
    """
    scope_annuel = dict(scope)
    scope_annuel.setdefault('date_debut', date(annee, 1, 1))
    scope_annuel.setdefault('date_fin', date(annee, 12, 31))

    colonne_mois = extract('month', Operation.date_operation).label('mois')
    requete = requete_base(**scope_annuel)
    if filtre_mode == 'encaisse':
        requete = requete.filter(Operation.mode_paiement != ModePaiement.CREDIT)

    lignes = (requete
              .with_entities(Operation.categorie, colonne_mois,
                             Operation.type_operation,
                             func.sum(Operation.montant).label('total'))
              .group_by(Operation.categorie, colonne_mois,
                        Operation.type_operation)
              .all())

    resultat = {}
    for categorie, mois, type_operation, total in lignes:
        code = categorie or CA_NON_VENTILE
        # Un code de charge sur une entrée (ou l'inverse) viendrait d'une
        # saisie incohérente : on le range du côté du sens réel.
        if type_operation == TypeOperation.ACHAT and not Rubrique.est_charge(code):
            code = Rubrique.CODE_AUTRES_CHARGES
        elif type_operation != TypeOperation.ACHAT and Rubrique.est_charge(code):
            code = CA_NON_VENTILE

        resultat.setdefault(code, _douze_zeros())
        resultat[code][int(mois) - 1] += float(total or 0.0)
    return resultat


def _budget_de(entreprise_id, annee, type_budget):
    if entreprise_id is None:
        return None
    return Budget.query.filter_by(entreprise_id=entreprise_id, annee=annee,
                                  type_budget=type_budget).first()


def _prevu_par_rubrique(budget):
    """Montants budgétés, ventilés par rubrique et par mois."""
    prevu = {}
    if budget is None:
        return prevu
    for ligne in budget.lignes:
        if not 1 <= (ligne.mois or 0) <= 12:
            continue
        prevu.setdefault(ligne.rubrique, _douze_zeros())
        prevu[ligne.rubrique][ligne.mois - 1] += float(ligne.montant or 0.0)
    return prevu


def _ligne(code, libelle, prevu, realise, garder_vide=True):
    """Construit une ligne de compte avec ses trois séries mensuelles."""
    serie_prevu = prevu.get(code) or _douze_zeros()
    serie_realise = realise.get(code) or _douze_zeros()
    serie_ecart = [r - p for p, r in zip(serie_prevu, serie_realise)]

    total_prevu = sum(serie_prevu)
    total_realise = sum(serie_realise)

    if not garder_vide and not total_prevu and not total_realise:
        return None

    return {
        'code': code,
        'libelle': libelle,
        'prevu': serie_prevu,
        'realise': serie_realise,
        'ecart': serie_ecart,
        'total_prevu': total_prevu,
        'total_realise': total_realise,
        'total_ecart': total_realise - total_prevu,
    }


def _totaliser(lignes, code, libelle):
    """Somme d'un ensemble de lignes, colonne par colonne."""
    prevu = _douze_zeros()
    realise = _douze_zeros()
    for ligne in lignes:
        for index in range(12):
            prevu[index] += ligne['prevu'][index]
            realise[index] += ligne['realise'][index]
    return {
        'code': code,
        'libelle': libelle,
        'prevu': prevu,
        'realise': realise,
        'ecart': [r - p for p, r in zip(prevu, realise)],
        'total_prevu': sum(prevu),
        'total_realise': sum(realise),
        'total_ecart': sum(realise) - sum(prevu),
    }


def _difference(gauche, droite, code, libelle):
    """Ligne calculée : gauche − droite (marge brute, résultat, solde)."""
    prevu = [a - b for a, b in zip(gauche['prevu'], droite['prevu'])]
    realise = [a - b for a, b in zip(gauche['realise'], droite['realise'])]
    return {
        'code': code,
        'libelle': libelle,
        'prevu': prevu,
        'realise': realise,
        'ecart': [r - p for p, r in zip(prevu, realise)],
        'total_prevu': sum(prevu),
        'total_realise': sum(realise),
        'total_ecart': sum(realise) - sum(prevu),
    }


def compte_exploitation_detaille(entreprise_id, annee, cree_par_id=None):
    """Compte d'exploitation mensuel au format du cabinet.

    I - PRODUITS (une ligne par famille) → Total Produit
    II - CHARGES VARIABLES               → Total Charges Variables
    III - MARGE BRUTE                    = Total Produit − Charges variables
    IV - CHARGES FIXES                   → Total Charges Fixes
    V - RÉSULTAT D'EXPLOITATION          = Marge brute − Charges fixes
    """
    scope = {'entreprise_id': entreprise_id}
    if cree_par_id is not None:
        scope['cree_par_id'] = cree_par_id

    realise = _realise_par_rubrique(annee, **scope)
    budget = _budget_de(entreprise_id, annee, TypeBudget.EXPLOITATION)
    prevu = _prevu_par_rubrique(budget)

    # --- I. Produits : une ligne par famille de produits ---
    familles = familles_de(entreprise_id)
    lignes_produits = [
        _ligne(f.code_rubrique, f"Chiffre d'affaires — {f.nom}", prevu, realise)
        for f in familles
    ]
    # Le chiffre d'affaires non ventilé n'apparaît que s'il porte un montant,
    # ou tant qu'aucune famille n'a été créée.
    non_ventile = _ligne(
        CA_NON_VENTILE, Rubrique.LABELS[CA_NON_VENTILE], prevu, realise,
        garder_vide=not familles)
    if non_ventile:
        lignes_produits.append(non_ventile)

    total_produits = _totaliser(lignes_produits, 'total_produits', 'Total Produit')

    # --- II. Charges variables ---
    lignes_variables = [
        _ligne(code, libelle, prevu, realise,
               garder_vide=(code != Rubrique.CODE_AUTRES_CHARGES))
        for code, libelle, nature in Rubrique.CHARGES
        if nature == NatureCharge.VARIABLE
    ]
    lignes_variables = [l for l in lignes_variables if l]
    total_variables = _totaliser(lignes_variables, 'total_variables',
                                 'Total Charges Variables')

    marge_brute = _difference(total_produits, total_variables,
                              'marge_brute', 'III - MARGE BRUTE')

    # --- IV. Charges fixes ---
    lignes_fixes = [
        _ligne(code, libelle, prevu, realise,
               garder_vide=(code != Rubrique.CODE_AUTRES_CHARGES))
        for code, libelle, nature in Rubrique.CHARGES
        if nature == NatureCharge.FIXE
    ]
    lignes_fixes = [l for l in lignes_fixes if l]
    total_fixes = _totaliser(lignes_fixes, 'total_fixes', 'Total Charges Fixes')

    resultat = _difference(marge_brute, total_fixes, 'resultat',
                           "V - RÉSULTAT D'EXPLOITATION (Bénéfice ou Perte)")

    return {
        'annee': annee,
        'mois': entetes_mois(),
        'budget': budget,
        'sections': [
            {'code': 'produits', 'titre': 'I - PRODUITS',
             'lignes': lignes_produits, 'total': total_produits},
            {'code': 'variables', 'titre': 'II - CHARGES VARIABLES',
             'lignes': lignes_variables, 'total': total_variables},
            {'code': 'fixes', 'titre': 'IV - CHARGES FIXES',
             'lignes': lignes_fixes, 'total': total_fixes},
        ],
        'marge_brute': marge_brute,
        'resultat': resultat,
    }


def compte_tresorerie_detaille(entreprise_id, annee, cree_par_id=None):
    """Compte de trésorerie mensuel au format du cabinet.

    I - Encaissements                    → Total Encaissement (A)
    II - Décaissements                   → Total Décaissement (B)
    Solde de début de période (C), puis D = C + A − B, reporté d'un mois
    sur l'autre.

    Seul l'argent réellement mouvementé entre ici : une vente à crédit est
    du chiffre d'affaires, pas de la trésorerie.
    """
    scope = {'entreprise_id': entreprise_id}
    if cree_par_id is not None:
        scope['cree_par_id'] = cree_par_id

    encaisse = _realise_par_rubrique(annee, filtre_mode='encaisse', **scope)
    budget = _budget_de(entreprise_id, annee, TypeBudget.TRESORERIE)
    prevu = _prevu_par_rubrique(budget)

    # --- I. Encaissements ---
    # « Ventes au comptant » regroupe toutes les ventes effectivement
    # encaissées, quelle que soit la famille de produits concernée.
    ventes_comptant = _douze_zeros()
    for code, serie in encaisse.items():
        if Rubrique.est_ca(code):
            for index in range(12):
                ventes_comptant[index] += serie[index]
    encaisse[RUBRIQUE_VENTES_COMPTANT] = ventes_comptant

    lignes_encaissements = [
        _ligne(RUBRIQUE_VENTES_COMPTANT, LIBELLE_VENTES_COMPTANT, prevu, encaisse)
    ]
    for code, libelle in Rubrique.ENCAISSEMENTS:
        complet = PREFIXE_ENCAISSEMENT + code
        ligne = _ligne(complet, libelle, prevu, encaisse,
                       garder_vide=(code != 'autres'))
        if ligne:
            lignes_encaissements.append(ligne)

    total_encaissements = _totaliser(lignes_encaissements, 'total_encaissements',
                                     'Total Encaissement (A)')

    # --- II. Décaissements : les mêmes postes de charges, décaissés ---
    lignes_decaissements = [
        _ligne(code, libelle, prevu, encaisse,
               garder_vide=(code != Rubrique.CODE_AUTRES_CHARGES))
        for code, libelle, _nature in Rubrique.CHARGES
    ]
    lignes_decaissements = [l for l in lignes_decaissements if l]
    total_decaissements = _totaliser(lignes_decaissements, 'total_decaissements',
                                     'Total Décaissement (B)')

    # --- Soldes reportés de mois en mois ---
    solde_initial_prevu = float(budget.solde_initial or 0.0) if budget else 0.0
    solde_initial_realise = _solde_reporte(entreprise_id, annee, cree_par_id)

    debut_prevu, debut_realise = _douze_zeros(), _douze_zeros()
    fin_prevu, fin_realise = _douze_zeros(), _douze_zeros()

    courant_prevu = solde_initial_prevu
    courant_realise = solde_initial_realise
    for index in range(12):
        debut_prevu[index] = courant_prevu
        debut_realise[index] = courant_realise
        courant_prevu += (total_encaissements['prevu'][index]
                          - total_decaissements['prevu'][index])
        courant_realise += (total_encaissements['realise'][index]
                            - total_decaissements['realise'][index])
        fin_prevu[index] = courant_prevu
        fin_realise[index] = courant_realise

    def _serie(libelle, code, serie_prevu, serie_realise):
        return {
            'code': code, 'libelle': libelle,
            'prevu': serie_prevu, 'realise': serie_realise,
            'ecart': [r - p for p, r in zip(serie_prevu, serie_realise)],
            # Un solde ne se cumule pas : le total de la colonne annuelle est
            # celui de décembre, pas la somme des douze mois.
            'total_prevu': serie_prevu[-1],
            'total_realise': serie_realise[-1],
            'total_ecart': serie_realise[-1] - serie_prevu[-1],
        }

    return {
        'annee': annee,
        'mois': entetes_mois(),
        'budget': budget,
        'sections': [
            {'code': 'encaissements',
             'titre': 'I - Encaissements (Entrées de trésorerie)',
             'lignes': lignes_encaissements, 'total': total_encaissements},
            {'code': 'decaissements',
             'titre': 'II - Décaissements (Sorties de trésorerie)',
             'lignes': lignes_decaissements, 'total': total_decaissements},
        ],
        'solde_debut': _serie('Solde de trésorerie en début de période (C)',
                              'solde_debut', debut_prevu, debut_realise),
        'solde_fin': _serie('Solde de trésorerie en fin de période (D = C + A − B)',
                            'solde_fin', fin_prevu, fin_realise),
    }


def _solde_reporte(entreprise_id, annee, cree_par_id=None):
    """Trésorerie accumulée avant le 1er janvier de l'exercice.

    C'est le solde réel de début de période : tout ce qui a été encaissé
    moins tout ce qui a été décaissé au cours des exercices précédents.
    """
    scope = {'entreprise_id': entreprise_id, 'date_fin': date(annee - 1, 12, 31)}
    if cree_par_id is not None:
        scope['cree_par_id'] = cree_par_id

    solde = 0.0
    lignes = (requete_base(**scope)
              .filter(Operation.mode_paiement != ModePaiement.CREDIT)
              .with_entities(Operation.type_operation,
                             func.sum(Operation.montant))
              .group_by(Operation.type_operation).all())
    for type_operation, total in lignes:
        montant = float(total or 0.0)
        solde += -montant if type_operation == TypeOperation.ACHAT else montant
    return solde


def rubriques_de_saisie(entreprise_id, type_operation):
    """Liste fermée proposée au moment de la saisie d'une opération.

    Le cabinet a été explicite : « la catégorie, ce n'est pas le client qui
    la met, il la sélectionne ». Une entrée propose les familles de produits
    et les encaissements de trésorerie ; une sortie propose les charges.
    """
    if type_operation == TypeOperation.ACHAT:
        return [
            {'code': code, 'libelle': libelle,
             'groupe': ('Charges variables' if nature == NatureCharge.VARIABLE
                        else 'Charges fixes')}
            for code, libelle, nature in Rubrique.CHARGES
        ]

    options = [
        {'code': f.code_rubrique, 'libelle': f.nom,
         'groupe': "Chiffre d'affaires par famille"}
        for f in familles_de(entreprise_id)
    ]
    options.append({'code': CA_NON_VENTILE,
                    'libelle': Rubrique.LABELS[CA_NON_VENTILE],
                    'groupe': "Chiffre d'affaires par famille"})
    options += [
        {'code': PREFIXE_ENCAISSEMENT + code, 'libelle': libelle,
         'groupe': 'Autres encaissements'}
        for code, libelle in Rubrique.ENCAISSEMENTS
    ]
    return options


def libelles_rubriques(entreprise_id):
    """Dictionnaire {code: libellé} couvrant tout le plan d'une entreprise."""
    libelles = dict(Rubrique.LABELS)
    libelles[RUBRIQUE_VENTES_COMPTANT] = LIBELLE_VENTES_COMPTANT
    for famille in familles_de(entreprise_id):
        libelles[famille.code_rubrique] = f"Chiffre d'affaires — {famille.nom}"
    return libelles


def journal_de_bord(annee=None, **scope):
    """Journal de bord au format demandé : entrées, sorties et solde cumulé.

    Colonnes : Date, Libellé, Catégorie, Entrée, Sortie, Mode de paiement,
    Solde, Référence de la pièce justificative.

    Le solde est cumulatif. C'est lui qui fait du journal un outil de
    contrôle : un solde qui devient négatif signale une erreur de saisie ou
    un problème de caisse.
    """
    scope_annuel = dict(scope)
    if annee:
        scope_annuel.setdefault('date_debut', date(annee, 1, 1))
        scope_annuel.setdefault('date_fin', date(annee, 12, 31))

    operations = (requete_base(**scope_annuel)
                  .order_by(Operation.date_operation, Operation.id).all())

    libelles = libelles_rubriques(scope.get('entreprise_id'))
    solde = 0.0
    lignes = []
    for operation in operations:
        entree = operation.montant if operation.signe() >= 0 else 0.0
        sortie = operation.montant if operation.signe() < 0 else 0.0
        solde += entree - sortie
        lignes.append({
            'operation': operation,
            'date': operation.date_operation,
            'libelle': operation.libelle or '',
            'categorie': libelles.get(operation.categorie, operation.categorie),
            'entree': entree,
            'sortie': sortie,
            'mode_paiement': ModePaiement.LABELS.get(operation.mode_paiement,
                                                     operation.mode_paiement),
            'solde': solde,
            'preuve': operation.reference or operation.numero_facture or '',
            'lien_preuve': operation.fichier_url or operation.fichier_local or '',
        })
    return lignes


def donnees_plates(**scope):
    """Journal « à plat », une ligne par opération, enrichi de colonnes
    calculées (année, mois, trimestre, montant signé...).

    C'est le format qui se prête aux tris, aux filtres et aux tableaux
    croisés du tableur : une table simple, sans formule ni sous-total, où
    chaque colonne est une donnée directement exploitable.
    """
    entetes = [
        'Date', 'Année', 'Mois', 'Nom du mois', 'Trimestre',
        'Type', 'Rubrique', 'Libellé', 'Client', 'Périmètre',
        'Entrée', 'Sortie', 'Solde',
        'Mode de paiement', 'Référence', 'N° Facture', 'Saisi par',
    ]

    libelles = libelles_rubriques(scope.get('entreprise_id'))
    lignes = []
    solde = 0.0
    operations = (requete_base(**scope)
                  .order_by(Operation.date_operation, Operation.id).all())
    for op in operations:
        date_op = op.date_operation
        mois = f'{date_op.month:02d}'
        signe = op.montant_signe()
        entree = op.montant if signe >= 0 else 0
        sortie = op.montant if signe < 0 else 0
        solde += entree - sortie
        lignes.append([
            date_op.strftime('%Y-%m-%d'),
            date_op.year,
            date_op.month,
            MOIS_FR[mois],
            f'T{(date_op.month - 1) // 3 + 1}',
            TypeOperation.LABELS_COURTS.get(op.type_operation, op.type_operation),
            libelles.get(op.categorie, op.categorie),
            op.libelle or '',
            op.client.nom if op.client else 'Sans client',
            op.libelle_perimetre(),
            round(entree),
            round(sortie),
            round(solde),
            ModePaiement.LABELS.get(op.mode_paiement, op.mode_paiement),
            op.reference or '',
            op.numero_facture or '',
            op.cree_par.nom if op.cree_par else '',
        ])
    return entetes, lignes


def analyse_automatique(exploitation, tresorerie, resume_mois):
    """Génère des constats textuels simples, utilisés dans les rapports PDF.

    Ce sont des observations factuelles tirées des chiffres — le consultant
    reste libre d'ajouter ses propres recommandations dans le rapport.
    """
    constats = []

    ca = exploitation['chiffre_affaires']
    charges = exploitation['charges']
    resultat = exploitation['resultat_net']

    if ca == 0:
        constats.append("Aucune vente enregistrée sur la période analysée.")
        return constats

    if resultat > 0:
        constats.append(
            f"L'activité est bénéficiaire sur la période : résultat de "
            f"{resultat:,.0f} FCFA pour un chiffre d'affaires de {ca:,.0f} FCFA "
            f"(taux de marge de {exploitation['taux_marge']:.1f} %).".replace(',', ' ')
        )
    else:
        constats.append(
            f"L'activité est déficitaire sur la période : les charges "
            f"({charges:,.0f} FCFA) dépassent le chiffre d'affaires "
            f"({ca:,.0f} FCFA), soit une perte de {abs(resultat):,.0f} FCFA.".replace(',', ' ')
        )

    # Poste de charge dominant
    if exploitation['detail_charges']:
        principale = exploitation['detail_charges'][0]
        part = principale['total'] / charges * 100 if charges else 0
        constats.append(
            f"Le premier poste de charges est « {principale['categorie']} » avec "
            f"{principale['total']:,.0f} FCFA, soit {part:.1f} % du total des charges."
            .replace(',', ' ')
        )

    # Créances en attente
    if tresorerie['creances'] > 0:
        constats.append(
            f"Un montant de {tresorerie['creances']:,.0f} FCFA est enregistré en crédit : "
            f"ces sommes restent à encaisser et ne constituent pas de la trésorerie disponible."
            .replace(',', ' ')
        )

    # Meilleur et pire mois
    mois_actifs = {k: v for k, v in resume_mois.items() if v['ventes'] or v['achats']}
    if len(mois_actifs) >= 2:
        meilleur = max(mois_actifs.items(), key=lambda kv: kv[1]['resultat'])
        pire = min(mois_actifs.items(), key=lambda kv: kv[1]['resultat'])
        constats.append(
            f"Le meilleur mois est {meilleur[1]['libelle']} "
            f"({meilleur[1]['resultat']:,.0f} FCFA de résultat) et le plus faible "
            f"{pire[1]['libelle']} ({pire[1]['resultat']:,.0f} FCFA).".replace(',', ' ')
        )

    # Trésorerie disponible
    constats.append(
        f"La trésorerie disponible (espèces + mobile money + banque) s'établit à "
        f"{tresorerie['total_disponible']:,.0f} FCFA.".replace(',', ' ')
    )

    return constats

