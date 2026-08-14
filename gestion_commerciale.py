"""
Posiges V1 — logique de gestion commerciale.

Ce module concentre les règles métier des ventes, du stock et des rapports.
Il applique le principe posé dès le départ : le gérant de TPE saisit une
seule fois, l'application déduit tout le reste.

Trois enchaînements automatiques :

  1. Un reçu encaissé  →  écriture au journal des opérations
                          (le chiffre d'affaires se met à jour seul)

  2. Un reçu émis      →  sortie de stock des produits vendus
                          (les quantités se mettent à jour seules)

  3. Une entrée stock  →  possibilité d'enregistrer l'achat au journal
                          (la charge est comptabilisée en même temps)
"""
from datetime import date, datetime

from sqlalchemy import func

import comptes
from models import (Article, CA_NON_VENTILE, ConditionVente, Document,
                    Fournisseur, LigneDocument, ModePaiement, MouvementStock,
                    NatureArticle, Operation, PREFIXE_ENCAISSEMENT,
                    ProfilFacturation, Rapport, SectionRapport, StatutDocument,
                    TypeDocument, TypeMouvement, TypeOperation, db)

# Rubrique de trésorerie alimentée par le règlement d'une facture émise.
CODE_CREANCES_CLIENTS = PREFIXE_ENCAISSEMENT + 'creances_clients'


# ==========================================================================
# NUMÉROTATION DES DOCUMENTS
# ==========================================================================

PREFIXES = {TypeDocument.FACTURE: 'FAC', TypeDocument.PROFORMA: 'PRO',
            TypeDocument.RECU: 'REC'}


def prochain_numero(type_document, entreprise_id):
    """Numéro séquentiel par entreprise, par type et par année.

    Format : FAC-2026-00001 / PRO-2026-00001 / REC-2026-00001
    """
    prefixe = PREFIXES.get(type_document, 'DOC')
    annee = date.today().year
    base = f'{prefixe}-{annee}-'

    dernier = (Document.query
               .filter(Document.entreprise_id == entreprise_id,
                       Document.numero.like(f'{base}%'))
               .order_by(Document.numero.desc())
               .first())

    numero = int(dernier.numero.split('-')[-1]) + 1 if dernier else 1
    return f'{base}{numero:05d}'


def prochain_numero_commande(entreprise_id):
    """N° de commande de l'en-tête des modèles du cabinet.

    Format : 26 008 C0035 / 001 — année sur deux chiffres, rang du document
    dans l'année, puis rang de la commande. Il identifie l'affaire, quand le
    numéro de facture identifie la pièce.
    """
    annee = date.today().year
    rang = (Document.query
            .filter(Document.entreprise_id == entreprise_id,
                    Document.numero_commande.isnot(None)).count()) + 1
    return f'{annee % 100:02d} {rang:03d} C{rang:04d} / 001'


# ==========================================================================
# PROFIL DE FACTURATION
# ==========================================================================

def profil_de(entreprise):
    """Retourne le profil de facturation, en le créant au besoin.

    Ainsi le formulaire est toujours accessible, même avant toute saisie.
    """
    if entreprise is None:
        return None
    profil = ProfilFacturation.query.filter_by(entreprise_id=entreprise.id).first()
    if profil is None:
        profil = ProfilFacturation(
            entreprise_id=entreprise.id,
            raison_sociale=entreprise.nom,
            telephone=entreprise.contact or '',
            ville=entreprise.localisation or '',
            logo=entreprise.logo,
        )
        db.session.add(profil)
        db.session.commit()
    return profil


def familles_de(entreprise_id):
    """Familles de produits d'une entreprise, dans l'ordre d'affichage."""
    from models import FamilleProduit
    return (FamilleProduit.query
            .filter_by(entreprise_id=entreprise_id)
            .order_by(FamilleProduit.ordre, FamilleProduit.nom).all())


