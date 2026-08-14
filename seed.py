"""
Jeu de données de démonstration Posiges V1.

Usage :  python seed.py

Crée les comptes de test des deux parties prenantes — le cabinet AK World
et deux entreprises accompagnées —, les familles de produits, un catalogue,
un historique d'opérations réaliste sur l'année en cours et des budgets
d'exploitation et de trésorerie déjà renseignés, pour que les colonnes
« Prévu » et « Écart » soient parlantes dès la première connexion.

⚠️  À n'utiliser qu'en développement — jamais sur la base de production.
"""
import random
from datetime import date, timedelta

import budgets
import gestion_commerciale as gc
from app import app, initialiser_base
from models import (Article, Budget, Client, ConditionVente, Departement,
                    Document, Entreprise, FamilleProduit, Fournisseur,
                    LigneBudget, ModePaiement, MouvementStock, NatureArticle,
                    Operation, PREFIXE_CHARGE, PREFIXE_ENCAISSEMENT, Role,
                    RUBRIQUE_VENTES_COMPTANT, TypeBudget, TypeDocument,
                    TypeMouvement, TypeOperation, User, db)

# Rubriques de charges utilisées par les opérations de démonstration,
# avec une fourchette de montants plausible pour une TPE ivoirienne.
CHARGES_DEMO = [
    ('achat_marchandises', 'Réapprovisionnement du stock', 15000, 80000),
    ('intrants', 'Fruits, sucre et additifs', 10000, 50000),
    ('emballages', 'Bouteilles, étiquettes et cartons', 8000, 35000),
    ('transport_livraison', 'Livraison aux revendeurs', 3000, 20000),
    ('loyer', 'Loyer mensuel du local', 60000, 60000),
    ('salaires', 'Salaires du personnel', 80000, 150000),
    ('energie_eau', 'Facture CIE / SODECI', 12000, 35000),
    ('charges_sociales', 'Cotisations CNPS', 15000, 40000),
    ('publicite', 'Communication et réseaux sociaux', 5000, 25000),
]

NOMS_CLIENTS = [
    "Restaurant Le Bon Goût", "Maquis Chez Tantie", "Boutique Awa",
    "Supérette Konan", "Hôtel Ivoire Plus", "Cafétéria du Plateau",
    "Kiosque Adjamé", "Épicerie Yopougon",
]

FAMILLES_DEMO = [
    ('Jus de fruits', 'Jus pressés, toutes contenances', 1),
    ('Boissons infusées', 'Bissap, gingembre et infusions', 2),
    ('Conditionnement', 'Ventes par carton et par lot', 3),
    ('Prestations', 'Services facturés à la journée', 4),
]

ARTICLES_DEMO = [
    # (désignation, nature, famille, prix vente, prix achat, unité, stock, seuil)
    ("Jus d'orange 1L", 'produit', 'Jus de fruits', 1500, 900, 'pièce', 120, 25),
    ("Jus d'ananas 1L", 'produit', 'Jus de fruits', 1500, 900, 'pièce', 80, 25),
    ("Jus de bissap 1L", 'produit', 'Boissons infusées', 1200, 700, 'pièce', 18, 20),
    ("Jus de gingembre 50cl", 'produit', 'Boissons infusées', 1000, 600, 'pièce', 45, 15),
    ("Carton de 12 bouteilles", 'produit', 'Conditionnement', 16000, 10000, 'carton', 22, 10),
    ("Sucre raffiné", 'produit', 'Conditionnement', 900, 650, 'kg', 60, 20),
    ("Livraison à domicile", 'service', 'Prestations', 2000, 0, 'forfait', 0, 0),
    ("Prestation événementielle", 'service', 'Prestations', 75000, 0, 'jour', 0, 0),
]

FOURNISSEURS_DEMO = [
    ("Grossiste Adjamé", 'Fruits et matières premières', '07 11 22 33 44', 'Adjamé, Abidjan'),
    ("Emballages Plus CI", 'Bouteilles et étiquettes', '05 55 66 77 88', 'Yopougon, Abidjan'),
    ("Transport Rapide SARL", 'Livraison et logistique', '01 33 44 55 66', 'Treichville, Abidjan'),
]


