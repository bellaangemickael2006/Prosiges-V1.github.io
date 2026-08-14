"""
Posiges V1 — routes de la gestion commerciale.

Regroupe les écrans ajoutés à l'application : catalogue, ventes,
fiches client et fournisseur, stock et rapports.

Ces routes sont enregistrées sur l'application principale par
`enregistrer_routes(app, aides)`, où `aides` fournit les fonctions
d'autorisation et de périmètre définies dans app.py. Ce découpage évite
de faire enfler app.py au-delà du raisonnable.
"""
import json
from datetime import date, datetime

from flask import (abort, flash, redirect, render_template, request, send_file,
                   url_for)
from flask_login import current_user, login_required

import comptes
import documents_pdf
import gestion_commerciale as gc
from models import (Article, Audit, CATEGORIES_ARTICLES, Client,
                    ConditionVente, Document, Entreprise, FamilleProduit,
                    Fournisseur, ModePaiement, MouvementStock, NatureArticle,
                    NatureCharge, Operation, Rapport, Role, SectionRapport,
                    StatutDocument, TypeDocument, TypeMouvement, TypeOperation,
                    UNITES, db)


def enregistrer_routes(app, aides):
    """Attache toutes les routes commerciales à l'application."""

    perimetre_entreprise = aides['perimetre_entreprise']
    cabinet_requis = aides['cabinet_requis']
    interne_requis = aides['interne_requis']
    administration_requise = aides['administration_requise']
    analyse_requise = aides['analyse_requise']
    enregistrer_image = aides['enregistrer_image']

    # ==================================================================
    # Contexte commun aux templates
    # ==================================================================

    @app.context_processor
    def injecter_commercial():
        contexte = {
            'CATEGORIES_ARTICLES': CATEGORIES_ARTICLES,
            'UNITES': UNITES,
            'LABELS_NATURE': NatureArticle.LABELS,
            'LABELS_TYPEDOC': TypeDocument.LABELS,
            'LABELS_STATUT': StatutDocument.LABELS,
            'LABELS_CONDITION': ConditionVente.LABELS,
            'LABELS_MOUVEMENT': TypeMouvement.LABELS,
            'LABELS_NATURE_CHARGE': NatureCharge.LABELS,
        }
        # Sélecteur d'entreprise dans le menu (remplace l'onglet dédié)
        if current_user.is_authenticated and not current_user.est_client:
            contexte['entreprises_menu'] = current_user.entreprises_accessibles()
            contexte['entreprise_active'] = request.args.get('entreprise_id', type=int)
        return contexte

    def _entreprise_courante(obligatoire=True):
        """Entreprise sur laquelle porte l'écran commercial.

        Un client est toujours ramené à la sienne. Le cabinet choisit via
        le sélecteur du menu.
        """
        entreprise = perimetre_entreprise()
        if entreprise is None and obligatoire:
            flash("Choisissez d'abord une entreprise dans le menu.", 'info')
            return None
        return entreprise

    def _args(entreprise):
        return {'entreprise_id': entreprise.id} if entreprise else {}

    def _peut_modifier(entreprise):
        """Qui peut créer ou modifier des données commerciales."""
        if current_user.est_cabinet:
            return True
        if current_user.est_client:
            return entreprise and entreprise.id == current_user.entreprise_id
        if current_user.est_consultant:
            return entreprise and current_user.peut_voir_entreprise(entreprise.id)
        return False

    def _verifier_acces(entreprise):
        if entreprise is None:
            abort(400)
        if not current_user.peut_voir_entreprise(entreprise.id):
            abort(403)

    # ==================================================================
    # 1. CATALOGUE — PRODUITS ET SERVICES
    # ==================================================================

    @app.route('/catalogue')
    @login_required
    def catalogue():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return render_template('choisir_entreprise.html',
                                   titre="Produits et services")
        _verifier_acces(entreprise)

        q = Article.query.filter_by(entreprise_id=entreprise.id)
        nature = request.args.get('nature')
        recherche = (request.args.get('q') or '').strip()
        if nature in NatureArticle.CHOIX:
            q = q.filter_by(nature=nature)
        if recherche:
            motif = f'%{recherche}%'
            q = q.filter(db.or_(Article.designation.ilike(motif),
                                Article.reference.ilike(motif),
                                Article.categorie.ilike(motif)))

        articles = q.order_by(Article.designation).all()
        produits = [a for a in articles if a.nature == NatureArticle.PRODUIT]

        return render_template(
            'catalogue.html',
            entreprise=entreprise,
            articles=articles,
            alertes=gc.articles_en_alerte(entreprise.id),
            valeur_stock=gc.valeur_totale_stock(entreprise.id),
            nombre_produits=len(produits),
            nombre_services=len(articles) - len(produits),
            filtre_nature=nature,
            recherche=recherche,
            peut_modifier=_peut_modifier(entreprise),
            scope_args=_args(entreprise),
        )

    @app.route('/catalogue/nouveau', methods=['GET', 'POST'])
    @login_required
    def nouvel_article():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return redirect(url_for('catalogue'))
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        if request.method == 'POST':
            nature = request.form.get('nature', NatureArticle.PRODUIT)
            article = Article(
                reference=(request.form.get('reference') or '').strip(),
                designation=request.form['designation'].strip(),
                nature=nature,
                categorie=(request.form.get('categorie') or '').strip(),
                famille_id=request.form.get('famille_id', type=int),
                description=(request.form.get('description') or '').strip(),
                prix_vente=float(request.form.get('prix_vente') or 0),
                prix_achat=float(request.form.get('prix_achat') or 0),
                unite=request.form.get('unite') or 'pièce',
                entreprise_id=entreprise.id,
            )
            if nature == NatureArticle.PRODUIT:
                article.quantite_stock = float(request.form.get('quantite_stock') or 0)
                article.seuil_alerte = float(request.form.get('seuil_alerte') or 0)

            db.session.add(article)
            db.session.flush()

            if article.suit_stock and article.quantite_stock:
                gc.enregistrer_mouvement(
                    article, TypeMouvement.ENTREE, article.quantite_stock,
                    utilisateur=current_user, motif='Stock initial')
                article.quantite_stock = float(request.form.get('quantite_stock') or 0)

            Audit.journaliser(current_user, 'creation', 'Article',
                              article.id, article.designation)
            db.session.commit()
            flash(f"« {article.designation} » ajouté au catalogue.", 'succes')

            if request.form.get('continuer'):
                return redirect(url_for('nouvel_article', **_args(entreprise)))
            return redirect(url_for('catalogue', **_args(entreprise)))

        return render_template('article_form.html', article=None,
                               entreprise=entreprise,
                               familles=gc.familles_de(entreprise.id),
                               scope_args=_args(entreprise))

    @app.route('/catalogue/<int:article_id>/modifier', methods=['GET', 'POST'])
    @login_required
    def modifier_article(article_id):
        article = db.get_or_404(Article, article_id)
        entreprise = db.session.get(Entreprise, article.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        if request.method == 'POST':
            article.reference = (request.form.get('reference') or '').strip()
            article.designation = request.form['designation'].strip()
            article.nature = request.form.get('nature', article.nature)
            article.categorie = (request.form.get('categorie') or '').strip()
            article.famille_id = request.form.get('famille_id', type=int)
            article.description = (request.form.get('description') or '').strip()
            article.prix_vente = float(request.form.get('prix_vente') or 0)
            article.prix_achat = float(request.form.get('prix_achat') or 0)
            article.unite = request.form.get('unite') or 'pièce'
            article.actif = bool(request.form.get('actif'))
            if article.suit_stock:
                article.seuil_alerte = float(request.form.get('seuil_alerte') or 0)

            Audit.journaliser(current_user, 'modification', 'Article',
                              article.id, article.designation)
            db.session.commit()
            flash("Article mis à jour.", 'succes')
            return redirect(url_for('catalogue', **_args(entreprise)))

        return render_template('article_form.html', article=article,
                               entreprise=entreprise,
                               familles=gc.familles_de(entreprise.id),
                               scope_args=_args(entreprise))

    @app.route('/catalogue/<int:article_id>/supprimer', methods=['POST'])
    @login_required
    def supprimer_article(article_id):
        article = db.get_or_404(Article, article_id)
        entreprise = db.session.get(Entreprise, article.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        Audit.journaliser(current_user, 'suppression', 'Article',
                          article.id, article.designation)
        # Désactivation plutôt que suppression : les documents émis
        # continuent de référencer l'article.
        article.actif = False
        db.session.commit()
        flash(f"« {article.designation} » retiré du catalogue.", 'succes')
        return redirect(url_for('catalogue', **_args(entreprise)))

    # ==================================================================
    # 1 bis. FAMILLES DE PRODUITS
    #
    # Une famille regroupe des articles de même nature — « Canins »,
    # « Volaille » — et devient une ligne de chiffre d'affaires du compte
    # d'exploitation. C'est ce qui permet de sortir le chiffre d'affaires
    # par famille plutôt qu'un total global sans relief.
    # ==================================================================

    @app.route('/familles', methods=['GET', 'POST'])
    @login_required
    def familles():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return render_template('choisir_entreprise.html',
                                   titre='Familles de produits')
        _verifier_acces(entreprise)

        if request.method == 'POST':
            if not _peut_modifier(entreprise):
                abort(403)
            nom = (request.form.get('nom') or '').strip()
            if not nom:
                flash("Donnez un nom à la famille.", 'erreur')
                return redirect(url_for('familles', **_args(entreprise)))

            famille = FamilleProduit(
                nom=nom,
                description=(request.form.get('description') or '').strip(),
                ordre=request.form.get('ordre', type=int) or 0,
                entreprise_id=entreprise.id)
            db.session.add(famille)
            Audit.journaliser(current_user, 'creation', 'FamilleProduit',
                              None, nom)
            db.session.commit()
            flash(f"Famille « {nom} » créée. Elle apparaîtra comme ligne de "
                  f"chiffre d'affaires dans le compte d'exploitation.", 'succes')
            return redirect(url_for('familles', **_args(entreprise)))

        liste = gc.familles_de(entreprise.id)
        return render_template(
            'familles.html', entreprise=entreprise, familles=liste,
            sans_famille=Article.query.filter_by(entreprise_id=entreprise.id,
                                                 famille_id=None).count(),
            peut_modifier=_peut_modifier(entreprise),
            scope_args=_args(entreprise))

    @app.route('/familles/<int:famille_id>/modifier', methods=['POST'])
    @login_required
    def modifier_famille(famille_id):
        famille = db.get_or_404(FamilleProduit, famille_id)
        entreprise = db.session.get(Entreprise, famille.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        famille.nom = (request.form.get('nom') or famille.nom).strip()
        famille.description = (request.form.get('description') or '').strip()
        famille.ordre = request.form.get('ordre', type=int) or 0
        Audit.journaliser(current_user, 'modification', 'FamilleProduit',
                          famille.id, famille.nom)
        db.session.commit()
        flash("Famille mise à jour.", 'succes')
        return redirect(url_for('familles', **_args(entreprise)))

    @app.route('/familles/<int:famille_id>/supprimer', methods=['POST'])
    @login_required
    def supprimer_famille(famille_id):
        famille = db.get_or_404(FamilleProduit, famille_id)
        entreprise = db.session.get(Entreprise, famille.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        # Les articles rattachés ne sont pas supprimés : ils repassent en
        # chiffre d'affaires non ventilé, et rien ne disparaît des comptes.
        Article.query.filter_by(famille_id=famille.id).update({'famille_id': None})
        Audit.journaliser(current_user, 'suppression', 'FamilleProduit',
                          famille.id, famille.nom)
        db.session.delete(famille)
        db.session.commit()
        flash(f"Famille « {famille.nom} » supprimée. Les articles concernés "
              f"restent au catalogue, sans famille.", 'succes')
        return redirect(url_for('familles', **_args(entreprise)))

    # ==================================================================
    # 2. GESTION DES VENTES — FACTURE, PROFORMA ET REÇU
    # ==================================================================

    @app.route('/ventes')
    @login_required
    def ventes():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return render_template('choisir_entreprise.html',
                                   titre="Gestion des ventes")
        _verifier_acces(entreprise)

        q = Document.query.filter_by(entreprise_id=entreprise.id)
        type_doc = request.args.get('type')
        statut = request.args.get('statut')
        recherche = (request.args.get('q') or '').strip()

        if type_doc in TypeDocument.CHOIX:
            q = q.filter_by(type_document=type_doc)
        if statut in StatutDocument.CHOIX:
            q = q.filter_by(statut=statut)
        if recherche:
            motif = f'%{recherche}%'
            q = q.filter(db.or_(Document.numero.ilike(motif),
                                Document.note.ilike(motif)))

        documents = q.order_by(Document.date_document.desc(),
                               Document.id.desc()).limit(300).all()

        return render_template(
            'ventes.html',
            entreprise=entreprise,
            documents=documents,
            synthese=gc.synthese_ventes(entreprise.id),
            filtre_type=type_doc,
            filtre_statut=statut,
            recherche=recherche,
            peut_modifier=_peut_modifier(entreprise),
            scope_args=_args(entreprise),
        )

    @app.route('/ventes/nouveau', methods=['GET', 'POST'])
    @login_required
    def nouveau_document():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return redirect(url_for('ventes'))
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        profil = gc.profil_de(entreprise)

        if request.method == 'POST':
            type_document = request.form.get('type_document', TypeDocument.PROFORMA)

            # Client : reconnu par son nom, créé s'il est inconnu
            nom_client = (request.form.get('client_nom') or '').strip()
            client = None
            if nom_client:
                client = Client.query.filter(
                    Client.nom.ilike(nom_client),
                    Client.entreprise_id == entreprise.id).first()
                if client is None:
                    client = Client(
                        nom=nom_client,
                        contact=(request.form.get('client_contact') or '').strip(),
                        email=(request.form.get('client_email') or '').strip(),
                        localisation=(request.form.get('client_localisation') or '').strip(),
                        entreprise_id=entreprise.id)
                    db.session.add(client)
                    db.session.flush()

            # Lignes du document
            lignes = []
            designations = request.form.getlist('designation')
            quantites = request.form.getlist('quantite')
            unites = request.form.getlist('unite')
            prix = request.form.getlist('prix_unitaire')
            articles = request.form.getlist('article_id')

            payes = request.form.getlist('ligne_montant_paye')
            remises = request.form.getlist('ligne_remise')

            for index, designation in enumerate(designations):
                if not designation.strip():
                    continue
                article_id = None
                if index < len(articles) and articles[index]:
                    try:
                        article_id = int(articles[index])
                    except ValueError:
                        article_id = None
                lignes.append({
                    'article_id': article_id,
                    'designation': designation.strip(),
                    'quantite': quantites[index] if index < len(quantites) else 1,
                    'unite': unites[index] if index < len(unites) else 'pièce',
                    'prix_unitaire': prix[index] if index < len(prix) else 0,
                    'montant_paye': payes[index] if index < len(payes) else None,
                    'remise': str(index) in remises,
                })

            if not lignes:
                flash("Ajoutez au moins une ligne au document.", 'erreur')
                return redirect(url_for('nouveau_document', **_args(entreprise)))

            condition = request.form.get('condition_vente', ConditionVente.COMPTANT)
            montant_paye = request.form.get('montant_paye')

            document = gc.creer_document(
                type_document, entreprise, client, lignes, current_user,
                condition=condition,
                montant_paye=float(montant_paye) if montant_paye else None,
                mode_paiement=request.form.get('mode_paiement'),
                reference_paiement=(request.form.get('reference_paiement') or '').strip(),
                date_document=datetime.strptime(
                    request.form['date_document'], '%Y-%m-%d').date(),
                date_echeance=(datetime.strptime(request.form['date_echeance'],
                                                 '%Y-%m-%d').date()
                               if request.form.get('date_echeance') else None),
                note=(request.form.get('note') or '').strip(),
                numero_commande=(request.form.get('numero_commande') or '').strip()
                                or None,
                categorie_transaction=(
                    request.form.get('categorie_transaction') or '').strip() or None,
            )

            # Enchaînements automatiques : comptabilité puis stock
            messages = [f"{TypeDocument.LABELS[type_document]} {document.numero} créé"]
            if type_document in (TypeDocument.FACTURE, TypeDocument.RECU):
                if gc.synchroniser_comptabilite(document, current_user):
                    messages.append("écritures portées au journal")
                nombre, insuffisances = gc.deduire_stock_document(document,
                                                                  current_user)
                if nombre:
                    messages.append(f"stock mis à jour ({nombre} article(s))")
                for manque in insuffisances:
                    flash(
                        f"Stock insuffisant pour « {manque['article'].designation} » : "
                        f"{manque['demandee']:g} vendus pour {manque['disponible']:g} "
                        f"en stock. Le document est enregistré, mais pensez à "
                        f"régulariser depuis l'onglet Stock.", 'erreur')

            Audit.journaliser(current_user, 'creation', 'Document',
                              document.id, f"{document.numero} — "
                                           f"{document.montant_ttc:.0f} FCFA")
            db.session.commit()
            flash(' — '.join(messages) + '.', 'succes')
            return redirect(url_for('voir_document', document_id=document.id))

        articles = (Article.query
                    .filter_by(entreprise_id=entreprise.id, actif=True)
                    .order_by(Article.designation).all())
        clients = (Client.query.filter_by(entreprise_id=entreprise.id)
                   .order_by(Client.nom).all())

        catalogue_json = json.dumps([{
            'id': a.id, 'designation': a.designation,
            'prix': a.prix_vente or 0, 'unite': a.unite or 'pièce',
            'nature': a.nature, 'stock': a.quantite_stock or 0,
            'famille': a.famille.nom if a.famille else '',
        } for a in articles], ensure_ascii=False)

        type_defaut = request.args.get('type', TypeDocument.FACTURE)
        return render_template(
            'document_form.html',
            entreprise=entreprise, profil=profil,
            articles=articles, clients=clients,
            catalogue_json=catalogue_json,
            aujourdhui=date.today().isoformat(),
            numero_commande=gc.prochain_numero_commande(entreprise.id),
            type_defaut=(type_defaut if type_defaut in TypeDocument.CHOIX
                         else TypeDocument.FACTURE),
            scope_args=_args(entreprise),
        )

    @app.route('/ventes/<int:document_id>')
    @login_required
    def voir_document(document_id):
        document = db.get_or_404(Document, document_id)
        entreprise = db.session.get(Entreprise, document.entreprise_id)
        _verifier_acces(entreprise)

        return render_template(
            'document_detail.html',
            document=document, entreprise=entreprise,
            profil=gc.profil_de(entreprise),
            peut_modifier=_peut_modifier(entreprise),
            scope_args=_args(entreprise),
        )

    @app.route('/ventes/<int:document_id>/pdf')
    @login_required
    def telecharger_document(document_id):
        document = db.get_or_404(Document, document_id)
        entreprise = db.session.get(Entreprise, document.entreprise_id)
        _verifier_acces(entreprise)

        flux = documents_pdf.generer_document_pdf(
            document, gc.profil_de(entreprise))
        nom = f"{document.numero}.pdf"
        Audit.journaliser(current_user, 'export', 'Document', document.id, nom)
        db.session.commit()
        return send_file(flux, as_attachment=True, download_name=nom,
                         mimetype='application/pdf')

    @app.route('/ventes/<int:document_id>/encaisser', methods=['POST'])
    @login_required
    def encaisser_document(document_id):
        document = db.get_or_404(Document, document_id)
        entreprise = db.session.get(Entreprise, document.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        montant = float(request.form.get('montant') or 0)
        if montant <= 0:
            flash("Indiquez un montant supérieur à zéro.", 'erreur')
            return redirect(url_for('voir_document', document_id=document.id))

        gc.enregistrer_encaissement(
            document, montant,
            mode_paiement=request.form.get('mode_paiement'),
            reference=(request.form.get('reference') or '').strip(),
            utilisateur=current_user)

        Audit.journaliser(current_user, 'modification', 'Document',
                          document.id, f"Encaissement {montant:.0f} FCFA")
        db.session.commit()
        flash(f"Encaissement de {montant:,.0f} FCFA enregistré.".replace(',', ' '),
              'succes')
        return redirect(url_for('voir_document', document_id=document.id))

    @app.route('/ventes/<int:document_id>/convertir', methods=['POST'])
    @login_required
    def convertir_document(document_id):
        """Transforme une proforma acceptée en reçu, sans ressaisie."""
        proforma = db.get_or_404(Document, document_id)
        entreprise = db.session.get(Entreprise, proforma.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)
        if proforma.type_document != TypeDocument.PROFORMA:
            abort(400)

        lignes = [{
            'article_id': l.article_id, 'designation': l.designation,
            'quantite': l.quantite, 'unite': l.unite,
            'prix_unitaire': l.prix_unitaire,
        } for l in proforma.lignes]

        recu = gc.creer_document(
            TypeDocument.RECU, entreprise, proforma.client, lignes, current_user,
            condition=request.form.get('condition_vente', ConditionVente.COMPTANT),
            montant_paye=float(request.form.get('montant_paye') or 0) or None,
            mode_paiement=request.form.get('mode_paiement'),
            note=f"Suite à la proforma {proforma.numero}")

        gc.synchroniser_comptabilite(recu, current_user)
        _, insuffisances = gc.deduire_stock_document(recu, current_user)
        for manque in insuffisances:
            flash(f"Stock insuffisant pour « {manque['article'].designation} » : "
                  f"pensez à régulariser depuis l'onglet Stock.", 'erreur')

        proforma.statut = StatutDocument.REGLE
        Audit.journaliser(current_user, 'creation', 'Document', recu.id,
                          f"{recu.numero} depuis {proforma.numero}")
        db.session.commit()
        flash(f"Reçu {recu.numero} établi à partir de la proforma "
              f"{proforma.numero}.", 'succes')
        return redirect(url_for('voir_document', document_id=recu.id))

    @app.route('/ventes/<int:document_id>/annuler', methods=['POST'])
    @login_required
    def annuler_doc(document_id):
        document = db.get_or_404(Document, document_id)
        entreprise = db.session.get(Entreprise, document.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        gc.annuler_document(document, current_user)
        Audit.journaliser(current_user, 'modification', 'Document',
                          document.id, f"Annulation de {document.numero}")
        db.session.commit()
        flash(f"{document.numero} annulé : écriture comptable et mouvements "
              "de stock repris.", 'succes')
        return redirect(url_for('ventes', **_args(entreprise)))

    # ==================================================================
    # 3. FICHE CLIENT
    # ==================================================================

    @app.route('/clients/<int:client_id>')
    @login_required
    def fiche_client(client_id):
        client = db.get_or_404(Client, client_id)
        if client.entreprise_id:
            entreprise = db.session.get(Entreprise, client.entreprise_id)
            _verifier_acces(entreprise)
        else:
            if not current_user.est_cabinet:
                abort(403)
            entreprise = None

        return render_template(
            'fiche_client.html',
            fiche=gc.fiche_client(client),
            entreprise=entreprise,
            peut_modifier=_peut_modifier(entreprise) if entreprise else current_user.est_cabinet,
            scope_args=_args(entreprise),
        )

    @app.route('/clients/<int:client_id>/modifier', methods=['POST'])
    @login_required
    def modifier_client(client_id):
        client = db.get_or_404(Client, client_id)
        if client.entreprise_id:
            entreprise = db.session.get(Entreprise, client.entreprise_id)
            _verifier_acces(entreprise)
            if not _peut_modifier(entreprise):
                abort(403)
        elif not current_user.est_cabinet:
            abort(403)

        client.nom = request.form['nom'].strip()
        client.contact = (request.form.get('contact') or '').strip()
        client.localisation = (request.form.get('localisation') or '').strip()
        Audit.journaliser(current_user, 'modification', 'Client',
                          client.id, client.nom)
        db.session.commit()
        flash("Fiche client mise à jour.", 'succes')
        return redirect(url_for('fiche_client', client_id=client.id))

    # ==================================================================
    # 4. FOURNISSEURS
    # ==================================================================

    @app.route('/fournisseurs', methods=['GET', 'POST'])
    @login_required
    def fournisseurs():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return render_template('choisir_entreprise.html',
                                   titre="Fournisseurs")
        _verifier_acces(entreprise)

        if request.method == 'POST':
            if not _peut_modifier(entreprise):
                abort(403)
            fournisseur = Fournisseur(
                nom=request.form['nom'].strip(),
                contact=(request.form.get('contact') or '').strip(),
                email=(request.form.get('email') or '').strip(),
                adresse=(request.form.get('adresse') or '').strip(),
                localisation=(request.form.get('localisation') or '').strip(),
                specialite=(request.form.get('specialite') or '').strip(),
                entreprise_id=entreprise.id,
            )
            db.session.add(fournisseur)
            Audit.journaliser(current_user, 'creation', 'Fournisseur',
                              None, fournisseur.nom)
            db.session.commit()
            flash(f"Fournisseur « {fournisseur.nom} » ajouté.", 'succes')
            return redirect(url_for('fournisseurs', **_args(entreprise)))

        liste = (Fournisseur.query.filter_by(entreprise_id=entreprise.id)
                 .order_by(Fournisseur.nom).all())
        return render_template('fournisseurs.html',
                               entreprise=entreprise, fournisseurs=liste,
                               peut_modifier=_peut_modifier(entreprise),
                               scope_args=_args(entreprise))

    @app.route('/fournisseurs/<int:fournisseur_id>')
    @login_required
    def fiche_fournisseur(fournisseur_id):
        fournisseur = db.get_or_404(Fournisseur, fournisseur_id)
        entreprise = db.session.get(Entreprise, fournisseur.entreprise_id)
        _verifier_acces(entreprise)

        return render_template('fiche_fournisseur.html',
                               fiche=gc.fiche_fournisseur(fournisseur),
                               entreprise=entreprise,
                               peut_modifier=_peut_modifier(entreprise),
                               scope_args=_args(entreprise))

    @app.route('/fournisseurs/<int:fournisseur_id>/modifier', methods=['POST'])
    @login_required
    def modifier_fournisseur(fournisseur_id):
        fournisseur = db.get_or_404(Fournisseur, fournisseur_id)
        entreprise = db.session.get(Entreprise, fournisseur.entreprise_id)
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        fournisseur.nom = request.form['nom'].strip()
        fournisseur.contact = (request.form.get('contact') or '').strip()
        fournisseur.email = (request.form.get('email') or '').strip()
        fournisseur.adresse = (request.form.get('adresse') or '').strip()
        fournisseur.localisation = (request.form.get('localisation') or '').strip()
        fournisseur.specialite = (request.form.get('specialite') or '').strip()
        Audit.journaliser(current_user, 'modification', 'Fournisseur',
                          fournisseur.id, fournisseur.nom)
        db.session.commit()
        flash("Fiche fournisseur mise à jour.", 'succes')
        return redirect(url_for('fiche_fournisseur', fournisseur_id=fournisseur.id))

    # ==================================================================
    # 5. STOCK
    # ==================================================================

    @app.route('/stock')
    @login_required
    def stock():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return render_template('choisir_entreprise.html', titre="Stock")
        _verifier_acces(entreprise)

        articles = (Article.query
                    .filter_by(entreprise_id=entreprise.id,
                               nature=NatureArticle.PRODUIT, actif=True)
                    .order_by(Article.designation).all())
        mouvements = (MouvementStock.query
                      .filter_by(entreprise_id=entreprise.id)
                      .order_by(MouvementStock.date_mouvement.desc(),
                                MouvementStock.id.desc()).limit(100).all())

        return render_template(
            'stock.html',
            entreprise=entreprise,
            articles=articles,
            mouvements=mouvements,
            alertes=[a for a in articles if a.en_alerte],
            ruptures=[a for a in articles if a.en_rupture],
            valeur_stock=gc.valeur_totale_stock(entreprise.id),
            fournisseurs=Fournisseur.query.filter_by(
                entreprise_id=entreprise.id).order_by(Fournisseur.nom).all(),
            peut_modifier=_peut_modifier(entreprise),
            aujourdhui=date.today().isoformat(),
            scope_args=_args(entreprise),
        )

    @app.route('/stock/mouvement', methods=['POST'])
    @login_required
    def nouveau_mouvement():
        entreprise = _entreprise_courante()
        _verifier_acces(entreprise)
        if not _peut_modifier(entreprise):
            abort(403)

        article = db.get_or_404(Article, request.form.get('article_id', type=int))
        if article.entreprise_id != entreprise.id:
            abort(403)

        type_mouvement = request.form.get('type_mouvement', TypeMouvement.ENTREE)
        quantite = float(request.form.get('quantite') or 0)
        if quantite <= 0:
            flash("Indiquez une quantité supérieure à zéro.", 'erreur')
            return redirect(url_for('stock', **_args(entreprise)))

        fournisseur_id = request.form.get('fournisseur_id', type=int)
        gc.enregistrer_mouvement(
            article, type_mouvement, quantite, utilisateur=current_user,
            motif=(request.form.get('motif') or '').strip(),
            fournisseur_id=fournisseur_id,
            date_mouvement=datetime.strptime(
                request.form['date_mouvement'], '%Y-%m-%d').date()
            if request.form.get('date_mouvement') else None)

        # Une entrée de stock peut être comptabilisée comme un achat
        montant_achat = float(request.form.get('montant_achat') or 0)
        if type_mouvement == TypeMouvement.ENTREE and montant_achat > 0:
            fournisseur = (db.session.get(Fournisseur, fournisseur_id)
                           if fournisseur_id else None)
            libelle = f"Achat {article.designation}"
            if fournisseur:
                libelle += f" — {fournisseur.nom}"
            operation = Operation(
                date_operation=date.today(),
                type_operation=TypeOperation.ACHAT,
                categorie='Achat marchandises',
                libelle=libelle,
                nature_charge=NatureCharge.VARIABLE,
                entreprise_id=entreprise.id,
                montant=montant_achat,
                mode_paiement=request.form.get('mode_paiement') or ModePaiement.CASH,
                reference=f"STK-{article.id}",
                numero_facture=f"STK-{date.today().year}-{article.id}-"
                               f"{MouvementStock.query.count() + 1:05d}",
                cree_par_id=current_user.id)
            db.session.add(operation)
            db.session.commit()
            flash(f"Entrée de {quantite:g} {article.unite} enregistrée et "
                  f"achat porté au journal.", 'succes')
        else:
            flash(f"Mouvement enregistré : stock de « {article.designation} » "
                  f"à {article.quantite_stock:g} {article.unite}.", 'succes')

        if article.en_alerte:
            flash(f"Attention : « {article.designation} » est au seuil d'alerte "
                  f"({article.quantite_stock:g} restant).", 'erreur')

        Audit.journaliser(current_user, 'creation', 'MouvementStock',
                          article.id, f"{type_mouvement} {quantite:g}")
        db.session.commit()
        return redirect(url_for('stock', **_args(entreprise)))

    # ==================================================================
    # 6. RAPPORTS D'ACCOMPAGNEMENT
    # ==================================================================

    @app.route('/rapports-cabinet')
    @login_required
    def rapports_cabinet():
        if current_user.est_client:
            entreprise = current_user.entreprise
        else:
            entreprise = _entreprise_courante(obligatoire=False)

        liste = gc.rapports_visibles(
            current_user, entreprise.id if entreprise else None)

        return render_template(
            'rapports_cabinet.html',
            rapports=liste, entreprise=entreprise,
            peut_rediger=current_user.peut_rediger_rapport,
            annee_defaut=date.today().year,
            scope_args=_args(entreprise),
        )

    @app.route('/rapports-cabinet/nouveau', methods=['POST'])
    @cabinet_requis
    def nouveau_rapport():
        entreprise = _entreprise_courante()
        if entreprise is None:
            flash("Choisissez une entreprise avant de rédiger un rapport.", 'erreur')
            return redirect(url_for('rapports_cabinet'))
        _verifier_acces(entreprise)

        annee = request.form.get('annee', type=int) or date.today().year
        rapport = gc.creer_rapport(entreprise, annee, current_user,
                                   titre=(request.form.get('titre') or '').strip() or None,
                                   periode=(request.form.get('periode') or '').strip() or None)

        Audit.journaliser(current_user, 'creation', 'Rapport',
                          rapport.id, rapport.titre)
        db.session.commit()
        flash("Rapport créé et pré-rempli avec les chiffres de la période. "
              "Complétez l'analyse et les recommandations.", 'succes')
        return redirect(url_for('modifier_rapport', rapport_id=rapport.id))

    @app.route('/rapports-cabinet/<int:rapport_id>', methods=['GET', 'POST'])
    @login_required
    def modifier_rapport(rapport_id):
        rapport = db.get_or_404(Rapport, rapport_id)
        entreprise = db.session.get(Entreprise, rapport.entreprise_id)
        _verifier_acces(entreprise)

        peut_rediger = current_user.peut_rediger_rapport
        if current_user.est_client and not rapport.publie:
            abort(403)

        gc.garantir_sections(rapport)

        if request.method == 'POST':
            if not peut_rediger:
                abort(403)
            rapport.titre = request.form['titre'].strip()
            rapport.periode = (request.form.get('periode') or '').strip()
            _appliquer_sections(rapport, request.form)

            # Ajouter une nouvelle section : on enregistre les saisies en
            # cours, puis on rattache une section vierge à modifier.
            if request.form.get('ajouter_section'):
                ordre = max((s.ordre for s in rapport.sections), default=-1) + 1
                db.session.add(SectionRapport(
                    rapport_id=rapport.id, ordre=ordre,
                    titre='Nouvelle section', corps=''))
                db.session.commit()
                return redirect(url_for('modifier_rapport', rapport_id=rapport.id)
                                + '#derniere-section')

            if request.form.get('publier'):
                gc.publier_rapport(rapport)
                flash("Rapport publié : le client y a désormais accès.", 'succes')
            else:
                db.session.commit()
                flash("Rapport enregistré.", 'succes')

            Audit.journaliser(current_user, 'modification', 'Rapport',
                              rapport.id, rapport.titre)
            db.session.commit()
            return redirect(url_for('modifier_rapport', rapport_id=rapport.id))

        return render_template('rapport_form.html', rapport=rapport,
                               entreprise=entreprise, peut_rediger=peut_rediger,
                               scope_args=_args(entreprise))

    def _appliquer_sections(rapport, formulaire):
        """Reporte les titres/corps saisis sur les sections, supprime celles
        cochées, puis renumérote l'ordre d'affichage."""
        ids = formulaire.getlist('section_id')
        titres = formulaire.getlist('section_titre')
        corps = formulaire.getlist('section_corps')
        a_supprimer = set(formulaire.getlist('supprimer'))

        existantes = {str(s.id): s for s in rapport.sections}
        ordre = 0
        for identifiant, titre, texte in zip(ids, titres, corps):
            if identifiant in a_supprimer:
                continue
            section = existantes.get(identifiant)
            if section is None:
                continue
            titre = (titre or '').strip() or 'Section'
            section.titre = titre
            section.corps = texte or ''
            section.ordre = ordre
            ordre += 1

        for identifiant in a_supprimer:
            section = existantes.get(identifiant)
            if section is not None:
                db.session.delete(section)
        db.session.commit()

    @app.route('/rapports-cabinet/<int:rapport_id>/pdf')
    @login_required
    def telecharger_rapport(rapport_id):
        rapport = db.get_or_404(Rapport, rapport_id)
        entreprise = db.session.get(Entreprise, rapport.entreprise_id)
        _verifier_acces(entreprise)
        if current_user.est_client and not rapport.publie:
            abort(403)

        from models import ParametresCabinet
        flux = documents_pdf.generer_rapport_pdf(
            rapport, gc.profil_de(entreprise),
            cabinet=db.session.get(ParametresCabinet, 1))
        nom = f"Rapport_{entreprise.nom.replace(' ', '_')}_{rapport.annee}.pdf"
        Audit.journaliser(current_user, 'export', 'Rapport', rapport.id, nom)
        db.session.commit()
        return send_file(flux, as_attachment=True, download_name=nom,
                         mimetype='application/pdf')

    @app.route('/rapports-cabinet/<int:rapport_id>/supprimer', methods=['POST'])
    @cabinet_requis
    def supprimer_rapport(rapport_id):
        rapport = db.get_or_404(Rapport, rapport_id)
        entreprise = db.session.get(Entreprise, rapport.entreprise_id)
        _verifier_acces(entreprise)

        Audit.journaliser(current_user, 'suppression', 'Rapport',
                          rapport.id, rapport.titre)
        db.session.delete(rapport)
        db.session.commit()
        flash("Rapport supprimé.", 'succes')
        return redirect(url_for('rapports_cabinet', **_args(entreprise)))

    # ==================================================================
    # 7. PROFIL DE FACTURATION
    # ==================================================================

    @app.route('/profil-facturation', methods=['GET', 'POST'])
    @login_required
    def profil_facturation():
        entreprise = _entreprise_courante()
        if entreprise is None:
            return render_template('choisir_entreprise.html',
                                   titre="Informations de facturation")
        _verifier_acces(entreprise)
        profil = gc.profil_de(entreprise)

        if request.method == 'POST':
            if not _peut_modifier(entreprise):
                abort(403)
            profil.raison_sociale = request.form['raison_sociale'].strip()
            profil.adresse = (request.form.get('adresse') or '').strip()
            profil.ville = (request.form.get('ville') or '').strip()
            profil.telephone = (request.form.get('telephone') or '').strip()
            profil.email = (request.form.get('email') or '').strip()
            profil.site_web = (request.form.get('site_web') or '').strip()
            profil.rccm = (request.form.get('rccm') or '').strip()
            profil.compte_contribuable = (request.form.get('compte_contribuable') or '').strip()
            profil.regime_tva = bool(request.form.get('regime_tva'))
            profil.taux_tva = float(request.form.get('taux_tva') or 18)
            profil.banque = (request.form.get('banque') or '').strip()
            profil.numero_compte = (request.form.get('numero_compte') or '').strip()
            profil.beneficiaire = (request.form.get('beneficiaire') or '').strip()
            profil.mobile_money = (request.form.get('mobile_money') or '').strip()
            profil.signataire = (request.form.get('signataire') or '').strip()
            profil.pied_legal = (request.form.get('pied_legal') or '').strip()
            profil.mention_pied = (request.form.get('mention_pied') or '').strip()

            # Logo et signature : repris en en-tête et en pied de chaque
            # facture et de chaque reçu émis par cette entreprise.
            logo = enregistrer_image(request.files.get('logo'),
                                     f'entreprise{entreprise.id}_logo')
            if logo:
                profil.logo = logo
                entreprise.logo = logo
            signature = enregistrer_image(request.files.get('signature'),
                                          f'entreprise{entreprise.id}_signature')
            if signature:
                profil.signature = signature
            if request.form.get('retirer_logo'):
                profil.logo = entreprise.logo = None
            if request.form.get('retirer_signature'):
                profil.signature = None

            Audit.journaliser(current_user, 'modification', 'ProfilFacturation',
                              profil.id, entreprise.nom)
            db.session.commit()
            flash("Informations de facturation enregistrées. Elles apparaîtront "
                  "automatiquement sur vos prochains documents.", 'succes')
            return redirect(url_for('profil_facturation', **_args(entreprise)))

        return render_template('profil_facturation.html',
                               profil=profil, entreprise=entreprise,
                               peut_modifier=_peut_modifier(entreprise),
                               scope_args=_args(entreprise))

    return app