def taux_tva_de(entreprise):
    """Taux de TVA applicable. Zéro si l'entreprise n'est pas assujettie."""
    profil = ProfilFacturation.query.filter_by(
        entreprise_id=entreprise.id).first() if entreprise else None
    if profil and profil.regime_tva:
        return profil.taux_tva or 0.0
    return 0.0


# ==========================================================================
# DOCUMENTS COMMERCIAUX
# ==========================================================================

def creer_document(type_document, entreprise, client, lignes, utilisateur,
                   condition=ConditionVente.COMPTANT, montant_paye=None,
                   mode_paiement=None, reference_paiement=None,
                   date_document=None, date_echeance=None, note=None,
                   numero_commande=None, categorie_transaction=None,
                   heure_document=None):
    """Crée une facture, une proforma ou un reçu à partir de ses lignes.

    `lignes` est une liste de dictionnaires :
        {article_id, designation, quantite, unite, prix_unitaire,
         montant_paye, remise}

    Retourne le document créé, totaux calculés et statut positionné.
    """
    document = Document(
        type_document=type_document,
        numero=prochain_numero(type_document, entreprise.id),
        numero_commande=(numero_commande
                         or prochain_numero_commande(entreprise.id)),
        categorie_transaction=categorie_transaction or None,
        date_document=date_document or date.today(),
        heure_document=heure_document or datetime.now().strftime('%HH%M'),
        date_echeance=date_echeance,
        entreprise_id=entreprise.id,
        client_id=client.id if client else None,
        condition_vente=condition,
        mode_paiement=mode_paiement,
        reference_paiement=reference_paiement,
        note=note,
        cree_par_id=utilisateur.id if utilisateur else None,
    )

    for ligne in lignes:
        if not ligne.get('designation'):
            continue
        remise = bool(ligne.get('remise'))
        paye_ligne = ligne.get('montant_paye')
        document.lignes.append(LigneDocument(
            article_id=ligne.get('article_id'),
            designation=ligne['designation'],
            quantite=float(ligne.get('quantite') or 0),
            unite=ligne.get('unite') or 'pièce',
            prix_unitaire=float(ligne.get('prix_unitaire') or 0),
            remise=remise,
            montant_paye=(0.0 if remise else
                          (float(paye_ligne) if paye_ligne not in (None, '')
                           else None)),
        ))

    document.recalculer(taux_tva_de(entreprise))

    # Un reçu constate un encaissement. Si le détail ligne à ligne a été
    # renseigné — le cas du modèle du cabinet, où une prestation est offerte
    # — il fait foi ; sinon on retombe sur la règle du comptant.
    if type_document == TypeDocument.RECU:
        if montant_paye is None:
            detaille = any(l.remise or l.montant_paye is not None
                           for l in document.lignes)
            if detaille:
                montant_paye = sum(l.paye_ligne for l in document.lignes)
            else:
                montant_paye = (document.montant_ttc
                                if condition == ConditionVente.COMPTANT else 0.0)
        document.montant_paye = min(float(montant_paye), document.montant_ttc)
    elif type_document == TypeDocument.FACTURE:
        # Une facture peut être accompagnée d'un acompte.
        document.montant_paye = min(float(montant_paye or 0), document.montant_ttc)
    else:
        document.montant_paye = 0.0

    document.actualiser_statut()
    db.session.add(document)
    db.session.commit()
    return document


def enregistrer_encaissement(document, montant, mode_paiement=None,
                             reference=None, utilisateur=None):
    """Enregistre un règlement partiel ou total sur un document."""
    document.montant_paye = min((document.montant_paye or 0) + float(montant),
                                document.montant_ttc or 0)
    if mode_paiement:
        document.mode_paiement = mode_paiement
    if reference:
        document.reference_paiement = reference
    document.actualiser_statut()
    db.session.commit()
    synchroniser_comptabilite(document, utilisateur)
    return document


# ==========================================================================
# LIEN AUTOMATIQUE AVEC LA COMPTABILITÉ
# ==========================================================================