# --------------------------------------------------------------------------
# Opérations du journal
# --------------------------------------------------------------------------

def creer_operations(entreprise_id, departement, nombre, debut, fin,
                     createur_id, familles=None, avec_financement=False):
    """Génère des entrées et des sorties réparties sur la période.

    Les entrées sont ventilées sur les familles de produits de l'entreprise
    et les sorties sur les rubriques de charges du plan : c'est ce qui rend
    le compte d'exploitation lisible dès la première ouverture.
    """
    delta = (fin - debut).days or 1
    clients = Client.query.filter(
        Client.entreprise_id == entreprise_id if entreprise_id
        else Client.entreprise_id.is_(None)).all()

    codes_ca = ([f.code_rubrique for f in familles] if familles
                else ['ca:0'])
    prefixe = (db.session.get(Entreprise, entreprise_id).nom if entreprise_id
               else departement)[:3].upper()
    depart = Operation.query.count()

    for index in range(nombre):
        jour = debut + timedelta(days=random.randint(0, delta))
        est_entree = random.random() < 0.62

        if est_entree:
            categorie = random.choice(codes_ca)
            libelle = 'Vente du jour'
            montant = float(random.randint(5000, 60000))
        else:
            code, libelle, mini, maxi = random.choice(CHARGES_DEMO)
            categorie = PREFIXE_CHARGE + code
            montant = float(random.randint(mini, maxi))

        db.session.add(Operation(
            date_operation=jour,
            type_operation=TypeOperation.VENTE if est_entree else TypeOperation.ACHAT,
            categorie=categorie,
            libelle=libelle,
            departement=departement,
            entreprise_id=entreprise_id,
            client=random.choice(clients) if (est_entree and clients) else None,
            montant=montant,
            mode_paiement=random.choices(
                ModePaiement.CHOIX, weights=[45, 30, 15, 10])[0],
            reference=f"REC-{random.randint(1000, 9999)}",
            numero_facture=f"{prefixe}-{date.today().year}-{depart + index + 1:05d}",
            cree_par_id=createur_id,
        ))

    # Quelques encaissements de trésorerie hors chiffre d'affaires, pour que
    # le compte de trésorerie ne se résume pas aux ventes.
    if entreprise_id and avec_financement:
        for code, montant in (('emprunts', 1500000), ('subventions', 350000)):
            db.session.add(Operation(
                date_operation=debut + timedelta(days=random.randint(0, 60)),
                type_operation=TypeOperation.VENTE,
                categorie=PREFIXE_ENCAISSEMENT + code,
                libelle='Financement',
                entreprise_id=entreprise_id,
                montant=float(montant),
                mode_paiement=ModePaiement.BANQUE,
                numero_facture=f"FIN-{entreprise_id}-{code}",
                cree_par_id=createur_id,
            ))


# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------

def creer_budgets(entreprise, annee, utilisateur, familles):
    """Renseigne un budget d'exploitation et un budget de trésorerie.

    Les montants sont dérivés de ce qui a réellement été enregistré, avec
    un écart de l'ordre de 10 à 20 %. C'est ce qui rend la démonstration
    parlante : des écarts plausibles, tantôt favorables tantôt défavorables,
    comme ceux qu'un gérant observe en cours d'année. Les mois à venir sont
    budgétés sur la moyenne des mois déjà écoulés.
    """
    import comptes

    mois_ecoules = min(12, date.today().month) if annee == date.today().year else 12

    def budgeter(budget, realise_par_rubrique):
        for rubrique, serie in realise_par_rubrique.items():
            moyenne = (sum(serie[:mois_ecoules]) / mois_ecoules) if mois_ecoules else 0
            if not moyenne:
                continue
            for mois in range(1, 13):
                reference = serie[mois - 1] if mois <= mois_ecoules else moyenne
                if not reference:
                    reference = moyenne
                montant = round(reference * random.uniform(0.82, 1.18), -3)
                if montant <= 0:
                    continue
                db.session.add(LigneBudget(budget_id=budget.id, rubrique=rubrique,
                                           mois=mois, montant=float(montant)))

    # --- Budget d'exploitation, calé sur le compte d'exploitation réalisé ---
    exploitation = budgets.obtenir_ou_creer(
        entreprise.id, annee, TypeBudget.EXPLOITATION, utilisateur)
    compte = comptes.compte_exploitation_detaille(entreprise.id, annee)
    realise = {}
    for section in compte['sections']:
        for ligne in section['lignes']:
            realise[ligne['code']] = ligne['realise']
    budgeter(exploitation, realise)

    # --- Budget de trésorerie, calé sur le compte de trésorerie réalisé ---
    tresorerie = budgets.obtenir_ou_creer(
        entreprise.id, annee, TypeBudget.TRESORERIE, utilisateur)
    tresorerie.solde_initial = float(round(random.randint(400000, 900000), -4))

    compte = comptes.compte_tresorerie_detaille(entreprise.id, annee)
    realise = {}
    for section in compte['sections']:
        for ligne in section['lignes']:
            realise[ligne['code']] = ligne['realise']
    budgeter(tresorerie, realise)

    db.session.commit()


