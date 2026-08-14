"""
Posiges V1 — application de gestion et de suivi comptable
d'AK World Business Services.

Lancement en développement :   python app.py
Lancement en production     :   gunicorn "app:app"
"""
import os
from datetime import date, datetime
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_file, url_for)
from flask_login import (LoginManager, current_user, login_required, login_user,
                         logout_user)
from werkzeug.utils import secure_filename

import budgets
import comptes
import exports
import google_integration
import migrations
import sheets_publication
from config import Config
from models import (Audit, Budget, Client, Departement, Entreprise,
                    FamilleProduit, ModePaiement, Operation,
                    ParametresCabinet, Role, Rubrique, TypeBudget,
                    TypeOperation, User, db)


# ==========================================================================
# Fabrique d'application
# ==========================================================================

def creer_app(config=Config):
    app = Flask(__name__)
    app.config.from_object(config)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    db.init_app(app)

    gestionnaire = LoginManager()
    gestionnaire.login_view = 'connexion'
    gestionnaire.login_message = "Veuillez vous connecter pour accéder à cette page."
    gestionnaire.init_app(app)

    @gestionnaire.user_loader
    def charger_utilisateur(user_id):
        return db.session.get(User, int(user_id))

    # ---- Variables disponibles dans tous les templates ----
    @app.context_processor
    def injecter_contexte():
        return {
            'DEPARTEMENTS': Departement.CHOIX,
            'LABELS_DEPT': Departement.LABELS,
            'LABELS_MODE': ModePaiement.LABELS,
            'LABELS_TYPE': TypeOperation.LABELS,
            'LABELS_TYPE_COURT': TypeOperation.LABELS_COURTS,
            'TYPES_OPERATION': TypeOperation.CHOIX,
            'LABELS_ROLE': Role.LABELS,
            'DESCRIPTIONS_ROLE': Role.DESCRIPTIONS,
            'ROLES_CABINET': Role.COTE_CABINET,
            'ROLES_ENTREPRISE': Role.COTE_ENTREPRISE,
            'LABELS_BUDGET': TypeBudget.LABELS,
            'TYPES_BUDGET': TypeBudget.CHOIX,
            'DEVISE': app.config['DEVISE'],
            'NOM_CABINET': app.config['NOM_CABINET'],
            'cabinet': parametres_cabinet(),
            'annee_courante': datetime.now().year,
            'google_actif': google_integration.est_configure(
                app.config['GOOGLE_CREDENTIALS_FILE']),
        }

    def parametres_cabinet():
        """Identité du cabinet (logo, coordonnées), disponible partout."""
        parametres = db.session.get(ParametresCabinet, 1)
        if parametres is None:
            parametres = ParametresCabinet(
                id=1, raison_sociale=app.config['NOM_CABINET'])
            db.session.add(parametres)
            db.session.commit()
        return parametres

    @app.template_filter('montant')
    def filtre_montant(valeur):
        try:
            return f"{float(valeur):,.0f}".replace(',', ' ')
        except (TypeError, ValueError):
            return '0'

    @app.template_filter('rubrique')
    def filtre_rubrique(code):
        """Rend lisible un code du plan de rubriques dans les gabarits.

        Les familles de produits sont résolues via un cache de requête : le
        même dictionnaire sert à toutes les lignes d'un tableau.
        """
        familles = getattr(g, '_familles_rubriques', None)
        if familles is None:
            familles = {f.id: f.nom for f in FamilleProduit.query.all()}
            g._familles_rubriques = familles
        return Rubrique.libelle(code, familles)

    @app.errorhandler(403)
    def erreur_403(_):
        return render_template('erreur.html', code=403,
                               message="Vous n'avez pas accès à cette page."), 403

    @app.errorhandler(404)
    def erreur_404(_):
        return render_template('erreur.html', code=404,
                               message="Cette page n'existe pas."), 404

    # ======================================================================
    # Sécurité transverse
    # ======================================================================

    @app.before_request
    def _verifier_origine_requete():
        """Défense anti-CSRF : une requête qui modifie l'état doit provenir
        du site lui-même. Combiné à SESSION_COOKIE_SAMESITE='Lax', ceci
        bloque la soumission de formulaires forgés depuis un autre site,
        sans avoir à instrumenter chaque formulaire d'un jeton dédié.
        """
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            origine = request.headers.get('Origin') or request.headers.get('Referer')
            if origine:
                hote_origine = urlparse(origine).netloc
                if hote_origine and hote_origine != request.host:
                    abort(403)

    @app.after_request
    def _en_tetes_securite(reponse):
        reponse.headers['X-Content-Type-Options'] = 'nosniff'
        reponse.headers['X-Frame-Options'] = 'DENY'
        reponse.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if app.config.get('COOKIE_SECURE'):
            reponse.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains')
        return reponse

    # ======================================================================
    # Décorateurs d'autorisation
    # ======================================================================

    def role_requis(*roles):
        def decorateur(fonction):
            @wraps(fonction)
            @login_required
            def enveloppe(*args, **kwargs):
                if current_user.role not in roles:
                    abort(403)
                return fonction(*args, **kwargs)
            return enveloppe
        return decorateur

    def administration_requise(fonction):
        """Réservé à la direction du cabinet : paramètres, comptes, entreprises."""
        return role_requis(Role.CABINET)(fonction)

    def interne_requis(fonction):
        """Membres du cabinet AK World."""
        return role_requis(Role.CABINET, Role.RESPONSABLE, Role.CONSULTANT)(fonction)

    def cabinet_requis(fonction):
        """Direction du cabinet et consultants.

        Réservé aux fonctions propres au cabinet : rédaction des rapports
        d'accompagnement et publication des classeurs de suivi.
        """
        return role_requis(Role.CABINET, Role.CONSULTANT)(fonction)

    def analyse_requise(fonction):
        """Tous les profils habilités aux tableaux de bord et aux exports.

        Cabinet et consultant côté AK World ; gérant et comptable côté
        entreprise. L'utilisateur standard en est exclu : il dispose d'un
        tableau de bord simplifié, limité à ses propres saisies.
        """
        return role_requis(Role.CABINET, Role.CONSULTANT, Role.GERANT,
                           Role.COMPTABLE)(fonction)

    def budget_requis(fonction):
        """Saisie des budgets : cabinet et gérant de l'entreprise."""
        return role_requis(Role.CABINET, Role.GERANT)(fonction)

    # ======================================================================
    # Détermination du périmètre courant
    # ======================================================================

    def perimetre_courant():
        """Renvoie (scope_dict, titre, entreprise_ou_None).

        `scope_dict` est passé tel quel aux fonctions de comptes.py.
        Un membre d'une entreprise accompagnée est toujours forcé sur sa
        propre entreprise ; un responsable est forcé sur son département.
        Un utilisateur standard est en outre limité à ses propres saisies.
        """
        if current_user.est_client:
            entreprise = current_user.entreprise
            if entreprise is None:
                abort(403)
            scope = {'entreprise_id': entreprise.id}
            if current_user.est_standard:
                scope['cree_par_id'] = current_user.id
            return scope, entreprise.nom, entreprise

        if current_user.est_responsable:
            return ({'departement': current_user.departement, 'interne_seulement': True},
                    Departement.LABELS.get(current_user.departement, 'Département'),
                    None)

        # Gérant / consultant : périmètre choisi via l'URL
        entreprise_id = request.args.get('entreprise_id', type=int)
        if entreprise_id:
            if not current_user.peut_voir_entreprise(entreprise_id):
                abort(403)
            entreprise = db.session.get(Entreprise, entreprise_id)
            return {'entreprise_id': entreprise_id}, entreprise.nom, entreprise

        if current_user.est_consultant:
            # Un consultant n'a pas de vue interne : on lui montre son 1er client
            accessibles = current_user.entreprises_accessibles()
            if accessibles:
                premiere = accessibles[0]
                return {'entreprise_id': premiere.id}, premiere.nom, premiere
            return {'entreprise_id': -1}, 'Aucune entreprise assignée', None

        # Gérant sans filtre : périmètre interne AK World
        departement = request.args.get('departement') or None
        titre = (Departement.LABELS.get(departement) if departement
                 else f"{app.config['NOM_CABINET']} — interne (tous départements)")
        return {'departement': departement, 'interne_seulement': True}, titre, None

    def requete_operations_visibles():
        """Requête des opérations que l'utilisateur courant a le droit de voir."""
        q = Operation.query
        if current_user.est_cabinet:
            return q
        if current_user.est_client:
            q = q.filter(Operation.entreprise_id == current_user.entreprise_id)
            if current_user.est_standard:
                # Tableau de bord simplifié : ses propres opérations, pas
                # celles de ses collègues ni celles de la direction.
                q = q.filter(Operation.cree_par_id == current_user.id)
            return q
        if current_user.est_consultant:
            ids = [e.id for e in current_user.entreprises_accessibles()] or [-1]
            return q.filter(Operation.entreprise_id.in_(ids))
        # Responsable de département interne
        return q.filter(Operation.entreprise_id.is_(None),
                        Operation.departement == current_user.departement)

    # ======================================================================
    # AUTHENTIFICATION
    # ======================================================================

    @app.route('/connexion', methods=['GET', 'POST'])
    def connexion():
        if current_user.is_authenticated:
            return redirect(url_for('accueil'))
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            utilisateur = User.query.filter_by(email=email).first()

            if utilisateur and utilisateur.est_verrouille():
                flash("Trop de tentatives échouées. Réessayez dans "
                      f"{User.DUREE_VERROUILLAGE_MINUTES} minutes.", 'erreur')
                return render_template('login.html')

            if (utilisateur and utilisateur.actif
                    and utilisateur.check_password(request.form.get('password', ''))):
                utilisateur.reinitialiser_echecs()
                login_user(utilisateur)
                utilisateur.derniere_connexion = datetime.utcnow()
                db.session.commit()
                suivant = request.args.get('next')
                return redirect(suivant or url_for('accueil'))

            if utilisateur:
                utilisateur.enregistrer_echec()
                db.session.commit()
            flash("Email ou mot de passe incorrect.", 'erreur')
        return render_template('login.html')

    @app.route('/deconnexion')
    @login_required
    def deconnexion():
        logout_user()
        return redirect(url_for('connexion'))

    @app.route('/profil', methods=['GET', 'POST'])
    @login_required
    def profil():
        if request.method == 'POST':
            actuel = request.form.get('mot_de_passe_actuel', '')
            nouveau = request.form.get('nouveau_mot_de_passe', '')
            confirmation = request.form.get('confirmation', '')

            if not current_user.check_password(actuel):
                flash("Le mot de passe actuel est incorrect.", 'erreur')
            elif len(nouveau) < 8:
                flash("Le nouveau mot de passe doit contenir au moins 8 caractères.", 'erreur')
            elif nouveau != confirmation:
                flash("La confirmation ne correspond pas au nouveau mot de passe.", 'erreur')
            else:
                current_user.set_password(nouveau)
                Audit.journaliser(current_user, 'modification', 'MotDePasse',
                                  current_user.id, 'Changement de mot de passe')
                db.session.commit()
                flash("Mot de passe modifié avec succès.", 'succes')
                return redirect(url_for('profil'))
        return render_template('profil.html')

    # ======================================================================
    # ACCUEIL / TABLEAU DE BORD
    # ======================================================================

    @app.route('/')
    @login_required
    def accueil():
        if current_user.est_standard:
            return redirect(url_for('espace_client'))
        return redirect(url_for('dashboard'))

    @app.route('/dashboard')
    @role_requis(Role.CABINET, Role.CONSULTANT, Role.RESPONSABLE, Role.GERANT,
                 Role.COMPTABLE)
    def dashboard():
        scope, titre, entreprise = perimetre_courant()
        annee = request.args.get('annee', type=int) or datetime.now().year

        scope_annuel = dict(scope)
        scope_annuel.update(date_debut=date(annee, 1, 1), date_fin=date(annee, 12, 31))

        exploitation = comptes.compte_exploitation(**scope_annuel)
        tresorerie = comptes.compte_tresorerie(**scope_annuel)
        resume = comptes.resume_mensuel(annee, **scope)
        evolution = comptes.evolution_periode(annee, **scope)
        meilleurs = comptes.top_clients(limite=5, **scope_annuel)

        dernieres = (requete_operations_visibles()
                     .order_by(Operation.date_creation.desc()).limit(8).all())

        # Analyse graphique : cabinet, consultant, gérant et comptable. Un
        # responsable de département interne voit ses chiffres sans les
        # analyses ; le calcul lui-même est évité, inutile de le produire.
        acces_analyse = current_user.peut_analyser
        graphiques = comptes.donnees_graphiques(annee, **scope) if acces_analyse else None

        return render_template(
            'dashboard.html',
            titre_perimetre=titre,
            entreprise=entreprise,
            scope_args=_args_perimetre(),
            exploitation=exploitation,
            tresorerie=tresorerie,
            resume=resume,
            evolution=evolution,
            meilleurs=meilleurs,
            dernieres=dernieres,
            graphiques=graphiques,
            acces_analyse=acces_analyse,
            annee=annee,
            entreprises=current_user.entreprises_accessibles(),
            filtre_departement=request.args.get('departement'),
            filtre_entreprise=request.args.get('entreprise_id', type=int),
        )

    @app.route('/espace-client')
    @role_requis(Role.STANDARD)
    def espace_client():
        """Tableau de bord simplifié de l'utilisateur standard.

        Il ne porte que sur les opérations que cet utilisateur a lui-même
        enregistrées : totaux du mois et de l'année, et ses dernières
        saisies. Il ne voit ni les chiffres de ses collègues, ni ceux de la
        direction de l'entreprise.
        """
        entreprise = current_user.entreprise
        if entreprise is None:
            abort(403)
        annee = datetime.now().year
        scope = {'entreprise_id': entreprise.id, 'cree_par_id': current_user.id}

        # Les indicateurs affichés portent tous sur l'exercice en cours, comme
        # le tableau mensuel juste en dessous : on borne donc explicitement.
        scope_annuel = dict(scope)
        scope_annuel.update(date_debut=date(annee, 1, 1), date_fin=date(annee, 12, 31))

        exploitation = comptes.compte_exploitation(**scope_annuel)
        tresorerie = comptes.compte_tresorerie(**scope_annuel)
        resume = comptes.resume_mensuel(annee, **scope)

        mois_courant = datetime.now().month
        debut, fin = comptes.bornes_mois(annee, mois_courant)
        exploitation_mois = comptes.compte_exploitation(
            date_debut=debut, date_fin=fin, **scope)

        dernieres = (Operation.query
                     .filter_by(entreprise_id=entreprise.id,
                                cree_par_id=current_user.id)
                     .order_by(Operation.date_operation.desc(), Operation.id.desc())
                     .limit(10).all())

        return render_template(
            'espace_client.html',
            entreprise=entreprise,
            exploitation=exploitation,
            exploitation_mois=exploitation_mois,
            tresorerie=tresorerie,
            resume=resume,
            dernieres=dernieres,
            annee=annee,
            mois_libelle=comptes.MOIS_FR[f'{mois_courant:02d}'],
        )

    # ======================================================================
    # COMPTE D'EXPLOITATION ET COMPTE DE TRÉSORERIE
    # ======================================================================

    def _entreprise_du_compte():
        """Entreprise sur laquelle porter un compte détaillé."""
        if current_user.est_client:
            return current_user.entreprise
        entreprise_id = request.args.get('entreprise_id', type=int)
        if entreprise_id:
            if not current_user.peut_voir_entreprise(entreprise_id):
                abort(403)
            return db.session.get(Entreprise, entreprise_id)
        accessibles = current_user.entreprises_accessibles()
        return accessibles[0] if len(accessibles) == 1 else None

    @app.route('/compte-exploitation')
    @analyse_requise
    def compte_exploitation():
        """Compte d'exploitation mensuel, format du cabinet."""
        entreprise = _entreprise_du_compte()
        if entreprise is None:
            return render_template('choisir_entreprise.html',
                                   titre="Compte d'exploitation")
        annee = request.args.get('annee', type=int) or datetime.now().year
        compte = comptes.compte_exploitation_detaille(
            entreprise.id, annee, cree_par_id=current_user.filtre_saisies_propres())

        return render_template(
            'compte_exploitation.html',
            titre="Compte d'exploitation",
            sous_titre="Rentabilité mensuelle de l'activité : "
                       "l'entreprise gagne-t-elle de l'argent ?",
            compte=compte, entreprise=entreprise, annee=annee,
            type_budget=TypeBudget.EXPLOITATION,
            scope_args={'entreprise_id': entreprise.id},
        )

    @app.route('/compte-tresorerie')
    @analyse_requise
    def compte_tresorerie():
        """Compte de trésorerie mensuel, format du cabinet."""
        entreprise = _entreprise_du_compte()
        if entreprise is None:
            return render_template('choisir_entreprise.html',
                                   titre='Compte de trésorerie')
        annee = request.args.get('annee', type=int) or datetime.now().year
        compte = comptes.compte_tresorerie_detaille(
            entreprise.id, annee, cree_par_id=current_user.filtre_saisies_propres())

        return render_template(
            'compte_tresorerie.html',
            titre='Compte de trésorerie',
            sous_titre="Liquidité réelle de l'entreprise : "
                       "l'argent est-il effectivement en caisse ?",
            compte=compte, entreprise=entreprise, annee=annee,
            type_budget=TypeBudget.TRESORERIE,
            scope_args={'entreprise_id': entreprise.id},
        )

    # ======================================================================
    # BUDGETS
    # ======================================================================

    @app.route('/budget')
    @budget_requis
    def budget_accueil():
        entreprise = _entreprise_du_compte()
        if entreprise is None:
            return render_template('choisir_entreprise.html', titre='Budgets')
        annee = request.args.get('annee', type=int) or datetime.now().year
        return render_template(
            'budget_accueil.html',
            entreprise=entreprise, annee=annee,
            synthese=budgets.synthese(entreprise.id, annee),
            scope_args={'entreprise_id': entreprise.id},
        )

    @app.route('/budget/<type_budget>', methods=['GET', 'POST'])
    @budget_requis
    def budget(type_budget):
        if type_budget not in TypeBudget.CHOIX:
            abort(404)
        entreprise = _entreprise_du_compte()
        if entreprise is None:
            return redirect(url_for('budget_accueil'))
        if not current_user.peut_voir_entreprise(entreprise.id):
            abort(403)

        annee = request.args.get('annee', type=int) or datetime.now().year
        ligne_budget = budgets.obtenir_ou_creer(entreprise.id, annee,
                                                type_budget, current_user)

        if request.method == 'POST':
            if request.form.get('recopier'):
                reprises = budgets.recopier_exercice_precedent(ligne_budget)
                flash(f"{reprises} montant(s) repris de l'exercice {annee - 1}."
                      if reprises else
                      f"Aucun budget {annee - 1} à reprendre.", 'info')
            else:
                modifiees = budgets.enregistrer(ligne_budget, request.form)
                Audit.journaliser(current_user, 'modification', 'Budget',
                                  ligne_budget.id,
                                  f"{TypeBudget.LABELS[type_budget]} {annee} — "
                                  f"{modifiees} ligne(s)")
                db.session.commit()
                flash(f"Budget enregistré. Les colonnes « Prévu » du "
                      f"{TypeBudget.LABELS[type_budget].lower()} sont à jour.",
                      'succes')
            return redirect(url_for('budget', type_budget=type_budget,
                                    entreprise_id=entreprise.id, annee=annee))

        return render_template(
            'budget_form.html',
            entreprise=entreprise, annee=annee, type_budget=type_budget,
            budget=ligne_budget,
            sections=budgets.structure(entreprise.id, type_budget),
            montants=ligne_budget.montants(),
            mois=comptes.entetes_mois(),
            scope_args={'entreprise_id': entreprise.id},
        )

    def _args_perimetre():
        """Reconstruit les paramètres d'URL du périmètre courant (pour les liens)."""
        args = {}
        if request.args.get('entreprise_id'):
            args['entreprise_id'] = request.args.get('entreprise_id', type=int)
        if request.args.get('departement'):
            args['departement'] = request.args.get('departement')
        return args

    # ======================================================================
    # JOURNAL DE BORD
    #
    # Colonnes arrêtées avec le cabinet : Date, Libellé, Catégorie, Entrée,
    # Sortie, Mode de paiement, Solde cumulé, Référence de la pièce. Le solde
    # cumulé est ce qui fait du journal un outil de contrôle : dès qu'il
    # devient négatif, il y a une erreur de saisie ou un trou de caisse.
    # ======================================================================

    @app.route('/journal')
    @login_required
    def journal():
        q = requete_operations_visibles()

        # Filtres additionnels
        entreprise_id = request.args.get('entreprise_id', type=int)
        departement = request.args.get('departement')
        type_op = request.args.get('type')
        recherche = (request.args.get('q') or '').strip()

        if entreprise_id:
            if not current_user.peut_voir_entreprise(entreprise_id):
                abort(403)
            q = q.filter(Operation.entreprise_id == entreprise_id)
        elif departement and current_user.est_cabinet:
            q = q.filter(Operation.entreprise_id.is_(None),
                         Operation.departement == departement)
        if type_op in TypeOperation.CHOIX:
            q = q.filter(Operation.type_operation == type_op)
        if recherche:
            motif = f'%{recherche}%'
            q = q.filter(db.or_(Operation.categorie.ilike(motif),
                                Operation.libelle.ilike(motif),
                                Operation.reference.ilike(motif),
                                Operation.numero_facture.ilike(motif)))

        # Le solde se cumule dans l'ordre chronologique ; l'affichage se fait
        # ensuite du plus récent au plus ancien, comme un relevé de compte.
        operations = q.order_by(Operation.date_operation, Operation.id).all()[-500:]

        libelles = comptes.libelles_rubriques(
            entreprise_id or (current_user.entreprise_id
                              if current_user.est_client else None))
        solde = 0.0
        lignes = []
        for operation in operations:
            entree = operation.montant if operation.montant_signe() >= 0 else 0.0
            sortie = operation.montant if operation.montant_signe() < 0 else 0.0
            solde += entree - sortie
            lignes.append({
                'operation': operation,
                'categorie': libelles.get(operation.categorie, operation.categorie),
                'entree': entree,
                'sortie': sortie,
                'solde': solde,
            })
        lignes.reverse()

        total_entrees = sum(l['entree'] for l in lignes)
        total_sorties = sum(l['sortie'] for l in lignes)

        return render_template(
            'journal.html',
            lignes=lignes,
            solde_final=solde,
            total_entrees=total_entrees,
            total_sorties=total_sorties,
            entreprises=current_user.entreprises_accessibles(),
            filtre_entreprise=entreprise_id,
            filtre_departement=departement,
            filtre_type=type_op,
            recherche=recherche,
        )

    def _generer_numero_facture(prefixe_source):
        prefixe = ''.join(c for c in prefixe_source.upper() if c.isalnum())[:3] or 'OPE'
        annee = datetime.now().year
        base = f"{prefixe}-{annee}-"
        dernier = (Operation.query
                   .filter(Operation.numero_facture.like(f'{base}%'))
                   .order_by(Operation.numero_facture.desc()).first())
        numero = int(dernier.numero_facture.split('-')[-1]) + 1 if dernier else 1
        return f"{base}{numero:05d}"

    def _perimetre_saisie():
        """Détermine à quel périmètre rattacher une nouvelle opération."""
        if current_user.est_client:
            return None, current_user.entreprise_id

        entreprise_id = request.form.get('entreprise_id', type=int)
        if entreprise_id:
            if not current_user.peut_voir_entreprise(entreprise_id):
                abort(403)
            return None, entreprise_id

        if current_user.est_consultant:
            abort(400, "Un consultant doit préciser l'entreprise concernée.")

        departement = (request.form.get('departement') if current_user.est_cabinet
                       else current_user.departement)
        if departement not in Departement.CHOIX:
            abort(400, "Département invalide.")
        return departement, None

    def _resoudre_client(nom_client, entreprise_id):
        """Trouve le client par nom dans le bon carnet d'adresses, ou le crée."""
        nom_client = (nom_client or '').strip()
        if not nom_client:
            return None
        q = Client.query.filter(Client.nom.ilike(nom_client))
        q = (q.filter(Client.entreprise_id == entreprise_id) if entreprise_id
             else q.filter(Client.entreprise_id.is_(None)))
        client = q.first()
        if client is None:
            client = Client(nom=nom_client, entreprise_id=entreprise_id)
            db.session.add(client)
            db.session.flush()
        return client

    def _enregistrer_justificatif(operation, fichier):
        """Sauvegarde locale + envoi Drive. Ne bloque jamais l'enregistrement."""
        if not fichier or not fichier.filename:
            return
        extension = os.path.splitext(fichier.filename)[1].lower()
        if extension not in app.config['EXTENSIONS_AUTORISEES']:
            flash(f"Format de fichier non autorisé ({extension}). "
                  "L'opération a été enregistrée sans justificatif.", 'erreur')
            return

        nom = secure_filename(f"{operation.numero_facture}_{fichier.filename}")
        chemin = os.path.join(app.config['UPLOAD_FOLDER'], nom)
        fichier.save(chemin)
        operation.fichier_local = chemin

        dossier = app.config['GOOGLE_DRIVE_FOLDER_ID']
        if operation.entreprise and operation.entreprise.drive_folder_id:
            dossier = operation.entreprise.drive_folder_id
        lien = google_integration.upload_fichier(
            chemin, nom, app.config['GOOGLE_CREDENTIALS_FILE'], dossier)
        if lien:
            operation.fichier_url = lien

    def _synchroniser(operation):
        sheet = app.config['GOOGLE_SHEET_ID']
        if operation.entreprise and operation.entreprise.sheet_id:
            sheet = operation.entreprise.sheet_id
        operation.synchro_sheet = google_integration.ajouter_ligne_sheet(
            operation, app.config['GOOGLE_CREDENTIALS_FILE'], sheet)

    @app.route('/journal/nouvelle', methods=['GET', 'POST'])
    @login_required
    def nouvelle_operation():
        if request.method == 'POST':
            departement, entreprise_id = _perimetre_saisie()
            client = _resoudre_client(request.form.get('client_nom'), entreprise_id)

            source_prefixe = departement or (
                db.session.get(Entreprise, entreprise_id).nom if entreprise_id else 'OPE')

            operation = Operation(
                date_operation=datetime.strptime(
                    request.form['date_operation'], '%Y-%m-%d').date(),
                type_operation=request.form['type_operation'],
                categorie=request.form['categorie'].strip(),
                libelle=(request.form.get('libelle') or '').strip(),
                departement=departement,
                entreprise_id=entreprise_id,
                client=client,
                montant=abs(float(request.form['montant'])),
                mode_paiement=request.form['mode_paiement'],
                reference=(request.form.get('reference') or '').strip(),
                numero_facture=_generer_numero_facture(source_prefixe),
                cree_par_id=current_user.id,
            )
            db.session.add(operation)
            db.session.commit()

            _enregistrer_justificatif(operation, request.files.get('justificatif'))
            _synchroniser(operation)
            Audit.journaliser(
                current_user, 'creation', 'Operation', operation.id,
                f"{operation.type_operation} {operation.montant:.0f} — {operation.categorie}")
            db.session.commit()

            flash(f"Opération enregistrée (facture {operation.numero_facture}). "
                  "Les comptes ont été mis à jour automatiquement.", 'succes')
            if request.form.get('continuer'):
                return redirect(url_for('nouvelle_operation'))
            return redirect(url_for('journal'))

        return render_template('operation_form.html', operation=None,
                               aujourdhui=date.today().isoformat(),
                               entreprises=current_user.entreprises_accessibles(),
                               clients=_clients_accessibles(),
                               **_rubriques_de_saisie())

    def _rubriques_de_saisie(entreprise_id=None):
        """Listes fermées de rubriques proposées au formulaire de saisie.

        Les deux listes sont transmises ensemble : le formulaire bascule de
        l'une à l'autre selon que l'utilisateur enregistre une entrée ou une
        sortie, sans recharger la page.
        """
        if entreprise_id is None:
            if current_user.est_client:
                entreprise_id = current_user.entreprise_id
            else:
                entreprise_id = request.args.get('entreprise_id', type=int)
        return {
            'rubriques_entree': comptes.rubriques_de_saisie(
                entreprise_id, TypeOperation.VENTE),
            'rubriques_sortie': comptes.rubriques_de_saisie(
                entreprise_id, TypeOperation.ACHAT),
        }

    def _clients_accessibles():
        if current_user.est_client:
            return (Client.query.filter_by(entreprise_id=current_user.entreprise_id)
                    .order_by(Client.nom).all())
        if current_user.est_cabinet:
            return Client.query.order_by(Client.nom).all()
        if current_user.est_consultant:
            ids = [e.id for e in current_user.entreprises_accessibles()] or [-1]
            return Client.query.filter(Client.entreprise_id.in_(ids)).order_by(Client.nom).all()
        return Client.query.filter(Client.entreprise_id.is_(None)).order_by(Client.nom).all()

    @app.route('/journal/<int:op_id>/modifier', methods=['GET', 'POST'])
    @login_required
    def modifier_operation(op_id):
        operation = db.get_or_404(Operation, op_id)
        if not current_user.peut_modifier_operation(operation):
            abort(403)

        if request.method == 'POST':
            ancien = (f"{operation.type_operation} {operation.montant:.0f} "
                      f"{operation.categorie} {operation.date_operation}")

            operation.date_operation = datetime.strptime(
                request.form['date_operation'], '%Y-%m-%d').date()
            operation.type_operation = request.form['type_operation']
            operation.categorie = request.form['categorie'].strip()
            operation.libelle = (request.form.get('libelle') or '').strip()
            operation.montant = abs(float(request.form['montant']))
            operation.mode_paiement = request.form['mode_paiement']
            operation.reference = (request.form.get('reference') or '').strip()
            operation.client = _resoudre_client(
                request.form.get('client_nom'), operation.entreprise_id)

            _enregistrer_justificatif(operation, request.files.get('justificatif'))
            Audit.journaliser(
                current_user, 'modification', 'Operation', operation.id,
                f"Avant : {ancien} | Après : {operation.type_operation} "
                f"{operation.montant:.0f} {operation.categorie} {operation.date_operation}")
            db.session.commit()

            flash("Opération modifiée. Pensez à resynchroniser le Google Sheet "
                  "si vous l'utilisez.", 'succes')
            return redirect(url_for('journal'))

        return render_template('operation_form.html', operation=operation,
                               aujourdhui=operation.date_operation.isoformat(),
                               entreprises=current_user.entreprises_accessibles(),
                               clients=_clients_accessibles(),
                               **_rubriques_de_saisie(operation.entreprise_id))

    @app.route('/journal/<int:op_id>/supprimer', methods=['POST'])
    @login_required
    def supprimer_operation(op_id):
        operation = db.get_or_404(Operation, op_id)
        if not current_user.peut_supprimer_operation(operation):
            abort(403)
        Audit.journaliser(
            current_user, 'suppression', 'Operation', operation.id,
            f"{operation.type_operation} {operation.montant:.0f} {operation.categorie} "
            f"(facture {operation.numero_facture})")
        db.session.delete(operation)
        db.session.commit()
        flash("Opération supprimée. L'action a été enregistrée dans l'historique.", 'succes')
        return redirect(url_for('journal'))

    # ======================================================================
    # CLIENTS (carnet d'adresses)
    # ======================================================================

    @app.route('/clients', methods=['GET', 'POST'])
    @login_required
    def clients():
        if request.method == 'POST':
            entreprise_id = (current_user.entreprise_id if current_user.est_client
                             else request.form.get('entreprise_id', type=int))
            if entreprise_id and not current_user.peut_voir_entreprise(entreprise_id):
                abort(403)
            client = Client(
                nom=request.form['nom'].strip(),
                contact=(request.form.get('contact') or '').strip(),
                localisation=(request.form.get('localisation') or '').strip(),
                entreprise_id=entreprise_id,
            )
            db.session.add(client)
            Audit.journaliser(current_user, 'creation', 'Client', None, client.nom)
            db.session.commit()
            flash("Client ajouté au carnet d'adresses.", 'succes')
            return redirect(url_for('clients'))

        return render_template('clients.html', clients=_clients_accessibles(),
                               entreprises=current_user.entreprises_accessibles())

    # ======================================================================
    # ENTREPRISES ACCOMPAGNÉES
    # ======================================================================

    @app.route('/entreprises')
    @interne_requis
    def entreprises():
        liste = current_user.entreprises_accessibles()
        statistiques = {}
        for entreprise in liste:
            scope = {'entreprise_id': entreprise.id}
            exploitation = comptes.compte_exploitation(**scope)
            statistiques[entreprise.id] = {
                'ca': exploitation['chiffre_affaires'],
                'resultat': exploitation['resultat_net'],
                'operations': Operation.query.filter_by(entreprise_id=entreprise.id).count(),
            }
        return render_template('entreprises.html', entreprises=liste,
                               statistiques=statistiques)

    @app.route('/entreprises/nouvelle', methods=['GET', 'POST'])
    @administration_requise
    def nouvelle_entreprise():
        if request.method == 'POST':
            entreprise = Entreprise(
                nom=request.form['nom'].strip(),
                secteur=(request.form.get('secteur') or '').strip(),
                contact=(request.form.get('contact') or '').strip(),
                localisation=(request.form.get('localisation') or '').strip(),
                drive_folder_id=(request.form.get('drive_folder_id') or '').strip(),
                sheet_id=(request.form.get('sheet_id') or '').strip(),
                sheet_rapport_id=(request.form.get('sheet_rapport_id') or '').strip() or None,
            )
            db.session.add(entreprise)
            db.session.flush()

            # Création automatique du dossier Drive dédié si Google est configuré
            if not entreprise.drive_folder_id:
                dossier = google_integration.creer_dossier_entreprise(
                    entreprise.nom, app.config['GOOGLE_CREDENTIALS_FILE'],
                    app.config['GOOGLE_DRIVE_FOLDER_ID'])
                if dossier:
                    entreprise.drive_folder_id = dossier

            # Consultants assignés
            for consultant_id in request.form.getlist('consultants', type=int):
                consultant = db.session.get(User, consultant_id)
                if consultant:
                    entreprise.consultants.append(consultant)

            Audit.journaliser(current_user, 'creation', 'Entreprise',
                              entreprise.id, entreprise.nom)
            db.session.commit()
            flash(f"Entreprise « {entreprise.nom} » créée. "
                  "Vous pouvez maintenant lui créer un compte utilisateur.", 'succes')
            return redirect(url_for('entreprises'))

        return render_template(
            'entreprise_form.html', entreprise=None,
            consultants=User.query.filter_by(role=Role.CONSULTANT).order_by(User.nom).all())

    @app.route('/entreprises/<int:entreprise_id>/modifier', methods=['GET', 'POST'])
    @administration_requise
    def modifier_entreprise(entreprise_id):
        entreprise = db.get_or_404(Entreprise, entreprise_id)
        if request.method == 'POST':
            entreprise.nom = request.form['nom'].strip()
            entreprise.secteur = (request.form.get('secteur') or '').strip()
            entreprise.contact = (request.form.get('contact') or '').strip()
            entreprise.localisation = (request.form.get('localisation') or '').strip()
            entreprise.drive_folder_id = (request.form.get('drive_folder_id') or '').strip()
            entreprise.sheet_id = (request.form.get('sheet_id') or '').strip()

            # Classeur de publication : si le gérant colle l'identifiant d'un
            # classeur qu'il a créé lui-même, on l'utilise tel quel et l'URL
            # est reconstruite. Sinon l'application en créera un.
            rapport_id = (request.form.get('sheet_rapport_id') or '').strip()
            if rapport_id != (entreprise.sheet_rapport_id or ''):
                entreprise.sheet_rapport_id = rapport_id or None
                entreprise.sheet_rapport_url = (
                    google_integration.url_classeur(rapport_id) if rapport_id else None)
            entreprise.actif = bool(request.form.get('actif'))

            entreprise.consultants = []
            for consultant_id in request.form.getlist('consultants', type=int):
                consultant = db.session.get(User, consultant_id)
                if consultant:
                    entreprise.consultants.append(consultant)

            Audit.journaliser(current_user, 'modification', 'Entreprise',
                              entreprise.id, entreprise.nom)
            db.session.commit()
            flash("Entreprise mise à jour.", 'succes')
            return redirect(url_for('entreprises'))

        return render_template(
            'entreprise_form.html', entreprise=entreprise,
            consultants=User.query.filter_by(role=Role.CONSULTANT).order_by(User.nom).all())

    # ======================================================================
    # RAPPORTS ET EXPORTS
    # ======================================================================

    @app.route('/rapports', methods=['GET', 'POST'])
    @login_required
    def rapports():
        scope, titre, entreprise = perimetre_courant()
        annee = request.args.get('annee', type=int) or datetime.now().year
        return render_template('rapports.html', titre_perimetre=titre,
                               entreprise=entreprise, annee=annee,
                               entreprises=current_user.entreprises_accessibles(),
                               filtre_entreprise=request.args.get('entreprise_id', type=int),
                               filtre_departement=request.args.get('departement'))

    @app.route('/export/excel')
    @login_required
    def export_excel():
        scope, titre, _ = perimetre_courant()
        annee = request.args.get('annee', type=int) or datetime.now().year

        scope_annuel = dict(scope)
        scope_annuel.update(date_debut=date(annee, 1, 1), date_fin=date(annee, 12, 31))
        operations = (comptes.requete_base(**scope_annuel)
                      .order_by(Operation.date_operation, Operation.id).all())

        flux = exports.generer_excel(operations, titre, annee, scope_annuel)
        nom = f"AKWorld_{_slug(titre)}_{annee}.xlsx"
        Audit.journaliser(current_user, 'export', 'Excel', None, nom)
        db.session.commit()
        return send_file(
            flux, as_attachment=True, download_name=nom,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.route('/export/pdf')
    @login_required
    def export_pdf():
        scope, titre, _ = perimetre_courant()
        annee = request.args.get('annee', type=int) or datetime.now().year
        commentaire = request.args.get('commentaire') or None

        scope_annuel = dict(scope)
        scope_annuel.update(date_debut=date(annee, 1, 1), date_fin=date(annee, 12, 31))

        flux = exports.generer_rapport_pdf(
            titre, annee, scope_annuel, auteur=current_user.nom,
            commentaire_consultant=commentaire)
        nom = f"Rapport_{_slug(titre)}_{annee}.pdf"
        Audit.journaliser(current_user, 'export', 'PDF', None, nom)
        db.session.commit()
        return send_file(flux, as_attachment=True, download_name=nom,
                         mimetype='application/pdf')

    def _slug(texte):
        return ''.join(c if c.isalnum() else '_' for c in texte).strip('_')[:40]

    @app.route('/export/sheets', methods=['POST'])
    @analyse_requise
    def export_sheets():
        """Publie le classeur de suivi vers Google Sheets.

        Le classeur reprend les quatre onglets arrêtés avec le cabinet :
        journal de bord, compte d'exploitation, compte de trésorerie et
        liste des produits et services.
        """
        scope, titre, entreprise = perimetre_courant()
        annee = request.args.get('annee', type=int) or datetime.now().year

        scope_annuel = dict(scope)
        scope_annuel.update(date_debut=date(annee, 1, 1), date_fin=date(annee, 12, 31))

        raison = google_integration.raison_indisponibilite(
            app.config['GOOGLE_CREDENTIALS_FILE'])
        if raison:
            flash(f"Publication impossible — {raison}", 'erreur')
            flash("Pour un diagnostic complet, lancez dans le dossier du projet : "
                  "python diagnostic_google.py — En attendant, l'export Excel "
                  "reste disponible.", 'info')
            return redirect(request.referrer or url_for('rapports'))

        # Réutiliser le classeur existant pour que son adresse reste valable
        # d'une publication à l'autre — c'est ce lien qui est communiqué au
        # client. Si aucun classeur n'existe encore, on réutilise celui
        # configuré en base plutôt que d'en créer un nouveau, création que le
        # compte de service n'est pas toujours autorisé à faire.
        sheet_existant = (
            entreprise.sheet_rapport_id if entreprise and entreprise.sheet_rapport_id
            else app.config.get('GOOGLE_SHEET_RAPPORT_ID')
            or app.config.get('GOOGLE_SHEET_ID')
            or None
        )
        dossier = (entreprise.drive_folder_id if entreprise and entreprise.drive_folder_id
                   else app.config['GOOGLE_DRIVE_FOLDER_ID'])

        sheet_id, url, lignes, erreur = sheets_publication.publier(
            titre, annee, scope_annuel,
            app.config['GOOGLE_CREDENTIALS_FILE'],
            sheet_id=sheet_existant,
            dossier_drive=dossier)

        if not sheet_id:
            # On affiche la cause réelle plutôt qu'un message générique :
            # l'utilisateur peut ainsi corriger sans lire les journaux.
            flash(f"Publication impossible — {erreur or 'cause inconnue'}",
                  'erreur')
            flash("Pour un diagnostic complet, lancez dans le dossier du "
                  "projet : python diagnostic_google.py", 'info')
            return redirect(request.referrer or url_for('rapports'))

        if entreprise:
            entreprise.sheet_rapport_id = sheet_id
            entreprise.sheet_rapport_url = url
            entreprise.derniere_publication = datetime.utcnow()

        Audit.journaliser(current_user, 'export', 'GoogleSheets', None,
                          f"{titre} {annee} — {lignes} lignes publiées")
        db.session.commit()

        flash(f"{lignes} opérations publiées dans Google Sheets — journal de "
              f"bord, compte d'exploitation, compte de trésorerie et liste "
              f"des produits et services.", 'succes')
        if url:
            flash(f"Ouvrir le classeur : {url}", 'info')
        return redirect(request.referrer or url_for('rapports'))

    @app.route('/synchroniser', methods=['POST'])
    @interne_requis
    def synchroniser_sheet():
        """Réécrit intégralement le Google Sheet du périmètre courant."""
        scope, titre, entreprise = perimetre_courant()
        operations = (comptes.requete_base(**scope)
                      .order_by(Operation.date_operation, Operation.id).all())

        sheet = app.config['GOOGLE_SHEET_ID']
        if entreprise and entreprise.sheet_id:
            sheet = entreprise.sheet_id

        nombre = google_integration.resynchroniser_journal(
            operations, app.config['GOOGLE_CREDENTIALS_FILE'], sheet)
        if nombre:
            flash(f"{nombre} opérations resynchronisées vers Google Sheets.", 'succes')
        else:
            raison = google_integration.raison_indisponibilite(
                app.config['GOOGLE_CREDENTIALS_FILE'])
            if raison:
                flash(f"Resynchronisation impossible — {raison}", 'erreur')
            elif not sheet:
                flash("Resynchronisation impossible — aucun Google Sheet n'est "
                      "défini. Renseignez GOOGLE_SHEET_ID dans le fichier .env, "
                      "ou l'identifiant du Sheet de l'entreprise dans "
                      "« Entreprises suivies → Modifier ».", 'erreur')
            else:
                flash("Resynchronisation impossible — le compte de service n'a "
                      "probablement pas le droit d'écrire dans ce classeur. "
                      "Partagez-le en ÉDITEUR avec l'adresse du compte de "
                      "service.", 'erreur')
            flash("Pour un diagnostic complet, lancez dans le dossier du "
                  "projet : python diagnostic_google.py", 'info')
        return redirect(request.referrer or url_for('dashboard'))

    # ======================================================================
    # UTILISATEURS
    #
    # Le cabinet crée tous les comptes, tous rôles confondus : c'est lui qui
    # installe l'application chez le client. Le gérant d'une entreprise peut
    # en outre créer lui-même des comptes comptable et standard, mais
    # uniquement dans sa propre entreprise.
    # ======================================================================

    def _roles_creables():
        if current_user.est_cabinet:
            return Role.CHOIX
        if current_user.est_gerant:
            return [Role.COMPTABLE, Role.STANDARD]
        return []

    def _utilisateurs_visibles():
        if current_user.est_cabinet:
            return User.query.order_by(User.role, User.nom).all()
        return (User.query.filter_by(entreprise_id=current_user.entreprise_id)
                .order_by(User.role, User.nom).all())

    def _verifier_gestion(utilisateur):
        """Le gérant ne gère que les comptes de sa propre entreprise."""
        if current_user.est_cabinet:
            return
        if (not current_user.est_gerant
                or utilisateur.entreprise_id != current_user.entreprise_id
                or utilisateur.role not in (Role.COMPTABLE, Role.STANDARD)):
            abort(403)

    @app.route('/utilisateurs', methods=['GET', 'POST'])
    @role_requis(Role.CABINET, Role.GERANT)
    def utilisateurs():
        creables = _roles_creables()

        if request.method == 'POST':
            email = request.form['email'].strip().lower()
            if User.query.filter_by(email=email).first():
                flash("Un compte existe déjà avec cet email.", 'erreur')
                return redirect(url_for('utilisateurs'))

            role = request.form['role']
            if role not in creables:
                abort(403)

            # Un rôle côté entreprise est toujours rattaché à une entreprise.
            if role in Role.COTE_ENTREPRISE:
                entreprise_id = (request.form.get('entreprise_id', type=int)
                                 if current_user.est_cabinet
                                 else current_user.entreprise_id)
                if not entreprise_id:
                    flash("Choisissez l'entreprise à laquelle rattacher ce compte.",
                          'erreur')
                    return redirect(url_for('utilisateurs'))
            else:
                entreprise_id = None

            utilisateur = User(
                nom=request.form['nom'].strip(),
                email=email,
                role=role,
                departement=(request.form.get('departement')
                             if role == Role.RESPONSABLE else None),
                entreprise_id=entreprise_id,
            )
            utilisateur.set_password(request.form['password'])

            if role == Role.CONSULTANT:
                for entreprise_id in request.form.getlist('entreprises_suivies', type=int):
                    entreprise = db.session.get(Entreprise, entreprise_id)
                    if entreprise:
                        utilisateur.entreprises_suivies.append(entreprise)

            db.session.add(utilisateur)
            Audit.journaliser(current_user, 'creation', 'User', None,
                              f"{utilisateur.email} ({role})")
            db.session.commit()
            flash(f"Compte créé pour {utilisateur.nom} — "
                  f"{Role.LABELS.get(role, role)}.", 'succes')
            return redirect(url_for('utilisateurs'))

        return render_template(
            'utilisateurs.html',
            utilisateurs=_utilisateurs_visibles(),
            roles_creables=creables,
            entreprises=(Entreprise.query.order_by(Entreprise.nom).all()
                         if current_user.est_cabinet else []))

    @app.route('/utilisateurs/<int:user_id>/basculer', methods=['POST'])
    @role_requis(Role.CABINET, Role.GERANT)
    def basculer_utilisateur(user_id):
        utilisateur = db.get_or_404(User, user_id)
        _verifier_gestion(utilisateur)
        if utilisateur.id == current_user.id:
            flash("Vous ne pouvez pas désactiver votre propre compte.", 'erreur')
            return redirect(url_for('utilisateurs'))
        utilisateur.actif = not utilisateur.actif
        Audit.journaliser(current_user, 'modification', 'User', utilisateur.id,
                          'Activation' if utilisateur.actif else 'Désactivation')
        db.session.commit()
        flash(f"Compte {'activé' if utilisateur.actif else 'désactivé'}.", 'succes')
        return redirect(url_for('utilisateurs'))

    @app.route('/utilisateurs/<int:user_id>/reinitialiser', methods=['POST'])
    @role_requis(Role.CABINET, Role.GERANT)
    def reinitialiser_mot_de_passe(user_id):
        utilisateur = db.get_or_404(User, user_id)
        _verifier_gestion(utilisateur)
        nouveau = request.form.get('nouveau_mot_de_passe', '')
        if len(nouveau) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", 'erreur')
        else:
            utilisateur.set_password(nouveau)
            Audit.journaliser(current_user, 'modification', 'MotDePasse',
                              utilisateur.id, 'Réinitialisation administrative')
            db.session.commit()
            flash(f"Mot de passe réinitialisé pour {utilisateur.nom}. "
                  "Communiquez-le lui et demandez-lui de le changer.", 'succes')
        return redirect(url_for('utilisateurs'))

    # ======================================================================
    # PARAMÈTRES DU CABINET (logo, coordonnées, signature)
    # ======================================================================

    def _enregistrer_image(fichier, prefixe):
        """Sauvegarde un logo ou une signature et retourne son chemin.

        Retourne None si aucun fichier n'a été transmis, et signale à
        l'utilisateur les formats refusés plutôt que de les ignorer.
        """
        if not fichier or not fichier.filename:
            return None
        extension = os.path.splitext(fichier.filename)[1].lower()
        if extension not in ('.png', '.jpg', '.jpeg', '.gif'):
            flash(f"Format d'image non accepté ({extension}). "
                  "Utilisez un fichier PNG ou JPG.", 'erreur')
            return None

        nom = secure_filename(f"{prefixe}{extension}")
        dossier = os.path.join(app.config['UPLOAD_FOLDER'], 'logos')
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, nom)
        fichier.save(chemin)
        return chemin.replace('\\', '/')

    @app.route('/parametres', methods=['GET', 'POST'])
    @administration_requise
    def parametres():
        """Identité du cabinet : logo, coordonnées et signature."""
        cabinet = parametres_cabinet()

        if request.method == 'POST':
            cabinet.raison_sociale = request.form['raison_sociale'].strip()
            cabinet.slogan = (request.form.get('slogan') or '').strip()
            cabinet.adresse = (request.form.get('adresse') or '').strip()
            cabinet.ville = (request.form.get('ville') or '').strip()
            cabinet.telephone = (request.form.get('telephone') or '').strip()
            cabinet.email = (request.form.get('email') or '').strip()
            cabinet.site_web = (request.form.get('site_web') or '').strip()
            cabinet.rccm = (request.form.get('rccm') or '').strip()
            cabinet.compte_contribuable = (
                request.form.get('compte_contribuable') or '').strip()
            cabinet.banque = (request.form.get('banque') or '').strip()
            cabinet.numero_compte = (request.form.get('numero_compte') or '').strip()
            cabinet.signataire = (request.form.get('signataire') or '').strip()
            cabinet.pied_legal = (request.form.get('pied_legal') or '').strip()

            # Préambule des rapports (couverture, présentation, habilitations…)
            cabinet.type_rapport = (request.form.get('type_rapport') or '').strip()
            cabinet.entete_pages = (request.form.get('entete_pages') or '').strip()
            cabinet.domaines_activite = request.form.get('domaines_activite') or ''
            cabinet.distinctions = request.form.get('distinctions') or ''
            cabinet.contacts_referent = request.form.get('contacts_referent') or ''
            cabinet.presentation = request.form.get('presentation') or ''
            cabinet.habilitations_fdfp = request.form.get('habilitations_fdfp') or ''
            cabinet.preambule_initialise = True

            logo = _enregistrer_image(request.files.get('logo'), 'cabinet_logo')
            if logo:
                cabinet.logo = logo
            logo_fdfp = _enregistrer_image(request.files.get('logo_fdfp'),
                                           'cabinet_fdfp')
            if logo_fdfp:
                cabinet.logo_fdfp = logo_fdfp
            signature = _enregistrer_image(request.files.get('signature'),
                                           'cabinet_signature')
            if signature:
                cabinet.signature = signature
            if request.form.get('retirer_logo'):
                cabinet.logo = None
            if request.form.get('retirer_signature'):
                cabinet.signature = None

            Audit.journaliser(current_user, 'modification', 'ParametresCabinet',
                              cabinet.id, cabinet.raison_sociale)
            db.session.commit()
            flash("Paramètres du cabinet enregistrés. Le logo apparaîtra sur "
                  "les rapports d'accompagnement.", 'succes')
            return redirect(url_for('parametres'))

        return render_template('parametres.html', cabinet=cabinet)

    @app.route('/image/<path:chemin>')
    @login_required
    def image_hebergee(chemin):
        """Sert un logo ou une signature stocké dans le dossier des envois.

        Le chemin est contraint au sous-dossier des logos : impossible de
        faire remonter la lecture ailleurs dans le système de fichiers.
        """
        dossier = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], 'logos'))
        cible = os.path.abspath(os.path.join(dossier, os.path.basename(chemin)))
        if not cible.startswith(dossier) or not os.path.exists(cible):
            abort(404)
        return send_file(cible)

    # ======================================================================
    # HISTORIQUE (audit)
    # ======================================================================

    @app.route('/historique')
    @administration_requise
    def historique():
        page = request.args.get('page', 1, type=int)
        entrees = (Audit.query.order_by(Audit.date_action.desc())
                   .paginate(page=page, per_page=50, error_out=False))
        return render_template('historique.html', entrees=entrees)

    # ==================================================================
    # POSIGES V1 — MODULES COMMERCIAUX
    # ==================================================================

    def perimetre_entreprise():
        """Entreprise sur laquelle porte l'écran commercial courant.

        Un client accompagné est toujours ramené à la sienne. Le cabinet
        choisit via le sélecteur d'entreprise du menu ; s'il n'a qu'une
        seule entreprise accessible, elle est sélectionnée d'office.
        """
        if current_user.est_client:
            return current_user.entreprise

        entreprise_id = request.args.get('entreprise_id', type=int)
        if entreprise_id:
            if not current_user.peut_voir_entreprise(entreprise_id):
                abort(403)
            return db.session.get(Entreprise, entreprise_id)

        accessibles = current_user.entreprises_accessibles()
        if len(accessibles) == 1:
            return accessibles[0]
        return None

    import routes_commerciales
    routes_commerciales.enregistrer_routes(app, {
        'perimetre_entreprise': perimetre_entreprise,
        'cabinet_requis': cabinet_requis,
        'interne_requis': interne_requis,
        'administration_requise': administration_requise,
        'analyse_requise': analyse_requise,
        'enregistrer_image': _enregistrer_image,
    })

    return app