def _ventilation_par_famille(document):
    """Répartit le montant d'un document entre les familles de produits.

    Le compte d'exploitation ne présente pas un chiffre d'affaires global
    mais un chiffre d'affaires par famille : il faut donc savoir, pour
    chaque document, ce qui revient à chacune.
    """
    parts = {}
    for ligne in document.lignes:
        article = ligne.article or (db.session.get(Article, ligne.article_id)
                                    if ligne.article_id else None)
        code = article.code_rubrique if article else CA_NON_VENTILE
        parts[code] = parts.get(code, 0.0) + ligne.total_ligne

    total = sum(parts.values())
    if total <= 0:
        return {CA_NON_VENTILE: document.montant_ttc or 0.0}

    # Répartition du TTC au prorata du HT de chaque famille, pour que la
    # somme des écritures corresponde exactement au montant du document.
    ttc = document.montant_ttc or total
    ventile = {code: round(part / total * ttc, 2) for code, part in parts.items()}

    # L'arrondi peut laisser quelques francs : ils vont à la plus grosse part.
    ecart = round(ttc - sum(ventile.values()), 2)
    if ecart and ventile:
        principale = max(ventile, key=ventile.get)
        ventile[principale] = round(ventile[principale] + ecart, 2)
    return ventile


def _effacer_ecritures(document):
    """Supprime les écritures déjà engendrées par un document."""
    for operation in Operation.query.filter_by(document_id=document.id).all():
        db.session.delete(operation)
    # Écritures des versions antérieures, rattachées par la seule référence.
    for operation in Operation.query.filter_by(
            entreprise_id=document.entreprise_id,
            reference=document.numero).all():
        db.session.delete(operation)
    db.session.flush()


def synchroniser_comptabilite(document, utilisateur=None):
    """Reporte un document au journal de bord.

    C'est le cœur du dispositif « une seule saisie » : le gérant émet une
    facture ou un reçu, et ses comptes se mettent à jour sans qu'il ait à
    ressaisir quoi que ce soit.

    La règle appliquée est celle rappelée par le cabinet : chiffre
    d'affaires et trésorerie ne se confondent pas.

      · Vente réglée intégralement au comptant
        → une écriture par famille de produits, au mode de paiement réel.
          Elle alimente le chiffre d'affaires ET les ventes au comptant.

      · Vente à crédit ou partiellement réglée
        → une écriture par famille au mode « crédit » : elle alimente le
          chiffre d'affaires sans toucher la trésorerie ;
        → plus une écriture d'encaissement des créances clients, pour la
          somme réellement reçue.

    Une facture de 1 000 000 dont 500 000 sont versés donne ainsi
    1 000 000 de chiffre d'affaires et 500 000 de trésorerie.
    """
    if document.type_document not in (TypeDocument.FACTURE, TypeDocument.RECU):
        return None

    _effacer_ecritures(document)
    if document.statut == StatutDocument.ANNULE:
        db.session.commit()
        return None

    total = document.montant_ttc or 0.0
    paye = document.montant_paye or 0.0
    if total <= 0 and paye <= 0:
        db.session.commit()
        return None

    mode_reel = document.mode_paiement or ModePaiement.CASH
    if mode_reel == ModePaiement.CREDIT:
        mode_reel = ModePaiement.CASH

    solde_du = round(total - paye, 2)
    au_comptant = (document.condition_vente == ConditionVente.COMPTANT
                   and solde_du <= 0.01)

    libelle = f"Vente — {document.numero}"
    if document.client:
        libelle += f" — {document.client.nom}"

    creees = []

    # --- Chiffre d'affaires, ventilé par famille de produits ---
    for index, (code, montant) in enumerate(
            sorted(_ventilation_par_famille(document).items()), start=1):
        if not montant:
            continue
        creees.append(Operation(
            date_operation=document.date_document,
            type_operation=TypeOperation.VENTE,
            categorie=code,
            libelle=libelle,
            entreprise_id=document.entreprise_id,
            client_id=document.client_id,
            montant=montant,
            mode_paiement=mode_reel if au_comptant else ModePaiement.CREDIT,
            reference=document.numero,
            numero_facture=f"{document.numero}-CA{index}",
            document_id=document.id,
            cree_par_id=utilisateur.id if utilisateur else document.cree_par_id,
        ))

    # --- Encaissement, lorsqu'il ne se confond pas avec la vente ---
    if not au_comptant and paye > 0:
        creees.append(Operation(
            date_operation=document.date_document,
            type_operation=TypeOperation.VENTE,
            categorie=CODE_CREANCES_CLIENTS,
            libelle=f"Règlement — {document.numero}"
                    + (f" — {document.client.nom}" if document.client else ''),
            entreprise_id=document.entreprise_id,
            client_id=document.client_id,
            montant=paye,
            mode_paiement=mode_reel,
            reference=document.numero,
            numero_facture=f"{document.numero}-ENC",
            document_id=document.id,
            cree_par_id=utilisateur.id if utilisateur else document.cree_par_id,
        ))

    for operation in creees:
        db.session.add(operation)
    db.session.commit()
    return creees[0] if creees else None


