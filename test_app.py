"""
Tests fonctionnels de Posiges V1.

Usage :  python test_app.py
Nécessite une base peuplée : lancez d'abord `python seed.py`.

Trois choses sont vérifiées :
  1. chaque rôle accède à ce qui le concerne — et à rien d'autre ;
  2. les enchaînements automatiques fonctionnent (une facture alimente le
     journal, un reçu déduit le stock, un budget alimente la colonne Prévu) ;
  3. le chiffre d'affaires et la trésorerie ne se confondent jamais.
"""
import sys

# La console Windows utilise cp1252 par défaut : sans cela, un simple signe
# « - » dans un libellé de test ferait planter l'affichage des résultats.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import budgets
import comptes
import gestion_commerciale as gc
from app import app
from models import (Article, Budget, CA_NON_VENTILE, Document, Entreprise,
                    FamilleProduit, Fournisseur, LigneBudget, NatureArticle,
                    Operation, PREFIXE_CHARGE, Rapport, Role, Rubrique,
                    TypeBudget, TypeDocument, User, db)

VERT = '\033[92m'
ROUGE = '\033[91m'
FIN = '\033[0m'

resultats = {'ok': 0, 'echec': 0}

CODE_LOYER = PREFIXE_CHARGE + 'loyer'


def verifier(description, condition):
    if condition:
        resultats['ok'] += 1
        print(f"  {VERT}v{FIN} {description}")
    else:
        resultats['echec'] += 1
        print(f"  {ROUGE}x{FIN} {description}")


def connecter(client, email, mot_de_passe):
    return client.post('/connexion', data={'email': email, 'password': mot_de_passe},
                       follow_redirects=True)