# ==========================================================================
# Initialisation
# ==========================================================================

app = creer_app()

if (app.config.get('COOKIE_SECURE')
        and app.config.get('SECRET_KEY') == 'dev-secret-a-changer-en-production'):
    import logging
    logging.getLogger(__name__).critical(
        "COOKIE_SECURE=true (configuration de production) mais SECRET_KEY "
        "n'a pas été définie : les sessions utilisent la clé par défaut, "
        "publique dans le code source. Définissez la variable d'environnement "
        "SECRET_KEY avant de déployer.")


def initialiser_base(application=None):
    """Crée les tables, met la base à niveau et crée le compte initial.

    Idempotent et sûr en cas d'appels concurrents : en production, plusieurs
    workers gunicorn démarrent en même temps et exécutent tous cette fonction.
    Un seul crée le compte ; les autres constatent qu'il existe déjà.
    """
    import time

    from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

    application = application or app
    with application.app_context():
        # Création des tables. Si plusieurs workers démarrent ensemble, l'un
        # d'eux peut trouver une table déjà créée par un autre : on réessaie
        # brièvement plutôt que d'échouer.
        for tentative in range(3):
            try:
                db.create_all()
                break
            except (OperationalError, ProgrammingError):
                db.session.rollback()
                if tentative == 2:
                    break
                time.sleep(0.4)

        # Mise à niveau d'une base créée par une version antérieure :
        # nouvelles colonnes, reprise des rôles et des catégories.
        migrations.appliquer(application.config.get('NOM_CABINET'), verbeux=True)

        if User.query.count() > 0:
            return False

        cabinet = User(nom='Direction AK World', email='cabinet@akworld.com',
                       role=Role.CABINET)
        cabinet.set_password('AkWorld2026!')
        db.session.add(cabinet)
        try:
            db.session.commit()
        except IntegrityError:
            # Un autre worker a créé le compte entre-temps : situation normale.
            db.session.rollback()
            return False

        print("=" * 62)
        print("  Compte cabinet créé")
        print("  Email        : cabinet@akworld.com")
        print("  Mot de passe : AkWorld2026!")
        print("  >>> Changez ce mot de passe dès la première connexion <<<")
        print("=" * 62)
        return True


# Initialisation automatique au démarrage — indispensable sous gunicorn,
# qui n'exécute jamais le bloc __main__ ci-dessous.
if app.config.get('AUTO_INIT'):
    try:
        initialiser_base()
    except Exception as _erreur:      # pragma: no cover
        # Ne jamais empêcher le démarrage de l'application.
        import logging
        logging.getLogger(__name__).warning(
            "Initialisation automatique reportée : %s", _erreur)


if __name__ == '__main__':
    # Le débogueur interactif Werkzeug (activé par debug=True) permet
    # d'exécuter du code arbitraire depuis le navigateur : à n'activer que
    # sur un poste de développement, jamais quand le serveur est joignable
    # depuis l'extérieur. En production, utilisez toujours gunicorn (voir
    # Procfile) plutôt que ce point d'entrée.
    debogage = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(debug=debogage, host='127.0.0.1' if debogage else '0.0.0.0', port=5000)