def annuler_document(document, utilisateur=None):
    """Annule un document, ses écritures comptables et ses sorties de stock."""
    document.statut = StatutDocument.ANNULE
    _effacer_ecritures(document)

    if document.stock_deduit:
        for mouvement in MouvementStock.query.filter_by(
                document_id=document.id).all():
            article = mouvement.article
            if article and article.suit_stock:
                article.quantite_stock = (article.quantite_stock or 0) + mouvement.quantite
            db.session.delete(mouvement)
        document.stock_deduit = False

    db.session.commit()
    return document


# ==========================================================================
# STOCK
# ==========================================================================

def enregistrer_mouvement(article, type_mouvement, quantite, utilisateur=None,
                          motif=None, fournisseur_id=None, document_id=None,
                          date_mouvement=None):
    """Enregistre une entrée, une sortie ou un ajustement de stock.

    Met à jour la quantité de l'article et conserve la quantité résultante,
    ce qui permet de reconstituer l'historique même après modification.
    """
    if not article.suit_stock:
        return None

    quantite = abs(float(quantite))
    actuel = article.quantite_stock or 0

    if type_mouvement == TypeMouvement.ENTREE:
        article.quantite_stock = actuel + quantite
    elif type_mouvement == TypeMouvement.SORTIE:
        article.quantite_stock = actuel - quantite
    else:                                   # ajustement : valeur absolue
        article.quantite_stock = quantite

    mouvement = MouvementStock(
        article_id=article.id,
        entreprise_id=article.entreprise_id,
        type_mouvement=type_mouvement,
        quantite=quantite,
        quantite_apres=article.quantite_stock,
        motif=motif,
        fournisseur_id=fournisseur_id,
        document_id=document_id,
        date_mouvement=date_mouvement or date.today(),
        cree_par_id=utilisateur.id if utilisateur else None,
    )
    db.session.add(mouvement)
    db.session.commit()
    return mouvement


def verifier_disponibilite(entreprise_id, lignes):
    """Contrôle si les quantités demandées sont disponibles en stock.

    Retourne la liste des insuffisances constatées. On avertit sans bloquer :
    sur le terrain, le stock réel diffère parfois de l'enregistrement, et
    empêcher une vente déjà conclue serait contre-productif. Le gérant est
    informé et corrige ensuite par un ajustement.
    """
    manquants = []
    for ligne in lignes:
        article_id = ligne.get('article_id')
        if not article_id:
            continue
        article = db.session.get(Article, article_id)
        if not article or not article.suit_stock:
            continue
        demandee = float(ligne.get('quantite') or 0)
        disponible = article.quantite_stock or 0
        if demandee > disponible:
            manquants.append({
                'article': article,
                'demandee': demandee,
                'disponible': disponible,
                'manquant': demandee - disponible,
            })
    return manquants