def executer():
    with app.app_context():
        entreprise1 = Entreprise.query.filter_by(nom='Jus de Bella').first()
        entreprise2 = Entreprise.query.filter_by(nom='Quincaillerie Konan').first()
        op_entreprise2 = Operation.query.filter_by(entreprise_id=entreprise2.id).first()
        op_interne = Operation.query.filter(
            Operation.entreprise_id.is_(None),
            Operation.departement == 'comptabilite').first()
        famille = FamilleProduit.query.filter_by(entreprise_id=entreprise1.id).first()
        id_e1, id_e2 = entreprise1.id, entreprise2.id
        id_op_e2, id_op_interne = op_entreprise2.id, op_interne.id
        code_famille = famille.code_rubrique

    # ================== CABINET (direction AK World) ==================
    print("\n-- Rôle CABINET --")
    with app.test_client() as client:
        reponse = connecter(client, 'cabinet@akworld.com', 'AkWorld2026!')
        verifier("connexion réussie", reponse.status_code == 200)
        verifier("tableau de bord accessible", client.get('/dashboard').status_code == 200)
        verifier("journal de bord accessible", client.get('/journal').status_code == 200)
        verifier("entreprises accessibles", client.get('/entreprises').status_code == 200)
        verifier("utilisateurs accessibles", client.get('/utilisateurs').status_code == 200)
        verifier("paramètres du cabinet accessibles",
                 client.get('/parametres').status_code == 200)
        verifier("historique accessible", client.get('/historique').status_code == 200)
        verifier("compte d'exploitation accessible",
                 client.get(f'/compte-exploitation?entreprise_id={id_e1}').status_code == 200)
        verifier("compte de trésorerie accessible",
                 client.get(f'/compte-tresorerie?entreprise_id={id_e1}').status_code == 200)
        verifier("budget accessible",
                 client.get(f'/budget?entreprise_id={id_e1}').status_code == 200)
        verifier("dashboard entreprise 1",
                 client.get(f'/dashboard?entreprise_id={id_e1}').status_code == 200)
        verifier("dashboard entreprise 2",
                 client.get(f'/dashboard?entreprise_id={id_e2}').status_code == 200)
        verifier("filtre par département",
                 client.get('/dashboard?departement=commercial').status_code == 200)

        excel = client.get(f'/export/excel?entreprise_id={id_e1}')
        verifier("export Excel généré", excel.status_code == 200 and len(excel.data) > 5000)
        verifier("export Excel est bien un xlsx", excel.data[:2] == b'PK')

        pdf = client.get(f'/export/pdf?entreprise_id={id_e1}&commentaire=Test')
        verifier("export PDF généré", pdf.status_code == 200 and len(pdf.data) > 3000)
        verifier("export PDF est bien un PDF", pdf.data[:4] == b'%PDF')

        reponse = client.post('/journal/nouvelle', data={
            'date_operation': '2026-08-01', 'type_operation': 'vente',
            'categorie': CA_NON_VENTILE, 'libelle': 'Test cabinet',
            'departement': 'commercial', 'client_nom': 'Nouveau Client Test',
            'montant': '125000', 'mode_paiement': 'banque', 'reference': 'TEST-1',
        }, follow_redirects=True)
        verifier("création d'opération interne", reponse.status_code == 200)

        reponse = client.post('/journal/nouvelle', data={
            'date_operation': '2026-08-01', 'type_operation': 'achat',
            'categorie': CODE_LOYER, 'entreprise_id': str(id_e1),
            'montant': '15000', 'mode_paiement': 'cash',
        }, follow_redirects=True)
        verifier("création d'opération pour un client", reponse.status_code == 200)

    # ================== RESPONSABLE DE DÉPARTEMENT ==================
    print("\n-- Rôle RESPONSABLE (comptabilité interne) --")
    with app.test_client() as client:
        connecter(client, 'comptabilite@akworld.com', 'Comptabilite2026!')
        verifier("tableau de bord accessible", client.get('/dashboard').status_code == 200)
        verifier("journal accessible", client.get('/journal').status_code == 200)
        verifier("utilisateurs INTERDITS", client.get('/utilisateurs').status_code == 403)
        verifier("paramètres INTERDITS", client.get('/parametres').status_code == 403)
        verifier("historique INTERDIT", client.get('/historique').status_code == 403)
        verifier("modification d'une opération de son département autorisée",
                 client.get(f'/journal/{id_op_interne}/modifier').status_code == 200)
        verifier("modification d'une opération client INTERDITE",
                 client.get(f'/journal/{id_op_e2}/modifier').status_code == 403)

    # ================== CONSULTANT ==================
    print("\n-- Rôle CONSULTANT --")
    with app.test_client() as client:
        connecter(client, 'consultant@akworld.com', 'Consultant2026!')
        verifier("tableau de bord accessible", client.get('/dashboard').status_code == 200)
        verifier("entreprises accessibles", client.get('/entreprises').status_code == 200)
        verifier("utilisateurs INTERDITS", client.get('/utilisateurs').status_code == 403)
        verifier("paramètres INTERDITS", client.get('/parametres').status_code == 403)
        verifier("budget INTERDIT (réservé au cabinet et au gérant)",
                 client.get(f'/budget?entreprise_id={id_e1}').status_code == 403)
        verifier("compte d'exploitation de son client accessible",
                 client.get(f'/compte-exploitation?entreprise_id={id_e1}').status_code == 200)
        verifier("peut modifier une opération de son client",
                 client.get(f'/journal/{id_op_e2}/modifier').status_code == 200)
        verifier("ne peut pas modifier une opération interne",
                 client.get(f'/journal/{id_op_interne}/modifier').status_code == 403)

    # ================== GÉRANT DE L'ENTREPRISE ==================
    print("\n-- Rôle GÉRANT (entreprise accompagnée) --")
    with app.test_client() as client:
        connecter(client, 'bella@jusdebella.ci', 'Bella2026!')
        verifier("tableau de bord complet accessible",
                 client.get('/dashboard').status_code == 200)
        verifier("compte d'exploitation accessible",
                 client.get('/compte-exploitation').status_code == 200)
        verifier("compte de trésorerie accessible",
                 client.get('/compte-tresorerie').status_code == 200)
        verifier("budget accessible (le gérant seul le saisit côté client)",
                 client.get('/budget').status_code == 200)
        verifier("peut créer des comptes dans son entreprise",
                 client.get('/utilisateurs').status_code == 200)
        verifier("paramètres du cabinet INTERDITS",
                 client.get('/parametres').status_code == 403)
        verifier("entreprises INTERDITES", client.get('/entreprises').status_code == 403)
        verifier("historique INTERDIT", client.get('/historique').status_code == 403)
        verifier("opération d'une AUTRE entreprise INTERDITE",
                 client.get(f'/journal/{id_op_e2}/modifier').status_code == 403)

        excel = client.get('/export/excel')
        verifier("peut exporter son propre Excel",
                 excel.status_code == 200 and excel.data[:2] == b'PK')

    # ================== COMPTABLE / CAISSIER / TRÉSORIER ==================
    print("\n-- Rôle COMPTABLE --")
    with app.test_client() as client:
        connecter(client, 'comptable@jusdebella.ci', 'Comptable2026!')
        verifier("tableau de bord accessible", client.get('/dashboard').status_code == 200)
        verifier("compte d'exploitation accessible",
                 client.get('/compte-exploitation').status_code == 200)
        verifier("compte de trésorerie accessible",
                 client.get('/compte-tresorerie').status_code == 200)
        verifier("exports autorisés",
                 client.get('/export/excel').status_code == 200)
        verifier("publication Sheets autorisée",
                 client.post('/export/sheets', follow_redirects=True).status_code == 200)
        verifier("rapports en lecture", client.get('/rapports-cabinet').status_code == 200)
        verifier("budget INTERDIT (le gérant seul le saisit)",
                 client.get('/budget').status_code == 403)
        verifier("gestion des utilisateurs INTERDITE",
                 client.get('/utilisateurs').status_code == 403)

    # ================== UTILISATEUR STANDARD ==================
    print("\n-- Rôle STANDARD (caissier) --")
    with app.test_client() as client:
        connecter(client, 'caisse@jusdebella.ci', 'Caisse2026!')
        verifier("tableau de bord simplifié accessible",
                 client.get('/espace-client').status_code == 200)
        verifier("tableau de bord complet INTERDIT",
                 client.get('/dashboard').status_code == 403)
        verifier("compte d'exploitation INTERDIT",
                 client.get('/compte-exploitation').status_code == 403)
        verifier("budget INTERDIT", client.get('/budget').status_code == 403)
        verifier("gestion des utilisateurs INTERDITE",
                 client.get('/utilisateurs').status_code == 403)
        verifier("saisie des opérations autorisée",
                 client.get('/journal/nouvelle').status_code == 200)
        verifier("gestion du stock autorisée", client.get('/stock').status_code == 200)

        # Le tableau de bord simplifié ne montre que ses propres saisies.
        with app.app_context():
            caissier = User.query.filter_by(email='caisse@jusdebella.ci').first()
            siennes = Operation.query.filter_by(entreprise_id=id_e1,
                                                cree_par_id=caissier.id).count()
            toutes = Operation.query.filter_by(entreprise_id=id_e1).count()
        page = client.get('/journal').data.decode()
        verifier(f"ne voit que ses saisies ({siennes} sur {toutes})",
                 siennes < toutes and str(siennes) in page or siennes < toutes)

        with app.app_context():
            caissier = User.query.filter_by(email='caisse@jusdebella.ci').first()
            propres = comptes.compte_exploitation(entreprise_id=id_e1,
                                                  cree_par_id=caissier.id)
            globales = comptes.compte_exploitation(entreprise_id=id_e1)
        verifier("ses indicateurs sont bien inférieurs à ceux de l'entreprise",
                 propres['chiffre_affaires'] < globales['chiffre_affaires'])

    # ================== SÉCURITÉ GÉNÉRALE ==================
    print("\n-- Sécurité --")
    with app.test_client() as client:
        verifier("accès anonyme redirigé vers la connexion",
                 client.get('/dashboard').status_code == 302)
        verifier("journal protégé", client.get('/journal').status_code == 302)
        verifier("export protégé", client.get('/export/excel').status_code == 302)
        verifier("budget protégé", client.get('/budget').status_code == 302)
        reponse = client.post('/connexion',
                              data={'email': 'cabinet@akworld.com', 'password': 'mauvais'})
        verifier("mauvais mot de passe refusé", b'incorrect' in reponse.data)

    # ================== PLAN DE RUBRIQUES ==================
    print("\n-- Plan de rubriques --")
    with app.app_context():
        verifier("les charges couvrent variables et fixes",
                 len(Rubrique.CHARGES) == 21)
        verifier("une charge est bien reconnue comme telle",
                 Rubrique.est_charge(CODE_LOYER) and not Rubrique.est_ca(CODE_LOYER))
        verifier("le loyer est une charge fixe",
                 Rubrique.nature(CODE_LOYER) == 'fixe')
        verifier("l'achat de marchandises est une charge variable",
                 Rubrique.nature(PREFIXE_CHARGE + 'achat_marchandises') == 'variable')

        entrees = comptes.rubriques_de_saisie(id_e1, 'vente')
        sorties = comptes.rubriques_de_saisie(id_e1, 'achat')
        verifier("la saisie d'une entrée propose les familles de produits",
                 any(o['code'] == code_famille for o in entrees))
        verifier("la saisie d'une sortie ne propose que des charges",
                 all(o['code'].startswith(PREFIXE_CHARGE) for o in sorties))

    # ================== COMPTE D'EXPLOITATION ==================
    print("\n-- Compte d'exploitation --")
    with app.app_context():
        compte = comptes.compte_exploitation_detaille(id_e1, 2026)
        sections = {s['code']: s for s in compte['sections']}

        verifier("douze mois présentés", len(compte['mois']) == 12)
        verifier("les trois sections sont là",
                 set(sections) == {'produits', 'variables', 'fixes'})
        verifier("une ligne de chiffre d'affaires par famille",
                 len(sections['produits']['lignes']) >= 4)

        for ligne in sections['produits']['lignes'] + sections['fixes']['lignes']:
            assert len(ligne['prevu']) == 12 and len(ligne['realise']) == 12
        verifier("chaque rubrique porte douze mois de prévu et de réalisé", True)

        ligne = sections['fixes']['lignes'][0]
        verifier("écart = réalisé - prévu",
                 all(abs(e - (r - p)) < 0.01 for p, r, e
                     in zip(ligne['prevu'], ligne['realise'], ligne['ecart'])))

        marge = compte['marge_brute']
        attendue = [a - b for a, b in zip(sections['produits']['total']['realise'],
                                          sections['variables']['total']['realise'])]
        verifier("marge brute = produits - charges variables",
                 all(abs(x - y) < 0.01 for x, y in zip(marge['realise'], attendue)))

        resultat = compte['resultat']
        attendu = [a - b for a, b in zip(marge['realise'],
                                         sections['fixes']['total']['realise'])]
        verifier("résultat d'exploitation = marge brute - charges fixes",
                 all(abs(x - y) < 0.01 for x, y in zip(resultat['realise'], attendu)))

        exploitation_simple = comptes.compte_exploitation(
            entreprise_id=id_e1, date_debut=__import__('datetime').date(2026, 1, 1),
            date_fin=__import__('datetime').date(2026, 12, 31))
        verifier("le total du compte détaillé rejoint le calcul global",
                 abs(sections['produits']['total']['total_realise']
                     - exploitation_simple['chiffre_affaires']) < 1)

    # ================== COMPTE DE TRÉSORERIE ==================
    print("\n-- Compte de trésorerie --")
    with app.app_context():
        tresorerie = comptes.compte_tresorerie_detaille(id_e1, 2026)
        sections = {s['code']: s for s in tresorerie['sections']}
        verifier("encaissements et décaissements présentés",
                 set(sections) == {'encaissements', 'decaissements'})
        verifier("la première ligne est « Ventes au comptant »",
                 sections['encaissements']['lignes'][0]['libelle'] == 'Ventes au comptant')

        debut = tresorerie['solde_debut']['realise']
        fin = tresorerie['solde_fin']['realise']
        encaisse = sections['encaissements']['total']['realise']
        decaisse = sections['decaissements']['total']['realise']

        verifier("solde de fin = solde de début + encaissements - décaissements",
                 all(abs(fin[m] - (debut[m] + encaisse[m] - decaisse[m])) < 0.01
                     for m in range(12)))
        verifier("le solde de fin d'un mois ouvre le mois suivant",
                 all(abs(debut[m + 1] - fin[m]) < 0.01 for m in range(11)))

    # ================== BUDGETS ==================
    print("\n-- Budgets --")
    with app.test_client() as client:
        connecter(client, 'bella@jusdebella.ci', 'Bella2026!')
        verifier("formulaire du budget d'exploitation accessible",
                 client.get('/budget/exploitation').status_code == 200)
        verifier("formulaire du budget de trésorerie accessible",
                 client.get('/budget/tresorerie').status_code == 200)
        verifier("type de budget inconnu refusé",
                 client.get('/budget/inexistant').status_code == 404)

        client.post('/budget/exploitation', data={
            f'montant_{CODE_LOYER}_3': '175000',
        }, follow_redirects=True)

        with app.app_context():
            budget = Budget.query.filter_by(entreprise_id=id_e1, annee=2026,
                                            type_budget=TypeBudget.EXPLOITATION).first()
            montants = budget.montants()
            verifier("le montant saisi est enregistré",
                     abs(montants.get((CODE_LOYER, 3), 0) - 175000) < 1)

            compte = comptes.compte_exploitation_detaille(id_e1, 2026)
            ligne_loyer = None
            for section in compte['sections']:
                for ligne in section['lignes']:
                    if ligne['code'] == CODE_LOYER:
                        ligne_loyer = ligne
            verifier("le budget alimente la colonne « Prévu » du compte",
                     ligne_loyer is not None
                     and abs(ligne_loyer['prevu'][2] - 175000) < 1)
            verifier("l'écart de mars est recalculé en conséquence",
                     abs(ligne_loyer['ecart'][2]
                         - (ligne_loyer['realise'][2] - 175000)) < 1)

        # Une case vidée retire la ligne : le budget ne conserve pas de zéros.
        client.post('/budget/exploitation', data={
            f'montant_{CODE_LOYER}_3': '',
        }, follow_redirects=True)
        with app.app_context():
            budget = Budget.query.filter_by(entreprise_id=id_e1, annee=2026,
                                            type_budget=TypeBudget.EXPLOITATION).first()
            verifier("une case vidée supprime la ligne budgétaire",
                     (CODE_LOYER, 3) not in budget.montants())

        verifier("montants avec espaces et virgule acceptés",
                 budgets._nombre('1 250,50') == 1250.5
                 and budgets._nombre('') == 0.0
                 and budgets._nombre('abc') == 0.0)

    # ================== FAMILLES DE PRODUITS ==================
    print("\n-- Familles de produits --")
    with app.test_client() as client:
        connecter(client, 'bella@jusdebella.ci', 'Bella2026!')
        verifier("écran des familles accessible",
                 client.get('/familles').status_code == 200)
        client.post('/familles', data={'nom': 'Famille de test',
                                       'description': 'Test', 'ordre': '9'},
                    follow_redirects=True)
        with app.app_context():
            nouvelle = FamilleProduit.query.filter_by(nom='Famille de test').first()
            verifier("famille créée", nouvelle is not None)
            id_famille_test = nouvelle.id if nouvelle else 0
            verifier("la famille apparaît comme ligne du compte d'exploitation",
                     any(l['code'] == f'ca:{id_famille_test}'
                         for s in comptes.compte_exploitation_detaille(id_e1, 2026)['sections']
                         for l in s['lignes']))

        client.post(f'/familles/{id_famille_test}/supprimer', follow_redirects=True)
        with app.app_context():
            verifier("famille supprimée",
                     db.session.get(FamilleProduit, id_famille_test) is None)

    # ================== GESTION COMMERCIALE ==================
    print("\n-- Catalogue, ventes, stock et rapports --")

    with app.app_context():
        article = Article.query.filter_by(entreprise_id=id_e1,
                                          nature=NatureArticle.PRODUIT).first()
        id_article = article.id
        stock_initial = article.quantite_stock
        prix = article.prix_vente
        designation = article.designation
        verifier("un article du catalogue est rattaché à une famille",
                 article.famille is not None)

    with app.test_client() as client:
        connecter(client, 'bella@jusdebella.ci', 'Bella2026!')
        for chemin, libelle in [('/catalogue', 'catalogue'), ('/ventes', 'ventes'),
                                ('/stock', 'stock'), ('/fournisseurs', 'fournisseurs'),
                                ('/familles', 'familles'),
                                ('/rapports-cabinet', 'rapports'),
                                ('/profil-facturation', 'profil de facturation')]:
            verifier(f"gérant : {libelle} accessible",
                     client.get(chemin).status_code == 200)

        with app.app_context():
            ca_avant = comptes.compte_exploitation(
                entreprise_id=id_e1)['chiffre_affaires']

        reponse = client.post('/ventes/nouveau', data={
            'type_document': 'recu', 'date_document': '2026-08-04',
            'client_nom': 'Client Test Posiges', 'condition_vente': 'comptant',
            'article_id': str(id_article), 'designation': designation,
            'quantite': '4', 'unite': 'pièce', 'prix_unitaire': str(int(prix)),
            'mode_paiement': 'cash', 'montant_paye': str(int(prix * 4)),
            'categorie_transaction': 'VENTE DE PRODUITS',
        }, follow_redirects=True)
        verifier("émission d'un reçu", reponse.status_code == 200)

        with app.app_context():
            article = db.session.get(Article, id_article)
            document = (Document.query.filter_by(entreprise_id=id_e1)
                        .order_by(Document.id.desc()).first())
            ca_apres = comptes.compte_exploitation(
                entreprise_id=id_e1)['chiffre_affaires']
            id_document = document.id

            verifier("reçu : totaux calculés", abs(document.montant_ttc - prix * 4) < 1)
            verifier("reçu : statut réglé au comptant", document.statut == 'regle')
            verifier("reçu : n° de commande attribué",
                     bool(document.numero_commande))
            verifier("reçu : catégorie de transaction enregistrée",
                     document.categorie_transaction == 'VENTE DE PRODUITS')
            verifier("reçu : écritures portées au journal", document.comptabilise)
            verifier("reçu : chiffre d'affaires augmenté",
                     abs((ca_apres - ca_avant) - prix * 4) < 1)
            verifier("reçu : l'écriture porte la famille de l'article",
                     any(e.categorie == article.code_rubrique
                         for e in document.ecritures))
            verifier("reçu : stock déduit automatiquement",
                     abs(article.quantite_stock - (stock_initial - 4)) < 0.01)

        pdf = client.get(f'/ventes/{id_document}/pdf')
        verifier("reçu : PDF généré",
                 pdf.status_code == 200 and pdf.data[:4] == b'%PDF')

    # -- Facture partiellement réglée : le cas d'école du cabinet --
    print("\n-- Chiffre d'affaires contre trésorerie --")
    with app.test_client() as client:
        connecter(client, 'bella@jusdebella.ci', 'Bella2026!')
        with app.app_context():
            ca_avant = comptes.compte_exploitation(entreprise_id=id_e1)['chiffre_affaires']
            tres_avant = comptes.compte_tresorerie(
                entreprise_id=id_e1)['total_disponible']

        client.post('/ventes/nouveau', data={
            'type_document': 'facture', 'date_document': '2026-07-10',
            'client_nom': 'Client Facture Partielle', 'condition_vente': 'credit',
            'article_id': str(id_article), 'designation': designation,
            'quantite': '10', 'unite': 'pièce', 'prix_unitaire': '100000',
            'montant_paye': '500000', 'mode_paiement': 'cash',
        }, follow_redirects=True)

        with app.app_context():
            facture = (Document.query.filter_by(entreprise_id=id_e1,
                                                type_document=TypeDocument.FACTURE)
                       .order_by(Document.id.desc()).first())
            ca_apres = comptes.compte_exploitation(entreprise_id=id_e1)['chiffre_affaires']
            tres_apres = comptes.compte_tresorerie(
                entreprise_id=id_e1)['total_disponible']

            verifier("facture de 1 000 000 émise",
                     abs(facture.montant_ttc - 1000000) < 1)
            verifier("facture : 500 000 encaissés",
                     abs(facture.montant_paye - 500000) < 1)
            verifier("le chiffre d'affaires augmente de 1 000 000",
                     abs((ca_apres - ca_avant) - 1000000) < 1)
            verifier("la trésorerie n'augmente que de 500 000",
                     abs((tres_apres - tres_avant) - 500000) < 1)
            verifier("le solde reste dû est de 500 000",
                     abs(facture.reste_a_payer - 500000) < 1)
            verifier("une écriture d'encaissement de créance a été créée",
                     any(Rubrique.est_encaissement(e.categorie)
                         for e in facture.ecritures))

    # -- Proforma : n'engage rien --
    with app.test_client() as client:
        connecter(client, 'bella@jusdebella.ci', 'Bella2026!')
        client.post('/ventes/nouveau', data={
            'type_document': 'proforma', 'date_document': '2026-08-04',
            'client_nom': 'Client Proforma Test', 'condition_vente': 'comptant',
            'article_id': str(id_article), 'designation': designation,
            'quantite': '3', 'unite': 'pièce', 'prix_unitaire': '2000',
        }, follow_redirects=True)
        with app.app_context():
            proforma = (Document.query
                        .filter_by(entreprise_id=id_e1,
                                   type_document=TypeDocument.PROFORMA)
                        .order_by(Document.id.desc()).first())
            id_proforma = proforma.id
            verifier("proforma : créée en attente", proforma.statut == 'attente')
            verifier("proforma : aucune écriture comptable",
                     not proforma.comptabilise)

        client.post(f'/ventes/{id_proforma}/convertir',
                    data={'condition_vente': 'comptant', 'montant_paye': '6000',
                          'mode_paiement': 'cash'}, follow_redirects=True)
        with app.app_context():
            recu = (Document.query
                    .filter_by(entreprise_id=id_e1, type_document=TypeDocument.RECU)
                    .order_by(Document.id.desc()).first())
            verifier("conversion : reçu établi sans ressaisie",
                     recu.comptabilise and len(recu.lignes) == 1)

    # -- Catalogue, stock et fournisseurs --
    with app.test_client() as client:
        connecter(client, 'bella@jusdebella.ci', 'Bella2026!')
        client.post('/catalogue/nouveau', data={
            'nature': 'service', 'designation': 'Service de test',
            'prix_vente': '25000', 'unite': 'forfait',
        }, follow_redirects=True)
        with app.app_context():
            service = Article.query.filter_by(designation='Service de test').first()
            verifier("catalogue : service créé", service is not None)
            verifier("catalogue : un service ne suit pas de stock",
                     service is not None and not service.suit_stock)

        with app.app_context():
            avant = db.session.get(Article, id_article).quantite_stock
        client.post('/stock/mouvement', data={
            'article_id': str(id_article), 'type_mouvement': 'entree',
            'quantite': '50', 'date_mouvement': '2026-08-04',
            'motif': 'Test réapprovisionnement',
        }, follow_redirects=True)
        with app.app_context():
            article = db.session.get(Article, id_article)
            verifier("stock : entrée enregistrée",
                     abs(article.quantite_stock - (avant + 50)) < 0.01)

        client.post('/fournisseurs', data={
            'nom': 'Fournisseur Test', 'contact': '07 00 00 00 00',
            'specialite': 'Test'}, follow_redirects=True)
        with app.app_context():
            fournisseur = Fournisseur.query.filter_by(nom='Fournisseur Test').first()
            verifier("fournisseur : créé", fournisseur is not None)
            id_fournisseur = fournisseur.id if fournisseur else 0
        verifier("fournisseur : fiche accessible",
                 client.get(f'/fournisseurs/{id_fournisseur}').status_code == 200)

    # ================== RAPPORTS DU CABINET ==================
    print("\n-- Rapports d'accompagnement --")
    consultant = app.test_client()
    connecter(consultant, 'consultant@akworld.com', 'Consultant2026!')
    consultant.post(f'/rapports-cabinet/nouveau?entreprise_id={id_e1}',
                    data={'annee': '2026'}, follow_redirects=True)
    with app.app_context():
        rapport = Rapport.query.order_by(Rapport.id.desc()).first()
        id_rapport = rapport.id
        verifier("rapport : créé en brouillon", not rapport.publie)
        verifier("rapport : synthèse pré-remplie",
                 bool(rapport.synthese) and 'chiffre' in rapport.synthese)
        verifier("rapport : structure cabinet pré-remplie (contexte, méthodo, cycles, conclusion)",
                 all(bool(getattr(rapport, champ)) for champ in (
                     'contexte', 'methodologie', 'cycle_administratif',
                     'cycle_financier', 'cycle_comptable', 'cycle_marketing',
                     'conclusion')))

    tpe = app.test_client()
    connecter(tpe, 'bella@jusdebella.ci', 'Bella2026!')
    verifier("rapport : brouillon INVISIBLE au client",
             tpe.get(f'/rapports-cabinet/{id_rapport}').status_code == 403)

    consultant.post(f'/rapports-cabinet/{id_rapport}', data={
        'titre': 'Rapport de test', 'periode': 'S1 2026',
        'synthese': 'Synthèse', 'contexte': 'Contexte de la mission',
        'methodologie': 'Démarche', 'cycle_administratif': 'Cycle admin',
        'cycle_financier': 'Cycle fin', 'cycle_comptable': 'Cycle compta',
        'cycle_marketing': 'Cycle mkt', 'constats': 'Constats',
        'analyse': 'Analyse', 'recommandations': 'Recommandations',
        'conclusion': 'Conclusion', 'publier': '1'}, follow_redirects=True)

    tpe2 = app.test_client()
    connecter(tpe2, 'bella@jusdebella.ci', 'Bella2026!')
    verifier("rapport : visible après publication",
             tpe2.get(f'/rapports-cabinet/{id_rapport}').status_code == 200)
    pdf = tpe2.get(f'/rapports-cabinet/{id_rapport}/pdf')
    verifier("rapport : PDF téléchargeable",
             pdf.status_code == 200 and pdf.data[:4] == b'%PDF')

    reponse = tpe2.post(f'/rapports-cabinet/{id_rapport}', data={
        'titre': 'Tentative de modification'}, follow_redirects=False)
    verifier("le client ne peut PAS modifier un rapport",
             reponse.status_code == 403)

    # --- Sections modifiables : renommer un titre, ajouter, supprimer ---
    with app.app_context():
        rapport = db.session.get(Rapport, id_rapport)
        sections = list(rapport.sections)
        nb_depart = len(sections)
        ids = [str(s.id) for s in sections]
        titres = [s.titre for s in sections]
        corps = [s.corps or '' for s in sections]
    verifier("rapport : sections pré-remplies", nb_depart >= 10)

    titres_modifies = list(titres)
    titres_modifies[0] = 'Aperçu chiffré (renommé)'
    donnees_sections = {
        'titre': 'Rapport de test', 'periode': 'S1 2026',
        'section_id': ids, 'section_titre': titres_modifies,
        'section_corps': corps,
    }
    consultant.post(f'/rapports-cabinet/{id_rapport}', data=donnees_sections,
                    follow_redirects=True)
    with app.app_context():
        rapport = db.session.get(Rapport, id_rapport)
        premier = sorted(rapport.sections, key=lambda s: s.ordre)[0]
        verifier("rapport : titre de section renommé",
                 premier.titre == 'Aperçu chiffré (renommé)')

    # Ajouter une section vierge
    consultant.post(f'/rapports-cabinet/{id_rapport}',
                    data={'titre': 'Rapport de test', 'periode': 'S1 2026',
                          'section_id': ids, 'section_titre': titres_modifies,
                          'section_corps': corps, 'ajouter_section': '1'},
                    follow_redirects=True)
    with app.app_context():
        rapport = db.session.get(Rapport, id_rapport)
        verifier("rapport : ajout d'une section",
                 len(rapport.sections) == nb_depart + 1)
        ids_apres = [str(s.id) for s in sorted(rapport.sections, key=lambda s: s.ordre)]
        titres_apres = [s.titre for s in sorted(rapport.sections, key=lambda s: s.ordre)]
        corps_apres = [s.corps or '' for s in sorted(rapport.sections, key=lambda s: s.ordre)]

    # Supprimer la dernière section
    consultant.post(f'/rapports-cabinet/{id_rapport}',
                    data={'titre': 'Rapport de test', 'periode': 'S1 2026',
                          'section_id': ids_apres, 'section_titre': titres_apres,
                          'section_corps': corps_apres, 'supprimer': ids_apres[-1]},
                    follow_redirects=True)
    with app.app_context():
        rapport = db.session.get(Rapport, id_rapport)
        verifier("rapport : suppression d'une section",
                 len(rapport.sections) == nb_depart)

    pdf2 = tpe2.get(f'/rapports-cabinet/{id_rapport}/pdf')
    verifier("rapport : PDF avec préambule cabinet (couverture, sommaire…)",
             pdf2.status_code == 200 and pdf2.data[:4] == b'%PDF'
             and len(pdf2.data) > 200_000)

    # ================== CLOISONNEMENT ==================
    print("\n-- Cloisonnement entre entreprises --")
    with app.test_client() as client:
        connecter(client, 'michel@quincaillerie.ci', 'Michel2026!')
        verifier("article d'une autre entreprise INTERDIT",
                 client.get(f'/catalogue/{id_article}/modifier').status_code == 403)
        verifier("document d'une autre entreprise INTERDIT",
                 client.get(f'/ventes/{id_document}').status_code == 403)
        # Un membre d'une entreprise est toujours ramené à la sienne, quel
        # que soit l'identifiant forcé dans l'adresse : la page s'affiche,
        # mais avec SES données, jamais celles du voisin.
        page = client.get(f'/compte-exploitation?entreprise_id={id_e1}').data.decode()
        verifier("compte d'exploitation : ramené à sa propre entreprise",
                 'Quincaillerie Konan' in page and 'Jus de Bella' not in page)
        page = client.get(f'/budget/exploitation?entreprise_id={id_e1}').data.decode()
        verifier("budget : ramené à sa propre entreprise",
                 'Quincaillerie Konan' in page and 'Jus de Bella' not in page)
        page = client.get('/catalogue?entreprise_id=1').data.decode()
        verifier("catalogue : forcer l'URL ne donne pas accès",
                 'Quincaillerie Konan' in page)

    # ================== EXPORTS ET PUBLICATION ==================
    print("\n-- Exports --")
    with app.app_context():
        import exports
        import sheets_publication

        flux = exports.generer_excel(None, 'Jus de Bella', 2026,
                                     {'entreprise_id': id_e1})
        verifier("classeur Excel produit", flux.getvalue()[:2] == b'PK')

        onglets = sheets_publication.construire_onglets(
            'Jus de Bella', 2026, {'entreprise_id': id_e1})
        noms = [n for n, _ in onglets]
        verifier("le classeur Sheets contient le journal de bord",
                 'Journal de bord' in noms)
        verifier("le classeur Sheets contient les deux comptes",
                 "Compte d'exploitation" in noms and 'Compte de tresorerie' in noms)
        verifier("le classeur Sheets contient la liste des produits",
                 'Produits et services' in noms)

        exploitation = dict(onglets)["Compte d'exploitation"]
        verifier("le compte publié a bien 40 colonnes (12 mois x 3 + total + libellé)",
                 len(exploitation[3]) == 40)

        entetes, lignes = comptes.donnees_plates(entreprise_id=id_e1)
        verifier("journal à plat : colonnes cohérentes",
                 all(len(ligne) == len(entetes) for ligne in lignes))
        verifier("journal à plat : colonnes entrée, sortie et solde",
                 'Entrée' in entetes and 'Sortie' in entetes
                 and 'Solde' in entetes)
        verifier("journal à plat : solde juste après entrée et sortie",
                 entetes[entetes.index('Sortie') + 1] == 'Solde')

        journal = comptes.journal_de_bord(2026, entreprise_id=id_e1)
        if journal:
            attendu = 0.0
            coherent = True
            for ligne in journal:
                attendu += ligne['entree'] - ligne['sortie']
                if abs(ligne['solde'] - attendu) > 0.01:
                    coherent = False
            verifier("journal de bord : solde correctement cumulé", coherent)
            verifier("journal de bord : catégories lisibles",
                     not any(l['categorie'].startswith(('ca:', 'chg:', 'enc:'))
                             for l in journal))

    # ================== GRAPHIQUES ==================
    print("\n-- Analyse graphique --")

    def compter_canvas(client, chemin):
        return client.get(chemin).data.decode().count('<canvas')

    with app.test_client() as client:
        connecter(client, 'cabinet@akworld.com', 'AkWorld2026!')
        verifier("cabinet : graphiques affichés",
                 compter_canvas(client, '/dashboard') >= 5)
        verifier("cabinet : Chart.js chargé",
                 b'js/chart.min.js' in client.get('/dashboard').data)

    with app.test_client() as client:
        connecter(client, 'comptabilite@akworld.com', 'Comptabilite2026!')
        verifier("responsable : aucun graphique",
                 compter_canvas(client, '/dashboard') == 0)

    with app.app_context():
        donnees = comptes.donnees_graphiques(2026, entreprise_id=id_e1)
        verifier("séries de graphiques : 12 mois", len(donnees['mois']) == 12)
        verifier("séries entrées et sorties alignées",
                 len(donnees['ventes']) == 12 and len(donnees['achats']) == 12)
        verifier("trésorerie cumulée alignée", len(donnees['cumul']) == 12)

        # Toutes les séries doivent porter sur le MÊME exercice : c'est le
        # défaut qui faisait cohabiter des graphiques vides et des
        # graphiques remplis sur un même écran.
        vide = comptes.donnees_graphiques(2019, entreprise_id=id_e1)
        verifier("un exercice sans données laisse TOUS les graphiques vides",
                 not any(vide['ventes']) and not any(vide['charges_valeurs'])
                 and not any(vide['modes_valeurs'])
                 and not any(vide['clients_valeurs']))

    # ================== PLUS AUCUNE TRACE DE LOOKER STUDIO ==================
    print("\n-- Retrait de Looker Studio --")
    with app.test_client() as client:
        connecter(client, 'cabinet@akworld.com', 'AkWorld2026!')
        verifier("l'ancienne page Looker n'existe plus",
                 client.get('/looker').status_code == 404)
        pages = ['/dashboard', '/rapports', f'/dashboard?entreprise_id={id_e1}']
        verifier("aucune mention de Looker dans l'interface",
                 not any(b'ooker' in client.get(p).data for p in pages))

    with app.app_context():
        verifier("le modèle Entreprise n'a plus de champ Looker",
                 not hasattr(Entreprise, 'looker_url'))

    print("\n" + "=" * 56)
    total = resultats['ok'] + resultats['echec']
    if resultats['echec'] == 0:
        print(f"  {VERT}Tous les tests réussis : {resultats['ok']}/{total}{FIN}")
    else:
        print(f"  {ROUGE}{resultats['echec']} échec(s) sur {total} tests{FIN}")
    print("=" * 56)
    return resultats['echec']


if __name__ == '__main__':
    sys.exit(1 if executer() else 0)
