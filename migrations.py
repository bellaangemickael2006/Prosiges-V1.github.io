"""
Posiges V1 — mise à niveau automatique de la base existante.

L'application est installée par le cabinet chez ses clients puis mise à jour
à distance. On ne peut donc pas demander à un gérant de TPE de lancer une
commande de migration : la base se met à niveau toute seule au démarrage.

Trois opérations, toutes idempotentes — les relancer ne change rien :

  1. Ajout des colonnes apparues dans les modèles (ALTER TABLE ADD COLUMN).
  2. Reprise des rôles : l'ancien « gerant » désignait la direction du
     cabinet et devient « cabinet » ; l'ancien « client » désignait le
     gérant de l'entreprise accompagnée et devient « gerant ».
  3. Reprise des catégories d'opérations, saisies librement dans les
     versions précédentes, vers le plan de rubriques fermé.

Aucune de ces opérations ne supprime de données. Les colonnes devenues
inutiles sont laissées en place : SQLite ne sait pas les retirer sans
recréer la table, et une colonne orpheline ne coûte rien.
"""
import logging

from sqlalchemy import inspect, text

from models import (CA_NON_VENTILE, CORRESPONDANCE_ANCIENNES_CATEGORIES,
                    ParametresCabinet, PREFIXE_CHARGE, Role, TypeOperation, db)

journal = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 1. Colonnes manquantes
# --------------------------------------------------------------------------

def _type_sql(colonne):
    """Type SQL d'une colonne, dans un dialecte accepté partout."""
    try:
        return colonne.type.compile(db.engine.dialect)
    except Exception:                      # type exotique : repli sûr
        return 'VARCHAR'


def ajouter_colonnes_manquantes():
    """Aligne le schéma physique sur les modèles, sans rien détruire."""
    inspecteur = inspect(db.engine)
    tables_existantes = set(inspecteur.get_table_names())
    ajoutees = []

    for table in db.metadata.sorted_tables:
        if table.name not in tables_existantes:
            continue                       # create_all vient de la créer

        presentes = {c['name'] for c in inspecteur.get_columns(table.name)}
        for colonne in table.columns:
            if colonne.name in presentes:
                continue
            if colonne.primary_key:
                continue                   # ne s'ajoute pas après coup

            declaration = f'{colonne.name} {_type_sql(colonne)}'
            # Une colonne ajoutée à une table déjà peuplée ne peut pas être
            # NOT NULL sans valeur par défaut : on la laisse nullable.
            defaut = colonne.default.arg if (
                colonne.default is not None
                and not callable(getattr(colonne.default, 'arg', None))) else None
            if isinstance(defaut, bool):
                declaration += f" DEFAULT {1 if defaut else 0}"
            elif isinstance(defaut, (int, float)):
                declaration += f" DEFAULT {defaut}"
            elif isinstance(defaut, str):
                echappe = defaut.replace("'", "''")
                declaration += f" DEFAULT '{echappe}'"

            try:
                db.session.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN {declaration}'))
                db.session.commit()
                ajoutees.append(f'{table.name}.{colonne.name}')
            except Exception as erreur:    # colonne déjà là, ou type refusé
                db.session.rollback()
                journal.debug('Colonne %s.%s non ajoutée : %s',
                              table.name, colonne.name, erreur)

    return ajoutees


# --------------------------------------------------------------------------
# 2. Reprise des rôles
# --------------------------------------------------------------------------

def migrer_roles():
    """Applique Role.MIGRATION en une seule instruction.

    L'ordre importe : « gerant » doit devenir « cabinet » AVANT que
    « client » ne devienne « gerant », sinon les deux se confondent. Un
    CASE unique évalue toujours l'ancienne valeur de chaque ligne, ce qui
    règle le problème sans passer par une valeur temporaire.
    """
    if not Role.MIGRATION:
        return 0

    branches = ' '.join(
        f"WHEN '{ancien}' THEN '{nouveau}'"
        for ancien, nouveau in Role.MIGRATION.items())
    anciens = ', '.join(f"'{a}'" for a in Role.MIGRATION)

    resultat = db.session.execute(text(
        f'UPDATE "user" SET role = CASE role {branches} ELSE role END '
        f'WHERE role IN ({anciens})'))
    db.session.commit()
    return resultat.rowcount or 0


# --------------------------------------------------------------------------
# 3. Reprise des catégories d'opérations
# --------------------------------------------------------------------------