def deduire_stock_document(document, utilisateur=None):
    """Décrémente le stock des produits d'un reçu, une seule fois.

    Retourne (nombre_articles_deduits, liste_des_insuffisances).
    """
    if document.stock_deduit or document.type_document != TypeDocument.RECU:
        return 0, []

    nombre = 0
    insuffisances = []
    for ligne in document.lignes:
        if not ligne.article_id:
            continue
        article = db.session.get(Article, ligne.article_id)
        if article and article.suit_stock:
            if ligne.quantite > (article.quantite_stock or 0):
                insuffisances.append({
                    'article': article,
                    'demandee': ligne.quantite,
                    'disponible': article.quantite_stock or 0,
                })
            enregistrer_mouvement(
                article, TypeMouvement.SORTIE, ligne.quantite,
                utilisateur=utilisateur,
                motif=f"Vente {document.numero}",
                document_id=document.id,
                date_mouvement=document.date_document)
            nombre += 1

    document.stock_deduit = True
    db.session.commit()
    return nombre, insuffisances


def articles_en_alerte(entreprise_id):
    """Produits dont le stock a atteint ou dépassé le seuil d'alerte."""
    articles = Article.query.filter_by(
        entreprise_id=entreprise_id,
        nature=NatureArticle.PRODUIT, actif=True).all()
    return [a for a in articles if a.en_alerte]


def valeur_totale_stock(entreprise_id):
    articles = Article.query.filter_by(
        entreprise_id=entreprise_id, nature=NatureArticle.PRODUIT).all()
    return sum(a.valeur_stock for a in articles)


# ==========================================================================
# FICHE CLIENT ET FICHE FOURNISSEUR
# ==========================================================================

def fiche_client(client):
    """Rassemble tout ce qui concerne un client : documents, opérations,
    montants payés et restant dus."""
    documents = (Document.query.filter_by(client_id=client.id)
                 .order_by(Document.date_document.desc(),
                           Document.id.desc()).all())
    operations = (Operation.query.filter_by(client_id=client.id)
                  .order_by(Operation.date_operation.desc()).all())

    actifs = [d for d in documents if d.statut != StatutDocument.ANNULE]
    facture = sum(d.montant_ttc or 0 for d in actifs)
    paye = sum(d.montant_paye or 0 for d in actifs)

    proformas = [d for d in actifs if d.type_document == TypeDocument.PROFORMA]
    recus = [d for d in actifs if d.type_document == TypeDocument.RECU]

    ca_journal = sum(o.montant for o in operations
                     if o.type_operation == TypeOperation.VENTE)

    return {
        'client': client,
        'documents': documents,
        'proformas': proformas,
        'recus': recus,
        'operations': operations,
        'total_facture': facture,
        'total_paye': paye,
        'reste_du': max(0.0, facture - paye),
        'ca_journal': ca_journal,
        'nombre_documents': len(actifs),
        'derniere_transaction': documents[0].date_document if documents else None,
    }


def fiche_fournisseur(fournisseur):
    """Équivalent côté fournisseur : achats et entrées de stock."""
    mouvements = (MouvementStock.query
                  .filter_by(fournisseur_id=fournisseur.id)
                  .order_by(MouvementStock.date_mouvement.desc()).all())

    achats = (Operation.query
              .filter(Operation.entreprise_id == fournisseur.entreprise_id,
                      Operation.type_operation == TypeOperation.ACHAT,
                      Operation.libelle.ilike(f'%{fournisseur.nom}%'))
              .order_by(Operation.date_operation.desc()).all())

    return {
        'fournisseur': fournisseur,
        'mouvements': mouvements,
        'achats': achats,
        'total_achats': sum(o.montant for o in achats),
        'nombre_livraisons': len(mouvements),
        'derniere_livraison': mouvements[0].date_mouvement if mouvements else None,
    }


# ==========================================================================
# TABLEAU DE BORD COMMERCIAL
# ==========================================================================