# --------------------------------------------------------------------------
# Catalogue, fournisseurs et documents commerciaux
# --------------------------------------------------------------------------

def peupler_commercial(entreprise, utilisateur, familles):
    """Profil de facturation, catalogue, fournisseurs, ventes et stock."""
    profil = gc.profil_de(entreprise)
    profil.raison_sociale = 'Jus de Bella'
    profil.adresse = 'Rue des Jardins, Cocody'
    profil.ville = "Abidjan, Côte d'Ivoire"
    profil.telephone = '07 00 00 00 01'
    profil.email = 'contact@jusdebella.ci'
    profil.rccm = 'CI-ABJ-2024-B-12345'
    profil.compte_contribuable = '1955625 S'
    profil.banque = 'Ecobank Côte d\'Ivoire'
    profil.numero_compte = 'CI93 0001 0002 0003 0004 0005'
    profil.beneficiaire = 'JUS DE BELLA SARL'
    profil.mobile_money = 'Orange Money 07 00 00 00 01'
    profil.signataire = 'Kouadio BELLA'
    profil.mention_pied = 'Merci de votre confiance. Paiement à réception.'

    par_nom = {f.nom: f for f in familles}

    fournisseurs = []
    for nom, specialite, contact, localisation in FOURNISSEURS_DEMO:
        fournisseur = Fournisseur(nom=nom, specialite=specialite, contact=contact,
                                  localisation=localisation,
                                  entreprise_id=entreprise.id)
        db.session.add(fournisseur)
        fournisseurs.append(fournisseur)

    articles = []
    for index, (designation, nature, famille, pv, pa, unite, stock, seuil) in \
            enumerate(ARTICLES_DEMO, start=1):
        article = Article(
            reference=f'ART-{index:03d}', designation=designation, nature=nature,
            famille_id=par_nom[famille].id if famille in par_nom else None,
            prix_vente=pv, prix_achat=pa, unite=unite,
            quantite_stock=stock if nature == NatureArticle.PRODUIT else 0,
            seuil_alerte=seuil if nature == NatureArticle.PRODUIT else 0,
            entreprise_id=entreprise.id)
        db.session.add(article)
        articles.append(article)
    db.session.flush()

    for article in articles:
        if article.suit_stock and article.quantite_stock:
            db.session.add(MouvementStock(
                article_id=article.id, entreprise_id=entreprise.id,
                type_mouvement=TypeMouvement.ENTREE,
                quantite=article.quantite_stock,
                quantite_apres=article.quantite_stock,
                motif='Stock initial',
                fournisseur_id=fournisseurs[0].id,
                date_mouvement=date(date.today().year, 1, 15),
                cree_par_id=utilisateur.id))
    db.session.flush()

    clients = Client.query.filter_by(entreprise_id=entreprise.id).all()
    produits = [a for a in articles if a.suit_stock]
    services = [a for a in articles if not a.suit_stock]

    # Un panachage de factures, de proformas et de reçus, dont une facture
    # partiellement réglée : le cas d'école cité par le cabinet.
    types = [TypeDocument.FACTURE, TypeDocument.RECU, TypeDocument.RECU,
             TypeDocument.PROFORMA]
    categories = ['VENTE DE PRODUITS', 'PRESTATION DE SERVICE',
                  'COMMANDE EN GROS']

    for index in range(14):
        jour = date.today() - timedelta(days=random.randint(1, 150))
        client = random.choice(clients) if clients else None
        type_document = types[index % len(types)]

        lignes = []
        for article in random.sample(produits, random.randint(1, 3)):
            lignes.append({'article_id': article.id,
                           'designation': article.designation,
                           'quantite': random.randint(2, 15),
                           'unite': article.unite,
                           'prix_unitaire': article.prix_vente})
        if random.random() < 0.3 and services:
            service = random.choice(services)
            lignes.append({'article_id': service.id,
                           'designation': service.designation,
                           'quantite': 1, 'unite': service.unite,
                           'prix_unitaire': service.prix_vente,
                           'remise': random.random() < 0.3})

        condition = (ConditionVente.CREDIT if random.random() < 0.3
                     else ConditionVente.COMPTANT)
        document = gc.creer_document(
            type_document, entreprise, client, lignes, utilisateur,
            condition=condition,
            montant_paye=None,
            mode_paiement=random.choice([ModePaiement.CASH,
                                         ModePaiement.MOBILE_MONEY,
                                         ModePaiement.BANQUE]),
            categorie_transaction=random.choice(categories),
            date_document=jour)

        if type_document in (TypeDocument.FACTURE, TypeDocument.RECU):
            if condition == ConditionVente.CREDIT:
                document.montant_paye = round(document.montant_ttc * 0.5)
                document.actualiser_statut()
                db.session.commit()
            gc.synchroniser_comptabilite(document, utilisateur)
            gc.deduire_stock_document(document, utilisateur)

    db.session.commit()


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------