def migrer_categories():
    """Convertit les catégories libres en codes du plan de rubriques.

    Une catégorie déjà codée (préfixe reconnu) est laissée telle quelle, ce
    qui rend la fonction rejouable. Une catégorie inconnue bascule vers la
    ligne « Autres » du bon côté : rien n'est perdu, tout reste comptabilisé.
    """
    from models import Operation, Rubrique

    operations = Operation.query.all()
    convertis = 0

    for operation in operations:
        categorie = (operation.categorie or '').strip()
        if (Rubrique.est_ca(categorie) or Rubrique.est_charge(categorie)
                or Rubrique.est_encaissement(categorie)):
            continue

        code = CORRESPONDANCE_ANCIENNES_CATEGORIES.get(categorie.lower())
        if code is None:
            code = (CA_NON_VENTILE
                    if operation.type_operation != TypeOperation.ACHAT
                    else Rubrique.CODE_AUTRES_CHARGES)
        # Une catégorie de vente ne peut pas devenir une charge, et
        # inversement : on recale sur le sens réel de l'opération.
        elif operation.type_operation == TypeOperation.ACHAT and not code.startswith(
                PREFIXE_CHARGE):
            code = Rubrique.CODE_AUTRES_CHARGES
        elif operation.type_operation != TypeOperation.ACHAT and code.startswith(
                PREFIXE_CHARGE):
            code = CA_NON_VENTILE

        operation.categorie = code
        convertis += 1

    if convertis:
        db.session.commit()
    return convertis


# --------------------------------------------------------------------------
# 4. Ligne unique des paramètres du cabinet
# --------------------------------------------------------------------------

def garantir_parametres_cabinet(nom_cabinet=None):
    """Crée la ligne de paramètres du cabinet si elle n'existe pas encore."""
    parametres = db.session.get(ParametresCabinet, 1)
    if parametres is None:
        parametres = ParametresCabinet(id=1)
        if nom_cabinet:
            parametres.raison_sociale = nom_cabinet
        db.session.add(parametres)
        db.session.commit()
    garantir_preambule_cabinet(parametres)
    return parametres


# Préambule par défaut des rapports (repris du modèle du cabinet). Chargé une
# seule fois : le consultant reste libre de tout modifier ensuite dans
# « Paramètres », ses modifications ne sont jamais écrasées.
PREAMBULE_DEFAUT = {
    'type_rapport': "RAPPORT DE MISSION D'ÉTAT DES LIEUX",
    'entete_pages': ("Étude & Conseils · Assistance en gestion · "
                     "Entrepreneuriat · Formation — "
                     "IDE : Inducteur de développement économique"),
    'domaines_activite': (
        "Formation et Coaching\n"
        "Études et conseils en management\n"
        "Entrepreneuriat\n"
        "Assistance en gestion\n"
        "Représentation commerciale"),
    'distinctions': (
        "Lauréat 2016 du Prix Services de la Business Plan Compétition de la "
        "CGECI (Patronat Ivoirien)\n"
        "Lauréat 2016 du Prix du Président de la République du Jeune "
        "entrepreneur Émergent (District d'Abidjan)\n"
        "Lauréat 2017 du 2ème Super Prix du Président de la République du "
        "Jeune entrepreneur Émergent (District d'Abidjan)"),
    'contacts_referent': (
        "Célestin AKROU — 05 46 95 05 02 / 07 57 10 74 10 / 07 68 22 46 45"),
    # « ## » en début de ligne = sous-titre encadré (bande bleue) sur la page
    # « Présentation ». Tout le reste est du corps ; « - » fait une puce.
    'presentation': (
        "## A- PROPOS DU CABINET\n"
        "AK WORLD BUSINESS SERVICES est une entreprise de droit ivoirien, "
        "légalement constituée sous le statut de société à responsabilité "
        "limitée au capital social de 5 000 000 FCFA. Elle est enregistrée "
        "sous le N° CC : 1822007 N – RC : CI-ABJ-2018-B-07247. Le siège social "
        "du cabinet est situé à Abidjan, Cocody Angré Pétro Ivoire.\n"
        "## A- NOS DOMAINES D'ACTIVITÉS\n"
        "- Études, conseils et réalisation.\n"
        "- Formation, recrutement et accompagnement professionnel.\n"
        "- Business développement, création et gestion des entreprises.\n"
        "- Conseils en finances et gestion de projets.\n"
        "- Certifications professionnelles individuelles et des organisations.\n"
        "## B- PRODUITS ET SERVICES\n"
        "- Formations inter-entreprises ou à la carte.\n"
        "- Appuis à la création et à la gestion d'entreprise.\n"
        "- Prestations comptables et financières (plan comptable, business "
        "model, bilan, tableaux de bord financiers, gestion fiscale).\n"
        "- Programmes de développement socio-économique.\n"
        "- Appui à l'autonomisation des acteurs de l'informel.\n"
        "- Formations professionnelles certifiantes.\n"
        "- Formations métiers pratiques / Prise en main de poste.\n"
        "- Insertion professionnelle des jeunes.\n"
        "## C- NOS PROGRAMMES DÉDIÉS\n"
        "- Programme performance.\n"
        "- Programme croissance.\n"
        "- Programme impact emplois.\n"
        "- Programme simplifié de gestion.\n"
        "- Programme Incubateur pour Reconversion Professionnelle.\n"
        "- Programme coaching professionnel individuel et organisationnel.\n"
        "## D- QUELQUES DISTINCTIONS\n"
        "- Lauréat 2016 du Prix du Président de la République du Jeune "
        "Entrepreneur Émergent.\n"
        "- Lauréat 2016 du Prix Services de la Business Plan Compétition "
        "CGECI (Patronat Ivoirien).\n"
        "- Lauréat 2017 du 2ème Super Prix du Président de la République du "
        "Jeune Entrepreneur Émergent.\n"
        "## E- NOS COORDONNÉES\n"
        "- Tél : (+225) 07 57 107 410 / (+225) 05 46 95 05 02\n"
        "- E-mail : akworldbusiness@gmail.com\n"
        "- Adresse : Abidjan Cocody Angré Pétro Ivoire cité star 2.\n"
        "- Référent projet : M. AKROU Célestin, Fondateur et Associé-gérant"),
    'habilitations_fdfp': (
        "Génie rural, plurivalences de la production agricole\n"
        "Management, gestion d'entreprise, entrepreneuriat\n"
        "Pluri-technologies BTP\n"
        "Pluri-technologies des transformations\n"
        "Spécialités plurivalentes de la G.R.H\n"
        "Spécialités plurivalentes échanges, gestion\n"
        "Spécialités plurivalentes de l'informatique\n"
        "Spécialités plurivalentes de capacité individuelle\n"
        "Spécialité plurivalente de la communication"),
}


