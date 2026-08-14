"""
Posiges V1 — saisie des budgets.

Le gérant ouvre « Budget », choisit entre le budget d'exploitation et le
budget de trésorerie, et remplit un tableau : une ligne par rubrique, une
colonne par mois, de Mois 1 à Mois 12. Un seul montant par case — le
prévisionnel.

Ce module ne fait que deux choses : préparer la grille à afficher, et
enregistrer ce qui a été saisi. Tout le reste (confrontation au réalisé,
calcul des écarts, totaux) est recalculé à la volée par `comptes.py` : rien
n'est figé, une correction se répercute partout immédiatement.
"""
from models import (Budget, CA_NON_VENTILE, LigneBudget,
                    LIBELLE_VENTES_COMPTANT, NatureCharge,
                    PREFIXE_ENCAISSEMENT, Rubrique, RUBRIQUE_VENTES_COMPTANT,
                    TypeBudget, db)

import comptes


def obtenir_ou_creer(entreprise_id, annee, type_budget, utilisateur=None):
    """Budget de l'exercice, créé vide s'il n'existe pas encore."""
    budget = Budget.query.filter_by(entreprise_id=entreprise_id, annee=annee,
                                    type_budget=type_budget).first()
    if budget is None:
        budget = Budget(entreprise_id=entreprise_id, annee=annee,
                        type_budget=type_budget,
                        cree_par_id=utilisateur.id if utilisateur else None)
        db.session.add(budget)
        db.session.commit()
    return budget


def structure(entreprise_id, type_budget):
    """Sections et rubriques à afficher dans le formulaire de budget.

    L'ossature est exactement celle du compte correspondant : le gérant
    remplit les mêmes lignes que celles qu'il lira ensuite en « Prévu ».
    """
    if type_budget == TypeBudget.TRESORERIE:
        encaissements = [(RUBRIQUE_VENTES_COMPTANT, LIBELLE_VENTES_COMPTANT)]
        encaissements += [(PREFIXE_ENCAISSEMENT + code, libelle)
                          for code, libelle in Rubrique.ENCAISSEMENTS
                          if code != 'autres']
        decaissements = [(code, libelle)
                         for code, libelle, _ in Rubrique.CHARGES
                         if code != Rubrique.CODE_AUTRES_CHARGES]
        return [
            {'code': 'encaissements',
             'titre': 'I - Encaissements (Entrées de trésorerie)',
             'rubriques': encaissements},
            {'code': 'decaissements',
             'titre': 'II - Décaissements (Sorties de trésorerie)',
             'rubriques': decaissements},
        ]

    # Budget d'exploitation
    familles = comptes.familles_de(entreprise_id)
    produits = [(f.code_rubrique, f.nom) for f in familles]
    if not produits:
        produits = [(CA_NON_VENTILE, Rubrique.LABELS[CA_NON_VENTILE])]

    return [
        {'code': 'produits', 'titre': 'I - PRODUITS',
         'rubriques': produits},
        {'code': 'variables', 'titre': 'II - CHARGES VARIABLES',
         'rubriques': [(code, libelle) for code, libelle, nature
                       in Rubrique.CHARGES
                       if nature == NatureCharge.VARIABLE]},
        {'code': 'fixes', 'titre': 'IV - CHARGES FIXES',
         'rubriques': [(code, libelle) for code, libelle, nature
                       in Rubrique.CHARGES
                       if nature == NatureCharge.FIXE
                       and code != Rubrique.CODE_AUTRES_CHARGES]},
    ]


def enregistrer(budget, formulaire):
    """Enregistre la grille saisie.

    Les champs du formulaire sont nommés `montant_<rubrique>_<mois>`. Une
    case vide vaut zéro ; les lignes à zéro ne sont pas stockées, ce qui
    évite de conserver douze enregistrements inutiles par rubrique non
    budgétée.
    """
    existantes = {(l.rubrique, l.mois): l for l in budget.lignes}
    modifiees = 0

    for champ, valeur in formulaire.items():
        if not champ.startswith('montant_'):
            continue
        reste = champ[len('montant_'):]
        rubrique, _, mois_texte = reste.rpartition('_')
        if not rubrique:
            continue
        try:
            mois = int(mois_texte)
        except ValueError:
            continue
        if not 1 <= mois <= 12:
            continue

        montant = _nombre(valeur)
        ligne = existantes.get((rubrique, mois))

        if montant:
            if ligne is None:
                db.session.add(LigneBudget(budget_id=budget.id,
                                           rubrique=rubrique, mois=mois,
                                           montant=montant))
                modifiees += 1
            elif ligne.montant != montant:
                ligne.montant = montant
                modifiees += 1
        elif ligne is not None:
            db.session.delete(ligne)
            modifiees += 1

    if budget.type_budget == TypeBudget.TRESORERIE:
        budget.solde_initial = _nombre(formulaire.get('solde_initial'))

    db.session.commit()
    return modifiees


def _nombre(valeur):
    """Lit un montant saisi, en tolérant les espaces et la virgule décimale."""
    if valeur is None:
        return 0.0
    texte = str(valeur).strip().replace(' ', '').replace(' ', '')
    texte = texte.replace(',', '.')
    if not texte:
        return 0.0
    try:
        return round(float(texte), 2)
    except ValueError:
        return 0.0


def recopier_exercice_precedent(budget):
    """Reprend le budget de l'année précédente comme point de départ.

    Beaucoup de rubriques ne bougent pas d'une année sur l'autre — le loyer,
    les assurances, les honoraires. Repartir de l'existant évite de ressaisir
    douze mois de charges fixes.
    """
    source = Budget.query.filter_by(entreprise_id=budget.entreprise_id,
                                    annee=budget.annee - 1,
                                    type_budget=budget.type_budget).first()
    if source is None:
        return 0

    existantes = {(l.rubrique, l.mois) for l in budget.lignes}
    reprises = 0
    for ligne in source.lignes:
        if (ligne.rubrique, ligne.mois) in existantes or not ligne.montant:
            continue
        db.session.add(LigneBudget(budget_id=budget.id, rubrique=ligne.rubrique,
                                   mois=ligne.mois, montant=ligne.montant))
        reprises += 1

    if budget.type_budget == TypeBudget.TRESORERIE and not budget.solde_initial:
        budget.solde_initial = source.solde_initial

    db.session.commit()
    return reprises


def synthese(entreprise_id, annee):
    """Où en est la saisie des budgets d'un exercice."""
    resultat = {}
    for type_budget in TypeBudget.CHOIX:
        budget = Budget.query.filter_by(entreprise_id=entreprise_id,
                                        annee=annee,
                                        type_budget=type_budget).first()
        resultat[type_budget] = {
            'budget': budget,
            'renseigne': bool(budget and budget.est_renseigne()),
            'total': sum(l.montant or 0 for l in budget.lignes) if budget else 0.0,
        }
    return resultat