def peupler():
    with app.app_context():
        initialiser_base()
        if Entreprise.query.count() > 0:
            print("La base contient déjà des données de démonstration. "
                  "Rien à faire.")
            return

        # ---- Côté cabinet AK World ----
        consultant = User(nom='Awa Traoré', email='consultant@akworld.com',
                          role=Role.CONSULTANT)
        consultant.set_password('Consultant2026!')

        responsable = User(nom='Yao Kouassi', email='commercial@akworld.com',
                           role=Role.RESPONSABLE,
                           departement=Departement.COMMERCIAL)
        responsable.set_password('Commercial2026!')

        interne = User(nom='Fatou Diallo', email='comptabilite@akworld.com',
                       role=Role.RESPONSABLE,
                       departement=Departement.COMPTABILITE)
        interne.set_password('Comptabilite2026!')

        db.session.add_all([consultant, responsable, interne])
        db.session.flush()

        # ---- Entreprises accompagnées ----
        entreprise1 = Entreprise(nom='Jus de Bella', secteur='Agroalimentaire',
                                 contact='07 00 00 00 01',
                                 localisation='Cocody, Abidjan')
        entreprise2 = Entreprise(nom='Quincaillerie Konan',
                                 secteur='Commerce général',
                                 contact='05 00 00 00 02',
                                 localisation='Yopougon, Abidjan')
        entreprise1.consultants.append(consultant)
        entreprise2.consultants.append(consultant)
        db.session.add_all([entreprise1, entreprise2])
        db.session.flush()

        # ---- Côté entreprises : les trois niveaux d'accès ----
        gerant1 = User(nom='Kouadio Bella', email='bella@jusdebella.ci',
                       role=Role.GERANT, entreprise_id=entreprise1.id)
        gerant1.set_password('Bella2026!')
        comptable1 = User(nom='Aya Koffi', email='comptable@jusdebella.ci',
                          role=Role.COMPTABLE, entreprise_id=entreprise1.id)
        comptable1.set_password('Comptable2026!')
        caissier1 = User(nom='Sery Gogo', email='caisse@jusdebella.ci',
                         role=Role.STANDARD, entreprise_id=entreprise1.id)
        caissier1.set_password('Caisse2026!')

        gerant2 = User(nom='Konan Michel', email='michel@quincaillerie.ci',
                       role=Role.GERANT, entreprise_id=entreprise2.id)
        gerant2.set_password('Michel2026!')

        db.session.add_all([gerant1, comptable1, caissier1, gerant2])
        db.session.flush()

        # ---- Familles de produits ----
        familles1 = []
        for nom, description, ordre in FAMILLES_DEMO:
            famille = FamilleProduit(nom=nom, description=description,
                                     ordre=ordre, entreprise_id=entreprise1.id)
            db.session.add(famille)
            familles1.append(famille)

        familles2 = []
        for ordre, nom in enumerate(['Quincaillerie', 'Peinture',
                                     'Plomberie'], start=1):
            famille = FamilleProduit(nom=nom, ordre=ordre,
                                     entreprise_id=entreprise2.id)
            db.session.add(famille)
            familles2.append(famille)
        db.session.flush()

        # ---- Carnets d'adresses ----
        for nom in NOMS_CLIENTS[:5]:
            db.session.add(Client(
                nom=nom, entreprise_id=entreprise1.id,
                contact=f"07 {random.randint(10, 99)} 00 00 00",
                email=f"{nom.split()[-1].lower()}@exemple.ci",
                localisation='Abidjan'))
        for nom in NOMS_CLIENTS[5:]:
            db.session.add(Client(nom=nom, entreprise_id=entreprise2.id,
                                  localisation='Abidjan'))
        for nom in ['Groupe SIFCA', 'PME Partenaire SARL', 'Coopérative Agricole']:
            db.session.add(Client(nom=nom, entreprise_id=None,
                                  localisation='Abidjan'))
        db.session.flush()

        # ---- Opérations du journal ----
        annee = date.today().year
        debut = date(annee, 1, 1)
        fin = min(date.today(), date(annee, 12, 31))

        # Une partie des saisies revient au caissier : son tableau de bord
        # simplifié n'est ainsi pas vide à la première connexion.
        creer_operations(entreprise1.id, None, 60, debut, fin, gerant1.id,
                         familles1, avec_financement=True)
        creer_operations(entreprise1.id, None, 30, debut, fin, caissier1.id, familles1)
        creer_operations(entreprise2.id, None, 60, debut, fin, gerant2.id,
                         familles2, avec_financement=True)
        creer_operations(None, Departement.COMMERCIAL, 40, debut, fin, responsable.id)
        creer_operations(None, Departement.COMPTABILITE, 25, debut, fin, interne.id)
        db.session.commit()

        # ---- Budgets, catalogue et documents ----
        creer_budgets(entreprise1, annee, gerant1, familles1)
        creer_budgets(entreprise2, annee, gerant2, familles2)
        peupler_commercial(entreprise1, gerant1, familles1)
        db.session.commit()

        print("=" * 70)
        print("  Données de démonstration Posiges V1 créées")
        print("=" * 70)
        print("  CABINET AK WORLD")
        print("    Direction    : cabinet@akworld.com        / AkWorld2026!")
        print("    Consultant   : consultant@akworld.com     / Consultant2026!")
        print("    Responsable  : commercial@akworld.com     / Commercial2026!")
        print("    Responsable  : comptabilite@akworld.com   / Comptabilite2026!")
        print("-" * 70)
        print("  ENTREPRISE « JUS DE BELLA »")
        print("    Gérant       : bella@jusdebella.ci        / Bella2026!")
        print("    Comptable    : comptable@jusdebella.ci    / Comptable2026!")
        print("    Caissier     : caisse@jusdebella.ci       / Caisse2026!")
        print("-" * 70)
        print("  ENTREPRISE « QUINCAILLERIE KONAN »")
        print("    Gérant       : michel@quincaillerie.ci    / Michel2026!")
        print("=" * 70)
        print(f"  {Operation.query.count()} opérations sur {annee} · "
              f"{FamilleProduit.query.count()} familles · "
              f"{Article.query.count()} articles")
        print(f"  {Fournisseur.query.count()} fournisseurs · "
              f"{Document.query.count()} documents · "
              f"{Budget.query.count()} budgets renseignés")
        print("=" * 70)


if __name__ == '__main__':
    peupler()