# Version du modèle de préambule livré avec l'application. À incrémenter quand
# le gabarit par défaut évolue (nouvelle mise en page de la présentation, etc.).
PREAMBULE_VERSION = 2


def garantir_preambule_cabinet(parametres):
    """Charge — ou rafraîchit — le préambule modèle des rapports.

    · Première initialisation : ne remplit que les champs restés vides.
    · Montée de `PREAMBULE_VERSION` : rafraîchit le gabarit modèle. C'est fait
      avant la mise en production ; une fois le cabinet installé, le numéro de
      version n'est plus incrémenté, donc ses saisies ne sont jamais écrasées.
    """
    version = getattr(parametres, 'preambule_version', 0) or 0
    if version >= PREAMBULE_VERSION:
        return parametres

    premiere_fois = not getattr(parametres, 'preambule_initialise', False)
    for champ, valeur in PREAMBULE_DEFAUT.items():
        if premiere_fois and getattr(parametres, champ, None):
            continue                      # ne pas écraser un champ déjà rempli
        setattr(parametres, champ, valeur)
    parametres.preambule_initialise = True
    parametres.preambule_version = PREAMBULE_VERSION
    db.session.commit()
    return parametres


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------

# Version courante du schéma de données. À incrémenter le jour où une
# nouvelle reprise ponctuelle doit être appliquée aux bases existantes.
VERSION_DONNEES = 1


def appliquer(nom_cabinet=None, verbeux=False):
    """Exécute la mise à niveau complète. Ne lève jamais d'exception.

    L'ajout des colonnes manquantes est rejoué à chaque démarrage : il est
    sans effet si le schéma est déjà à jour. Les reprises de DONNÉES, en
    revanche, ne s'appliquent qu'une seule fois — rejouer la reprise des
    rôles renommerait à tort les comptes créés depuis, puisque le code
    « gerant » a changé de sens entre les deux versions.
    """
    resume = {'colonnes': [], 'roles': 0, 'categories': 0, 'version': None}
    try:
        resume['colonnes'] = ajouter_colonnes_manquantes()
        parametres = garantir_parametres_cabinet(nom_cabinet)

        version = parametres.version_donnees or 0
        if version < VERSION_DONNEES:
            resume['roles'] = migrer_roles()
            resume['categories'] = migrer_categories()
            parametres.version_donnees = VERSION_DONNEES
            db.session.commit()
            resume['version'] = VERSION_DONNEES
    except Exception as erreur:            # pragma: no cover
        db.session.rollback()
        journal.warning('Mise à niveau de la base incomplète : %s', erreur)
        return resume

    if verbeux and (resume['colonnes'] or resume['roles'] or resume['categories']):
        print(f"  Base mise à niveau : {len(resume['colonnes'])} colonne(s), "
              f"{resume['roles']} rôle(s), {resume['categories']} catégorie(s).")
    return resume