def synthese_ventes(entreprise_id, annee=None):
    """Indicateurs de l'onglet Gestion des ventes."""
    annee = annee or date.today().year
    documents = (Document.query
                 .filter(Document.entreprise_id == entreprise_id,
                         Document.statut != StatutDocument.ANNULE)
                 .all())
    documents = [d for d in documents if d.date_document.year == annee]

    proformas = [d for d in documents if d.type_document == TypeDocument.PROFORMA]
    recus = [d for d in documents if d.type_document == TypeDocument.RECU]

    encaisse = sum(d.montant_paye or 0 for d in recus)
    facture = sum(d.montant_ttc or 0 for d in recus)

    return {
        'nombre_proformas': len(proformas),
        'proformas_en_attente': len([d for d in proformas
                                     if d.statut == StatutDocument.ATTENTE]),
        'montant_proformas': sum(d.montant_ttc or 0 for d in proformas),
        'nombre_recus': len(recus),
        'montant_facture': facture,
        'montant_encaisse': encaisse,
        'reste_a_encaisser': max(0.0, facture - encaisse),
        'annee': annee,
    }


# ==========================================================================
# RAPPORTS DU CABINET
# ==========================================================================

MODELE_SYNTHESE = (
    "Sur la période analysée, {entreprise} enregistre un chiffre d'affaires de "
    "{ca} FCFA pour des charges de {charges} FCFA, soit un résultat de "
    "{resultat} FCFA.\n\n"
    "La trésorerie disponible s'établit à {tresorerie} FCFA."
)

MODELE_CONTEXTE = (
    "Le présent rapport fait suite à la volonté de la Direction de "
    "{entreprise} de structurer l'entreprise et de mettre en place des "
    "systèmes de gestion efficaces : procédures de contrôle des entrées et "
    "sorties de fonds, suivi du stock, définition des objectifs et "
    "budgétisation.\n\n"
    "(Précisez ici l'activité de l'entreprise et le cadre de la mission.)"
)

MODELE_METHODOLOGIE = (
    "La démarche a consisté à examiner les cycles de gestion de "
    "{entreprise} au cours de séances de travail avec la Direction, puis à "
    "restituer pour chaque cycle les constats (forces à préserver, "
    "faiblesses à combler) et les recommandations.\n\n"
    "Cycles passés en revue : Administratif, Financier, Comptable, Marketing."
)

MODELE_CYCLE = (
    "Forces (acquis à préserver) :\n"
    "- \n\n"
    "Faiblesses (manques à combler) :\n"
    "- "
)

MODELE_ANALYSE = (
    "(Analyse du consultant : commentez ici l'évolution de l'activité, "
    "la structure des charges et la situation de trésorerie, en appui sur "
    "l'annexe graphique.)"
)

MODELE_RECOMMANDATIONS = (
    "1. (Première recommandation)\n"
    "2. (Deuxième recommandation)\n"
    "3. (Troisième recommandation)"
)

MODELE_CONCLUSION = (
    "L'état des lieux réalisé fait ressortir des acquis à préserver et des "
    "insuffisances à corriger. Les recommandations formulées, si elles sont "
    "validées, permettront d'améliorer la gestion de {entreprise} et "
    "d'entretenir sa croissance."
)


def _format_montant(valeur):
    return f"{valeur:,.0f}".replace(',', ' ')


def creer_rapport(entreprise, annee, utilisateur, titre=None, periode=None):
    """Crée un rapport pré-rempli avec les chiffres de la période.

    Le consultant n'a plus qu'à compléter l'analyse et les recommandations :
    la synthèse et les constats sont déjà rédigés à partir des données.
    """
    scope = {'entreprise_id': entreprise.id,
             'date_debut': date(annee, 1, 1), 'date_fin': date(annee, 12, 31)}

    exploitation = comptes.compte_exploitation(**scope)
    tresorerie = comptes.compte_tresorerie(**scope)
    resume = comptes.resume_mensuel(annee, entreprise_id=entreprise.id)
    constats = comptes.analyse_automatique(exploitation, tresorerie, resume)

    synthese = MODELE_SYNTHESE.format(
        entreprise=entreprise.nom,
        ca=_format_montant(exploitation['chiffre_affaires']),
        charges=_format_montant(exploitation['charges']),
        resultat=_format_montant(exploitation['resultat_net']),
        tresorerie=_format_montant(tresorerie['total_disponible']),
    )

    rapport = Rapport(
        entreprise_id=entreprise.id,
        titre=titre or f"Rapport d'accompagnement — {entreprise.nom} — {annee}",
        periode=periode or f"Exercice {annee}",
        annee=annee,
        synthese=synthese,
        contexte=MODELE_CONTEXTE.format(entreprise=entreprise.nom),
        methodologie=MODELE_METHODOLOGIE.format(entreprise=entreprise.nom),
        cycle_administratif=MODELE_CYCLE,
        cycle_financier=MODELE_CYCLE,
        cycle_comptable=MODELE_CYCLE,
        cycle_marketing=MODELE_CYCLE,
        constats='\n'.join(f"• {c}" for c in constats),
        analyse=MODELE_ANALYSE,
        recommandations=MODELE_RECOMMANDATIONS,
        conclusion=MODELE_CONCLUSION.format(entreprise=entreprise.nom),
        cree_par_id=utilisateur.id if utilisateur else None,
    )
    db.session.add(rapport)
    db.session.flush()                 # obtenir l'id avant de rattacher les sections

    plan = [
        ('Aperçu chiffré', synthese),
        ('1. Contexte', rapport.contexte),
        ('2. Méthodologie', rapport.methodologie),
        ('3.1 Cycle administratif', rapport.cycle_administratif),
        ('3.2 Cycle financier', rapport.cycle_financier),
        ('3.3 Cycle comptable', rapport.cycle_comptable),
        ('3.4 Cycle marketing', rapport.cycle_marketing),
        ('4. Analyse', rapport.analyse),
        ('5. Recommandations', rapport.recommandations),
        ('6. Conclusion', rapport.conclusion),
    ]
    for index, (titre_section, corps) in enumerate(plan):
        db.session.add(SectionRapport(
            rapport_id=rapport.id, ordre=index,
            titre=titre_section, corps=corps or ''))

    db.session.commit()
    return rapport


def garantir_sections(rapport):
    """Reconstruit les sections d'un rapport ancien à partir de ses anciens
    champs texte, s'il n'en possède pas encore. Idempotent."""
    if rapport.sections:
        return rapport.sections

    plan = [
        ('Aperçu chiffré', rapport.synthese),
        ('1. Contexte', rapport.contexte),
        ('2. Méthodologie', rapport.methodologie),
        ('3.1 Cycle administratif', rapport.cycle_administratif),
        ('3.2 Cycle financier', rapport.cycle_financier),
        ('3.3 Cycle comptable', rapport.cycle_comptable),
        ('3.4 Cycle marketing', rapport.cycle_marketing),
        ('4. Analyse', rapport.analyse),
        ('5. Recommandations', rapport.recommandations),
        ('6. Conclusion', rapport.conclusion),
        # rapports d'avant la structure par cycles
        ('Constats', rapport.constats),
    ]
    ordre = 0
    for titre_section, corps in plan:
        if corps and corps.strip():
            db.session.add(SectionRapport(
                rapport_id=rapport.id, ordre=ordre,
                titre=titre_section, corps=corps))
            ordre += 1
    db.session.commit()
    return rapport.sections


def publier_rapport(rapport):
    """Rend le rapport visible par le client à sa connexion."""
    rapport.publie = True
    rapport.date_publication = datetime.utcnow()
    db.session.commit()
    return rapport


def rapports_visibles(utilisateur, entreprise_id=None):
    """Rapports accessibles à l'utilisateur.

    Un client ne voit que les rapports publiés le concernant ; le cabinet
    voit aussi les brouillons.
    """
    q = Rapport.query
    if entreprise_id:
        q = q.filter_by(entreprise_id=entreprise_id)
    if utilisateur.est_client:
        q = q.filter_by(entreprise_id=utilisateur.entreprise_id, publie=True)
    return q.order_by(Rapport.date_creation.desc()).all()
