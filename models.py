"""
Posiges V1 — modèles de données.

L'application articule deux parties prenantes, conformément à l'architecture
arrêtée avec le cabinet :

  - AK WORLD BUSINESS SERVICES : le cabinet. Il installe l'application chez
    ses clients, paramètre les comptes à distance, accède à la totalité des
    données de ses clients, publie les classeurs et rédige les rapports.

  - L'ENTREPRISE ACCOMPAGNÉE : le client. Elle gère son activité au quotidien
    (gérant, comptable/caissier/trésorier, utilisateurs standards).

Deux périmètres coexistent donc dans le même journal d'opérations :
  - PÉRIMÈTRE INTERNE  : opérations d'AK World, rattachées à un `departement`.
  - PÉRIMÈTRE CLIENT   : opérations d'une TPE/PME accompagnée, rattachées à une
                          `entreprise_id`.

Une opération appartient toujours à l'un OU l'autre (jamais les deux).
"""
from datetime import datetime, timedelta

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


# --------------------------------------------------------------------------
# Énumérations métier
# --------------------------------------------------------------------------

class Role:
    """Rôles applicatifs, calqués sur l'architecture Posiges V1.

    ── Côté AK World Business Services (le cabinet) ──────────────────────
    CABINET      : direction du cabinet. Accès total à tous les clients,
                   paramètres, création des comptes, publication et rédaction
                   des rapports. C'est le seul rôle qui touche aux paramètres.
    CONSULTANT   : membre du cabinet, limité aux entreprises qui lui sont
                   assignées. Mêmes outils d'analyse, pas les paramètres.
    RESPONSABLE  : responsable d'un département interne d'AK World.

    ── Côté entreprise accompagnée (le client) ───────────────────────────
    GERANT       : gérant de l'entreprise. Vue totale sur son entreprise et
                   SEUL habilité, côté client, à saisir les budgets.
    COMPTABLE    : comptable / caissier / trésorier. Mêmes tableaux de bord
                   et mêmes exports que le gérant, sans la saisie du budget.
    STANDARD     : utilisateur standard ajouté par le gérant. Il enregistre
                   les entrées et sorties, gère le stock, et ne voit qu'un
                   tableau de bord simplifié limité à SES PROPRES saisies.
    """
    CABINET = 'cabinet'
    CONSULTANT = 'consultant'
    RESPONSABLE = 'responsable'
    GERANT = 'gerant'
    COMPTABLE = 'comptable'
    STANDARD = 'standard'

    CHOIX = [CABINET, CONSULTANT, RESPONSABLE, GERANT, COMPTABLE, STANDARD]

    # Rôles rattachés à une entreprise accompagnée (champ entreprise_id requis)
    COTE_ENTREPRISE = [GERANT, COMPTABLE, STANDARD]
    # Rôles rattachés au cabinet
    COTE_CABINET = [CABINET, CONSULTANT, RESPONSABLE]

    LABELS = {
        CABINET: 'Cabinet AK World',
        CONSULTANT: 'Consultant cabinet',
        RESPONSABLE: 'Responsable de département',
        GERANT: "Gérant de l'entreprise",
        COMPTABLE: 'Comptable / Caissier / Trésorier',
        STANDARD: 'Utilisateur standard',
    }

    DESCRIPTIONS = {
        CABINET: "Accès total à toutes les entreprises, aux paramètres, "
                 "à la création des comptes et à la rédaction des rapports.",
        CONSULTANT: "Suivi des entreprises assignées : tableaux de bord, "
                    "exports et rédaction des rapports.",
        RESPONSABLE: "Opérations du département interne AK World dont "
                     "l'utilisateur a la charge.",
        GERANT: "Vue complète de son entreprise, saisie des budgets, "
                "exports et lecture des rapports du cabinet.",
        COMPTABLE: "Tableaux de bord, indicateurs, exports et lecture des "
                   "rapports. Ne saisit pas les budgets.",
        STANDARD: "Saisit les entrées, les sorties et le stock. Ne voit qu'un "
                  "tableau de bord limité à ses propres opérations.",
    }

    # Correspondance ancien code → nouveau code, appliquée une seule fois au
    # démarrage (voir `migrations.py`). L'ancien « gerant » désignait la
    # direction du cabinet ; il devient « cabinet ». L'ancien « client »
    # désignait le gérant de l'entreprise ; il devient « gerant ».
    MIGRATION = {'gerant': CABINET, 'client': GERANT}


class Departement:
    COMMERCIAL = 'commercial'
    COMPTABILITE = 'comptabilite'
    RH = 'rh'
    STOCK = 'stock'

    CHOIX = [COMMERCIAL, COMPTABILITE, RH, STOCK]
    LABELS = {
        COMMERCIAL: 'Commercial',
        COMPTABILITE: 'Comptabilité',
        RH: 'Ressources Humaines',
        STOCK: 'Stock',
    }


class TypeOperation:
    """Sens de l'opération au journal de bord.

    VENTE = entrée d'argent (ou de créance), ACHAT = sortie. Ces deux codes
    historiques sont conservés en base ; l'interface parle d'« entrée » et de
    « sortie », vocabulaire du journal de bord demandé par le cabinet.
    """
    VENTE = 'vente'
    ACHAT = 'achat'
    AUTRE = 'autre'

    CHOIX = [VENTE, ACHAT]
    CHOIX_COMPLET = [VENTE, ACHAT, AUTRE]
    LABELS = {
        VENTE: 'Entrée (vente, encaissement)',
        ACHAT: 'Sortie (achat, dépense)',
        AUTRE: 'Autre',
    }
    LABELS_COURTS = {VENTE: 'Entrée', ACHAT: 'Sortie', AUTRE: 'Autre'}


class ModePaiement:
    CASH = 'cash'
    MOBILE_MONEY = 'mobile_money'
    BANQUE = 'banque'
    CREDIT = 'credit'

    CHOIX = [CASH, MOBILE_MONEY, BANQUE, CREDIT]
    LABELS = {
        CASH: 'Espèces',
        MOBILE_MONEY: 'Mobile Money',
        BANQUE: 'Banque',
        CREDIT: 'Crédit',
    }


# ==========================================================================
#  PLAN DE RUBRIQUES
#
#  C'est la pièce maîtresse de Posiges V1. Le compte d'exploitation et le
#  compte de trésorerie remis par le cabinet fixent une liste de libellés
#  FERMÉE : l'utilisateur ne saisit jamais une catégorie au clavier, il en
#  choisit une dans cette liste. Sans cela, comme l'a résumé le cabinet,
#  « on va avoir des informations qui vont dans tous les sens » et le
#  rapprochement budget / réalisé devient impossible.
#
#  Chaque code est stocké tel quel dans `Operation.categorie`. Le préfixe
#  indique la famille de rubrique :
#      ca:<id>    chiffre d'affaires d'une famille de produits
#      chg:<code> charge (variable ou fixe)
#      enc:<code> encaissement de trésorerie hors chiffre d'affaires
# ==========================================================================

PREFIXE_CA = 'ca:'
PREFIXE_CHARGE = 'chg:'
PREFIXE_ENCAISSEMENT = 'enc:'

# Chiffre d'affaires non ventilé : utilisé tant qu'aucune famille de produits
# n'a été créée, et pour les reprises de données antérieures.
CA_NON_VENTILE = 'ca:0'


class NatureCharge:
    """Distinction structurante du compte d'exploitation.

    VARIABLE : liée au niveau d'activité (achats, intrants, emballages).
    FIXE     : due quel que soit le niveau d'activité (loyer, salaires).
    """
    VARIABLE = 'variable'
    FIXE = 'fixe'

    CHOIX = [VARIABLE, FIXE]
    LABELS = {VARIABLE: 'Charge variable', FIXE: 'Charge fixe'}


# --- II. CHARGES VARIABLES (ordre du modèle papier du cabinet) ------------
CHARGES_VARIABLES = [
    ('achat_marchandises', 'Achat de marchandises'),
    ('intrants', 'Intrants de production (ingrédients et additifs, '
                 'consommables de production)'),
    ('transport_achat', 'Transport sur achat'),
    ('emballages', 'Emballages et conditionnement (pots, bouteilles, '
                   'étiquettes, cartons)'),
    ('sous_traitance', 'Frais de sous-traitance / production (salaires du '
                       'personnel saisonnier, frais de manutention)'),
    ('energie_production', 'Énergie et eau liées à la production '
                           '(électricité, gaz, charbon ou bois de chauffe, eau)'),
    ('transport_livraison', 'Transport sur livraison (grossistes, boutiques)'),
    ('frais_stockage', "Frais de stockage (location d'espaces de stockage "
                       'supplémentaires)'),
    ('commissions_vente', 'Commissions sur vente'),
]

# --- IV. CHARGES FIXES ----------------------------------------------------
CHARGES_FIXES = [
    ('loyer', 'Loyer'),
    ('salaires', 'Salaires (personnel, rémunération du dirigeant)'),
    ('charges_sociales', 'Charges sociales (CNPS, CNAM)'),
    ('impots_taxes', 'Impôts et taxes'),
    ('entretien', 'Entretien et réparation'),
    ('energie_eau', 'Énergie et eau (eau, électricité, gaz)'),
    ('assurances', 'Assurances (locaux, RC professionnel, maladie)'),
    ('honoraires', 'Honoraires (CGA)'),
    ('publicite', 'Publicité et communication'),
    ('transport_carburant', 'Transport et carburant'),
    ('agios', 'Agios et frais bancaires'),
    # Ligne de recueil : n'apparaît dans les tableaux que si elle porte un
    # montant. Elle évite qu'une reprise de données ou une dépense atypique
    # disparaisse silencieusement du compte d'exploitation.
    ('autres', 'Autres charges diverses'),
]

# --- I. ENCAISSEMENTS DU COMPTE DE TRÉSORERIE -----------------------------
# « Ventes au comptant » n'est pas dans cette liste : cette ligne est
# alimentée automatiquement par les ventes encaissées du journal.
ENCAISSEMENTS_DIRECTS = [
    ('creances_clients', 'Encaissement des créances clients'),
    ('subventions', "Subventions d'exploitation"),
    ('apports_capital', 'Apports en capital des associés'),
    ('emprunts', 'Emprunts bancaires'),
    ('decouverts', 'Découverts bancaires autorisés'),
    ('autres', 'Autres encaissements'),
]

RUBRIQUE_VENTES_COMPTANT = 'ventes_comptant'
LIBELLE_VENTES_COMPTANT = 'Ventes au comptant'


class Rubrique:
    """Accès unifié au plan de rubriques."""

    VARIABLES = CHARGES_VARIABLES
    FIXES = CHARGES_FIXES
    ENCAISSEMENTS = ENCAISSEMENTS_DIRECTS

    # Toutes les charges, dans l'ordre d'affichage, avec leur nature
    CHARGES = ([(PREFIXE_CHARGE + c, lib, NatureCharge.VARIABLE)
                for c, lib in CHARGES_VARIABLES]
               + [(PREFIXE_CHARGE + c, lib, NatureCharge.FIXE)
                  for c, lib in CHARGES_FIXES])

    LABELS = {}
    for _code, _lib in CHARGES_VARIABLES + CHARGES_FIXES:
        LABELS[PREFIXE_CHARGE + _code] = _lib
    for _code, _lib in ENCAISSEMENTS_DIRECTS:
        LABELS[PREFIXE_ENCAISSEMENT + _code] = _lib
    LABELS[CA_NON_VENTILE] = "Chiffre d'affaires (non ventilé)"
    del _code, _lib

    NATURES = {PREFIXE_CHARGE + c: NatureCharge.VARIABLE
               for c, _ in CHARGES_VARIABLES}
    NATURES.update({PREFIXE_CHARGE + c: NatureCharge.FIXE
                    for c, _ in CHARGES_FIXES})

    CODE_AUTRES_CHARGES = PREFIXE_CHARGE + 'autres'
    CODE_AUTRES_ENCAISSEMENTS = PREFIXE_ENCAISSEMENT + 'autres'

    @staticmethod
    def est_ca(code):
        return (code or '').startswith(PREFIXE_CA)

    @staticmethod
    def est_charge(code):
        return (code or '').startswith(PREFIXE_CHARGE)

    @staticmethod
    def est_encaissement(code):
        return (code or '').startswith(PREFIXE_ENCAISSEMENT)

    @staticmethod
    def famille_id(code):
        """Identifiant de la famille de produits porté par un code `ca:`."""
        if not Rubrique.est_ca(code):
            return None
        try:
            return int(code.split(':', 1)[1])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def nature(code):
        return Rubrique.NATURES.get(code)

    @classmethod
    def libelle(cls, code, familles=None):
        """Libellé lisible d'un code de rubrique.

        `familles` est un dictionnaire {id: nom} permettant de nommer les
        lignes de chiffre d'affaires sans requête supplémentaire.
        """
        if cls.est_ca(code):
            identifiant = cls.famille_id(code)
            if identifiant and familles and identifiant in familles:
                return f"Chiffre d'affaires — {familles[identifiant]}"
            return cls.LABELS.get(CA_NON_VENTILE)
        return cls.LABELS.get(code, code or '—')


# Reprise des catégories libres saisies avec les versions précédentes.
# Tout ce qui n'est pas reconnu bascule vers la ligne « Autres ».
CORRESPONDANCE_ANCIENNES_CATEGORIES = {
    "chiffre d'affaires": CA_NON_VENTILE,
    'vente de marchandises': CA_NON_VENTILE,
    'prestation de services': CA_NON_VENTILE,
    'achat marchandises': PREFIXE_CHARGE + 'achat_marchandises',
    'achat de marchandises': PREFIXE_CHARGE + 'achat_marchandises',
    'achat matières premières': PREFIXE_CHARGE + 'intrants',
    'transport': PREFIXE_CHARGE + 'transport_carburant',
    'salaires': PREFIXE_CHARGE + 'salaires',
    'loyer': PREFIXE_CHARGE + 'loyer',
    'électricité / eau': PREFIXE_CHARGE + 'energie_eau',
    'communication': PREFIXE_CHARGE + 'publicite',
    'impôts et taxes': PREFIXE_CHARGE + 'impots_taxes',
    'frais généraux': PREFIXE_CHARGE + 'autres',
    'divers': PREFIXE_CHARGE + 'autres',
}


# --------------------------------------------------------------------------
# Table d'association : consultants <-> entreprises suivies
# --------------------------------------------------------------------------

suivi_consultant = db.Table(
    'suivi_consultant',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('entreprise_id', db.Integer, db.ForeignKey('entreprise.id'), primary_key=True),
)


# --------------------------------------------------------------------------
# Entreprise accompagnée (TPE/PME cliente du cabinet)
# --------------------------------------------------------------------------

class Entreprise(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False, unique=True)
    secteur = db.Column(db.String(120))
    contact = db.Column(db.String(100))
    localisation = db.Column(db.String(150))
    actif = db.Column(db.Boolean, default=True, nullable=False)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    # Dossier Drive et Google Sheet dédiés à cette entreprise (optionnels :
    # si vides, la configuration globale de config.py est utilisée)
    drive_folder_id = db.Column(db.String(120))
    sheet_id = db.Column(db.String(120))

    # Logo de l'entreprise, repris en en-tête de ses factures et de ses reçus.
    logo = db.Column(db.String(400))

    # ---- Profil de facturation ----
    # Ces informations sont reprises automatiquement en en-tête de chaque
    # facture proforma et de chaque reçu émis par l'entreprise. Elles sont
    # saisies une seule fois : le client ne retape jamais rien.
    raison_sociale = db.Column(db.String(180))     # nom complet sur les documents
    adresse = db.Column(db.String(250))
    telephone = db.Column(db.String(100))
    email = db.Column(db.String(150))
    rccm = db.Column(db.String(100))               # registre du commerce
    compte_contribuable = db.Column(db.String(100))
    banque = db.Column(db.String(150))
    numero_compte = db.Column(db.String(100))
    mobile_money = db.Column(db.String(100))
    pied_facture = db.Column(db.Text)              # mentions libres bas de page

    # Classeur Google Sheets de l'entreprise, créé à l'installation et
    # réécrit à chaque publication. Son identifiant reste stable pour que le
    # lien communiqué au client reste valable d'une publication à l'autre.
    sheet_rapport_id = db.Column(db.String(120))
    sheet_rapport_url = db.Column(db.String(400))
    derniere_publication = db.Column(db.DateTime)

    consultants = db.relationship(
        'User', secondary=suivi_consultant, back_populates='entreprises_suivies'
    )

    def libelle_commercial(self):
        """Nom à faire figurer sur les documents commerciaux."""
        return self.raison_sociale or self.nom

    def profil_complet(self):
        """Indique si les informations minimales de facturation sont saisies."""
        return bool(self.adresse and self.telephone)

    def __repr__(self):
        return f'<Entreprise {self.nom}>'


# --------------------------------------------------------------------------
# Utilisateur
# --------------------------------------------------------------------------

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    role = db.Column(db.String(30), nullable=False, default=Role.RESPONSABLE)
    departement = db.Column(db.String(50))       # si RESPONSABLE
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))  # si CLIENT

    actif = db.Column(db.Boolean, default=True, nullable=False)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    derniere_connexion = db.Column(db.DateTime)

    # Protection contre les tentatives de connexion par force brute.
    tentatives_echouees = db.Column(db.Integer, default=0, nullable=False)
    verrouille_jusqua = db.Column(db.DateTime)

    entreprise = db.relationship('Entreprise', foreign_keys=[entreprise_id])
    entreprises_suivies = db.relationship(
        'Entreprise', secondary=suivi_consultant, back_populates='consultants'
    )

    # ---- Mot de passe ----

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ---- Verrouillage anti force brute ----

    SEUIL_VERROUILLAGE = 6
    DUREE_VERROUILLAGE_MINUTES = 15

    def est_verrouille(self):
        return bool(self.verrouille_jusqua and self.verrouille_jusqua > datetime.utcnow())

    def enregistrer_echec(self):
        self.tentatives_echouees = (self.tentatives_echouees or 0) + 1
        if self.tentatives_echouees >= self.SEUIL_VERROUILLAGE:
            self.verrouille_jusqua = (datetime.utcnow()
                                      + timedelta(minutes=self.DUREE_VERROUILLAGE_MINUTES))

    def reinitialiser_echecs(self):
        self.tentatives_echouees = 0
        self.verrouille_jusqua = None

    # ---- Rôles ----

    @property
    def est_cabinet(self):
        """Direction du cabinet AK World : accès total, y compris paramètres."""
        return self.role == Role.CABINET

    @property
    def est_consultant(self):
        return self.role == Role.CONSULTANT

    @property
    def est_responsable(self):
        return self.role == Role.RESPONSABLE

    @property
    def est_gerant(self):
        """Gérant de l'entreprise accompagnée (et non du cabinet)."""
        return self.role == Role.GERANT

    @property
    def est_comptable(self):
        return self.role == Role.COMPTABLE

    @property
    def est_standard(self):
        return self.role == Role.STANDARD

    @property
    def est_client(self):
        """Membre d'une entreprise accompagnée, quel que soit son niveau."""
        return self.role in Role.COTE_ENTREPRISE

    @property
    def est_interne(self):
        """Membre de l'équipe AK World (par opposition à une entreprise cliente)."""
        return self.role in Role.COTE_CABINET

    # ---- Habilitations fonctionnelles ----
    #
    # Nommer les droits plutôt que tester les rôles un à un : le jour où un
    # rôle évolue, une seule ligne change ici au lieu de vingt dans les vues.

    @property
    def peut_parametrer(self):
        """Paramètres de l'application, entreprises, comptes utilisateurs."""
        return self.est_cabinet

    @property
    def peut_analyser(self):
        """Graphiques, comptes détaillés, exports Sheets et Excel."""
        return self.role in (Role.CABINET, Role.CONSULTANT, Role.GERANT,
                             Role.COMPTABLE)

    @property
    def peut_budgeter(self):
        """Saisie des budgets d'exploitation et de trésorerie."""
        return self.role in (Role.CABINET, Role.GERANT)

    @property
    def peut_rediger_rapport(self):
        """Seul le cabinet rédige les rapports ; le client les lit."""
        return self.role in (Role.CABINET, Role.CONSULTANT)

    @property
    def voit_tout_le_perimetre(self):
        """Faux pour l'utilisateur standard, limité à ses propres saisies."""
        return not self.est_standard

    def libelle_role(self):
        base = Role.LABELS.get(self.role, self.role)
        if self.est_responsable and self.departement:
            return f"{base} — {Departement.LABELS.get(self.departement, self.departement)}"
        if self.est_client and self.entreprise:
            return f"{base} — {self.entreprise.nom}"
        return base

    def filtre_saisies_propres(self):
        """Identifiant à utiliser pour restreindre un tableau de bord.

        Renvoie l'identifiant de l'utilisateur pour un profil standard —
        qui ne voit que ses propres opérations — et None pour les autres.
        """
        return self.id if self.est_standard else None

    # ---- Périmètre d'accès ----

    def entreprises_accessibles(self):
        """Entreprises accompagnées que cet utilisateur peut consulter."""
        if self.est_cabinet:
            return Entreprise.query.order_by(Entreprise.nom).all()
        if self.est_consultant:
            return sorted(self.entreprises_suivies, key=lambda e: e.nom)
        if self.est_client and self.entreprise:
            return [self.entreprise]
        return []

    def peut_voir_entreprise(self, entreprise_id):
        if entreprise_id is None:
            return False
        return any(e.id == entreprise_id for e in self.entreprises_accessibles())

    def peut_voir_operation(self, operation):
        if self.est_cabinet:
            return True
        if operation.entreprise_id:
            if not self.peut_voir_entreprise(operation.entreprise_id):
                return False
            # L'utilisateur standard ne consulte que ses propres saisies.
            if self.est_standard:
                return operation.cree_par_id == self.id
            return True
        # Opération interne : réservée au responsable du département concerné
        return self.est_responsable and self.departement == operation.departement

    def peut_modifier_operation(self, operation):
        if self.est_cabinet:
            return True
        if not self.peut_voir_operation(operation):
            return False
        if self.est_standard:
            # L'utilisateur standard ne corrige que ce qu'il a lui-même saisi
            return operation.cree_par_id == self.id
        return True

    def peut_supprimer_operation(self, operation):
        # Mêmes règles que la modification (traçabilité assurée par l'audit)
        return self.peut_modifier_operation(operation)

    def __repr__(self):
        return f'<User {self.email} [{self.role}]>'


# --------------------------------------------------------------------------
# Clients finaux (les acheteurs, côté interne ou côté entreprise accompagnée)
# --------------------------------------------------------------------------

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    contact = db.Column(db.String(100))
    email = db.Column(db.String(150))
    localisation = db.Column(db.String(150))
    # NULL = carnet d'adresses interne AK World ; sinon carnet de l'entreprise
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    entreprise = db.relationship('Entreprise', backref='clients')

    def __repr__(self):
        return f'<Client {self.nom}>'


# --------------------------------------------------------------------------
# Opération (ligne de journal)
# --------------------------------------------------------------------------

class Operation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date_operation = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    type_operation = db.Column(db.String(20), nullable=False)
    categorie = db.Column(db.String(100), nullable=False)

    # Périmètre : l'un OU l'autre
    departement = db.Column(db.String(50))
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))

    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))
    montant = db.Column(db.Float, nullable=False)
    mode_paiement = db.Column(db.String(30), nullable=False)
    reference = db.Column(db.String(100))
    libelle = db.Column(db.String(255))

    # Charge fixe ou variable — sert au compte d'exploitation détaillé.
    # Renseigné pour les achats uniquement ; vide pour les ventes.
    nature_charge = db.Column(db.String(20))

    numero_facture = db.Column(db.String(50), unique=True)
    fichier_url = db.Column(db.String(400))
    fichier_local = db.Column(db.String(400))
    synchro_sheet = db.Column(db.Boolean, default=False)

    # Document commercial à l'origine de l'écriture, le cas échéant. Une
    # facture engendre une écriture de chiffre d'affaires par famille de
    # produits, et chaque encaissement une écriture de trésorerie ; ce lien
    # permet de toutes les retrouver pour les refaire ou les annuler.
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'))

    cree_par_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, onupdate=datetime.utcnow)

    client = db.relationship('Client', backref='operations')
    entreprise = db.relationship('Entreprise', backref='operations')
    cree_par = db.relationship('User', backref='operations')

    def signe(self):
        """Vente = entrée (+1), Achat = sortie (-1), Autre traité en entrée."""
        return -1 if self.type_operation == TypeOperation.ACHAT else 1

    def montant_signe(self):
        return self.montant * self.signe()

    def libelle_perimetre(self):
        if self.entreprise:
            return self.entreprise.nom
        return Departement.LABELS.get(self.departement, self.departement or '—')

    def __repr__(self):
        return f'<Operation {self.id} {self.type_operation} {self.montant}>'


# --------------------------------------------------------------------------
# Journal d'audit (traçabilité : qui a créé / modifié / supprimé quoi)
# --------------------------------------------------------------------------

class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(30), nullable=False)   # creation / modification / suppression
    objet_type = db.Column(db.String(50), nullable=False)
    objet_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    date_action = db.Column(db.DateTime, default=datetime.utcnow)

    utilisateur = db.relationship('User')

    @staticmethod
    def journaliser(user, action, objet_type, objet_id, details=''):
        db.session.add(Audit(
            user_id=user.id if user else None,
            action=action,
            objet_type=objet_type,
            objet_id=objet_id,
            details=details,
        ))

    def __repr__(self):
        return f'<Audit {self.action} {self.objet_type}#{self.objet_id}>'


# ==========================================================================
#  POSIGES V1 — GESTION COMMERCIALE
#
#  Modules ajoutés : profil de facturation, catalogue produits/services,
#  documents commerciaux (proforma et reçu), fournisseurs, mouvements de
#  stock et rapports du cabinet.
#
#  Principe directeur : l'entreprise accompagnée saisit le minimum et
#  l'application déduit le reste. Un reçu encaissé crée automatiquement
#  l'écriture au journal ; une vente livrée décrémente automatiquement
#  le stock. Le gérant de TPE n'a jamais à faire deux fois la même chose.
# ==========================================================================


class NatureArticle:
    """Un service n'a pas de stock : c'est la seule différence structurante."""
    PRODUIT = 'produit'
    SERVICE = 'service'

    CHOIX = [PRODUIT, SERVICE]
    LABELS = {PRODUIT: 'Produit', SERVICE: 'Service'}


class TypeDocument:
    """Les trois pièces émises par l'entreprise.

    FACTURE  : facture de vente. Elle constate le chiffre d'affaires.
    PROFORMA : devis ; ne constate rien tant qu'elle n'est pas acceptée.
    RECU     : reçu de paiement. Il constate l'encaissement réel.

    La distinction facture / reçu est celle rappelée par le cabinet : une
    facture de 1 000 000 réglée à hauteur de 500 000 donne 1 000 000 de
    chiffre d'affaires et 500 000 de trésorerie.
    """
    FACTURE = 'facture'
    PROFORMA = 'proforma'
    RECU = 'recu'

    CHOIX = [FACTURE, PROFORMA, RECU]
    LABELS = {FACTURE: 'Facture', PROFORMA: 'Facture proforma',
              RECU: 'Reçu de paiement'}
    TITRES = {FACTURE: 'FACTURE', PROFORMA: 'FACTURE PROFORMA',
              RECU: 'REÇU DE PAIEMENT'}


class StatutDocument:
    ATTENTE = 'attente'          # proforma émise, non réglée
    PARTIEL = 'partiel'          # acompte reçu
    REGLE = 'regle'              # intégralement payé
    ANNULE = 'annule'

    CHOIX = [ATTENTE, PARTIEL, REGLE, ANNULE]
    LABELS = {
        ATTENTE: 'En attente',
        PARTIEL: 'Partiellement payé',
        REGLE: 'Réglé',
        ANNULE: 'Annulé',
    }


class ConditionVente:
    COMPTANT = 'comptant'
    CREDIT = 'credit'

    CHOIX = [COMPTANT, CREDIT]
    LABELS = {COMPTANT: 'Vente au comptant', CREDIT: 'Vente à crédit'}


class TypeMouvement:
    ENTREE = 'entree'
    SORTIE = 'sortie'
    AJUSTEMENT = 'ajustement'

    CHOIX = [ENTREE, SORTIE, AJUSTEMENT]
    LABELS = {ENTREE: 'Entrée', SORTIE: 'Sortie', AJUSTEMENT: 'Ajustement'}


CATEGORIES_ARTICLES = [
    'Marchandises', 'Matières premières', 'Prestation de services',
    'Conseil', 'Formation', 'Transport', 'Location', 'Maintenance', 'Divers',
]

UNITES = ['pièce', 'carton', 'kg', 'litre', 'sac', 'mètre', 'heure', 'jour', 'forfait']


# --------------------------------------------------------------------------
# Profil de facturation — l'en-tête des documents émis
# --------------------------------------------------------------------------

class ProfilFacturation(db.Model):
    """Informations qui apparaissent en tête de chaque proforma et reçu.

    Renseignées une seule fois par l'entreprise, puis reportées
    automatiquement sur tous ses documents : le gérant de TPE ne ressaisit
    jamais ses coordonnées.
    """
    id = db.Column(db.Integer, primary_key=True)
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'), unique=True)

    raison_sociale = db.Column(db.String(150))
    adresse = db.Column(db.String(255))
    ville = db.Column(db.String(100))
    telephone = db.Column(db.String(60))
    email = db.Column(db.String(120))
    site_web = db.Column(db.String(150))

    # Identifiants légaux ivoiriens (facultatifs : beaucoup de TPE n'en ont pas)
    rccm = db.Column(db.String(80))
    compte_contribuable = db.Column(db.String(80))
    regime_tva = db.Column(db.Boolean, default=False, nullable=False)
    taux_tva = db.Column(db.Float, default=18.0)

    # Coordonnées bancaires — bloc « Informations bancaires » du modèle de
    # facture fourni par le cabinet.
    banque = db.Column(db.String(120))
    numero_compte = db.Column(db.String(120))       # IBAN ou n° de compte
    beneficiaire = db.Column(db.String(150))        # client bénéficiaire
    mobile_money = db.Column(db.String(120))

    # Images reprises sur les documents : logo en en-tête, signature ou
    # cachet sous la mention « LA DIRECTION », nom du signataire.
    logo = db.Column(db.String(400))
    signature = db.Column(db.String(400))
    signataire = db.Column(db.String(150))

    # Ligne légale centrée en pied de page (RCCM, CC, téléphones, e-mail).
    # Composée automatiquement si elle n'est pas saisie.
    pied_legal = db.Column(db.String(400))
    mention_pied = db.Column(db.String(400),
                             default="Merci de votre confiance.")

    entreprise = db.relationship('Entreprise', backref=db.backref(
        'profil_facturation', uselist=False))

    def est_complet(self):
        return bool(self.raison_sociale and (self.telephone or self.email))

    def ligne_pied_legal(self):
        """Bandeau légal du bas de page, tel qu'il figure sur les modèles.

        Exemple : « Abidjan (RCI) — Cocody · RCCM N° CI-ABJ-2019-B-20523 /
        CC N° 1955625 S · Tél. : +225 07 08 061 785 — Email : contact@… »
        """
        if self.pied_legal:
            return self.pied_legal

        lieu = ' — '.join(x for x in (self.ville, self.adresse) if x)
        identifiants = ' / '.join(
            x for x in (f'RCCM N° {self.rccm}' if self.rccm else None,
                        f'CC N° {self.compte_contribuable}'
                        if self.compte_contribuable else None) if x)
        contacts = ' — '.join(
            x for x in (f'Tél. : {self.telephone}' if self.telephone else None,
                        f'Email : {self.email}' if self.email else None) if x)
        return ' · '.join(x for x in (lieu, identifiants, contacts) if x)

    def __repr__(self):
        return f'<ProfilFacturation {self.raison_sociale}>'


# --------------------------------------------------------------------------
# Paramètres du cabinet AK World
# --------------------------------------------------------------------------

class ParametresCabinet(db.Model):
    """Identité du cabinet : logo, coordonnées et signature.

    Ligne unique (identifiant 1). Ces informations habillent les rapports
    d'accompagnement et les documents émis par le cabinet lui-même, de la
    même manière que le profil de facturation habille ceux d'un client.
    """
    id = db.Column(db.Integer, primary_key=True)

    raison_sociale = db.Column(db.String(180), default='AK World Business Services')
    slogan = db.Column(db.String(200), default='Assistance & conseil en gestion')
    adresse = db.Column(db.String(255))
    ville = db.Column(db.String(100))
    telephone = db.Column(db.String(100))
    email = db.Column(db.String(150))
    site_web = db.Column(db.String(150))
    rccm = db.Column(db.String(80))
    compte_contribuable = db.Column(db.String(80))
    banque = db.Column(db.String(120))
    numero_compte = db.Column(db.String(120))

    logo = db.Column(db.String(400))
    logo_fdfp = db.Column(db.String(400))
    signature = db.Column(db.String(400))
    signataire = db.Column(db.String(150))
    pied_legal = db.Column(db.String(400))

    # Préambule des rapports (4 premières pages, façon rapport du cabinet).
    #   Ces textes sont partagés par tous les rapports et modifiables dans
    #   « Paramètres ». Une ligne par élément là où c'est une liste.
    type_rapport = db.Column(db.String(200))          # titre de couverture
    entete_pages = db.Column(db.String(300))          # bandeau haut des pages
    domaines_activite = db.Column(db.Text)            # couverture : liste
    distinctions = db.Column(db.Text)                 # couverture : liste
    contacts_referent = db.Column(db.Text)            # couverture : contacts
    presentation = db.Column(db.Text)                 # page « Présentation »
    habilitations_fdfp = db.Column(db.Text)           # page FDFP : liste
    preambule_initialise = db.Column(db.Boolean, default=False, nullable=False)
    preambule_version = db.Column(db.Integer, default=0, nullable=False)

    # Version du schéma de données déjà appliquée à cette base. Elle empêche
    # les reprises de données de se rejouer : la reprise des rôles, par
    # exemple, renommerait à tort les comptes créés depuis.
    version_donnees = db.Column(db.Integer, default=0, nullable=False)

    date_modification = db.Column(db.DateTime, onupdate=datetime.utcnow)

    def ligne_pied_legal(self):
        if self.pied_legal:
            return self.pied_legal
        lieu = ' — '.join(x for x in (self.ville, self.adresse) if x)
        identifiants = ' / '.join(
            x for x in (f'RCCM N° {self.rccm}' if self.rccm else None,
                        f'CC N° {self.compte_contribuable}'
                        if self.compte_contribuable else None) if x)
        contacts = ' — '.join(
            x for x in (f'Tél. : {self.telephone}' if self.telephone else None,
                        f'Email : {self.email}' if self.email else None) if x)
        return ' · '.join(x for x in (lieu, identifiants, contacts) if x)

    def __repr__(self):
        return f'<ParametresCabinet {self.raison_sociale}>'


# --------------------------------------------------------------------------
# Fournisseur — symétrique du client
# --------------------------------------------------------------------------

class Fournisseur(db.Model):
    """Fournisseurs de l'entreprise accompagnée (et non du cabinet)."""
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    contact = db.Column(db.String(100))
    email = db.Column(db.String(120))
    adresse = db.Column(db.String(255))
    localisation = db.Column(db.String(150))
    specialite = db.Column(db.String(150))
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    entreprise = db.relationship('Entreprise', backref='fournisseurs')

    def __repr__(self):
        return f'<Fournisseur {self.nom}>'


# --------------------------------------------------------------------------
# Catalogue des produits et services
# --------------------------------------------------------------------------

class FamilleProduit(db.Model):
    """Famille — ou catégorie — de produits et de services.

    Le compte d'exploitation ne présente pas un chiffre d'affaires global
    mais un chiffre d'affaires PAR FAMILLE. L'exemple donné par le cabinet :
    on ne saisit pas « Rottweiler de 2 mois » comme rubrique de chiffre
    d'affaires, on rattache cet article à la famille « Canins », et c'est la
    famille qui devient une ligne du compte d'exploitation.
    """
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(300))
    ordre = db.Column(db.Integer, default=0)
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    entreprise = db.relationship('Entreprise', backref='familles')

    @property
    def code_rubrique(self):
        """Code stocké dans `Operation.categorie` pour cette famille."""
        return f'{PREFIXE_CA}{self.id}'

    def __repr__(self):
        return f'<FamilleProduit {self.nom}>'


class Article(db.Model):
    """Produit ou service vendu par l'entreprise.

    Le stock n'est suivi que pour les produits ; un service n'a ni
    quantité ni seuil d'alerte.
    """
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50))
    designation = db.Column(db.String(200), nullable=False)
    nature = db.Column(db.String(20), nullable=False, default=NatureArticle.PRODUIT)
    categorie = db.Column(db.String(100))
    famille_id = db.Column(db.Integer, db.ForeignKey('famille_produit.id'))
    description = db.Column(db.String(400))

    prix_vente = db.Column(db.Float, default=0.0, nullable=False)
    prix_achat = db.Column(db.Float, default=0.0)
    unite = db.Column(db.String(30), default='pièce')

    # Stock — ignoré pour les services
    quantite_stock = db.Column(db.Float, default=0.0)
    seuil_alerte = db.Column(db.Float, default=0.0)

    actif = db.Column(db.Boolean, default=True, nullable=False)
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    entreprise = db.relationship('Entreprise', backref='articles')
    famille = db.relationship('FamilleProduit', backref='articles')

    @property
    def code_rubrique(self):
        """Rubrique de chiffre d'affaires alimentée par la vente de cet article."""
        return self.famille.code_rubrique if self.famille else CA_NON_VENTILE

    @property
    def suit_stock(self):
        return self.nature == NatureArticle.PRODUIT

    @property
    def en_alerte(self):
        """Vrai si le stock est descendu au seuil d'alerte ou en dessous."""
        if not self.suit_stock or not self.seuil_alerte:
            return False
        return (self.quantite_stock or 0) <= self.seuil_alerte

    @property
    def en_rupture(self):
        return self.suit_stock and (self.quantite_stock or 0) <= 0

    @property
    def valeur_stock(self):
        if not self.suit_stock:
            return 0.0
        return (self.quantite_stock or 0) * (self.prix_achat or self.prix_vente or 0)

    def __repr__(self):
        return f'<Article {self.designation}>'


# --------------------------------------------------------------------------
# Documents commerciaux : proforma et reçu
# --------------------------------------------------------------------------

class Document(db.Model):
    """Facture, facture proforma ou reçu émis par l'entreprise à un client."""
    id = db.Column(db.Integer, primary_key=True)
    type_document = db.Column(db.String(20), nullable=False)
    numero = db.Column(db.String(50), unique=True, nullable=False)

    # Références de l'en-tête des modèles remis par le cabinet :
    # « N° de Commande » et « Catégorie de la transaction », distincts du
    # numéro de facture ou de reçu.
    numero_commande = db.Column(db.String(60))
    categorie_transaction = db.Column(db.String(120))

    date_document = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    heure_document = db.Column(db.String(10))     # « 18H13 » sur le modèle
    date_echeance = db.Column(db.Date)

    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))

    condition_vente = db.Column(db.String(20), default=ConditionVente.COMPTANT)
    statut = db.Column(db.String(20), default=StatutDocument.ATTENTE)

    montant_ht = db.Column(db.Float, default=0.0)
    montant_tva = db.Column(db.Float, default=0.0)
    montant_ttc = db.Column(db.Float, default=0.0)
    montant_paye = db.Column(db.Float, default=0.0)

    mode_paiement = db.Column(db.String(30))
    reference_paiement = db.Column(db.String(100))
    note = db.Column(db.String(500))

    # Les écritures engendrées par ce document sont retrouvées par
    # `Operation.document_id` : un document en produit plusieurs — une par
    # famille de produits, plus une par encaissement.
    stock_deduit = db.Column(db.Boolean, default=False, nullable=False)

    cree_par_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, onupdate=datetime.utcnow)

    entreprise = db.relationship('Entreprise', backref='documents')
    client = db.relationship('Client', backref='documents')
    cree_par = db.relationship('User')
    ecritures = db.relationship('Operation', backref='document',
                                foreign_keys='Operation.document_id')
    lignes = db.relationship('LigneDocument', backref='document',
                             cascade='all, delete-orphan',
                             order_by='LigneDocument.id')

    @property
    def comptabilise(self):
        """Vrai si le document a engendré au moins une écriture au journal."""
        return bool(self.ecritures)

    @property
    def reste_a_payer(self):
        return max(0.0, (self.montant_ttc or 0) - (self.montant_paye or 0))

    @property
    def est_regle(self):
        return self.reste_a_payer <= 0.01

    def recalculer(self, taux_tva=0.0):
        """Recalcule les totaux à partir des lignes."""
        self.montant_ht = sum(l.total_ligne for l in self.lignes)
        self.montant_tva = round(self.montant_ht * (taux_tva or 0) / 100, 2)
        self.montant_ttc = round(self.montant_ht + self.montant_tva, 2)
        return self.montant_ttc

    def actualiser_statut(self):
        if self.statut == StatutDocument.ANNULE:
            return
        if self.est_regle and (self.montant_ttc or 0) > 0:
            self.statut = StatutDocument.REGLE
        elif (self.montant_paye or 0) > 0:
            self.statut = StatutDocument.PARTIEL
        else:
            self.statut = StatutDocument.ATTENTE

    def __repr__(self):
        return f'<Document {self.numero}>'


class LigneDocument(db.Model):
    """Une ligne de proforma ou de reçu."""
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'))

    designation = db.Column(db.String(200), nullable=False)
    quantite = db.Column(db.Float, default=1.0, nullable=False)
    unite = db.Column(db.String(30), default='pièce')
    prix_unitaire = db.Column(db.Float, default=0.0, nullable=False)

    # Colonne « Montant payé » du modèle de reçu. Vide = intégralement payé.
    # `remise` remplace le montant par la mention « (Remise) », comme sur le
    # reçu fourni par le cabinet où une prestation est offerte au client.
    montant_paye = db.Column(db.Float)
    remise = db.Column(db.Boolean, default=False, nullable=False)

    article = db.relationship('Article')

    @property
    def total_ligne(self):
        return round((self.quantite or 0) * (self.prix_unitaire or 0), 2)

    @property
    def paye_ligne(self):
        """Montant réellement encaissé sur cette ligne."""
        if self.remise:
            return 0.0
        if self.montant_paye is None:
            return self.total_ligne
        return round(self.montant_paye, 2)

    def __repr__(self):
        return f'<LigneDocument {self.designation}>'


# --------------------------------------------------------------------------
# Mouvements de stock
# --------------------------------------------------------------------------

class MouvementStock(db.Model):
    """Historique des entrées et sorties de stock, avec justification."""
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'))

    type_mouvement = db.Column(db.String(20), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    quantite_apres = db.Column(db.Float)
    motif = db.Column(db.String(200))

    fournisseur_id = db.Column(db.Integer, db.ForeignKey('fournisseur.id'))
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'))

    date_mouvement = db.Column(db.Date, default=datetime.utcnow)
    cree_par_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    article = db.relationship('Article', backref='mouvements')
    fournisseur = db.relationship('Fournisseur')
    document = db.relationship('Document')
    cree_par = db.relationship('User')

    def __repr__(self):
        return f'<MouvementStock {self.type_mouvement} {self.quantite}>'


# --------------------------------------------------------------------------
# Rapports du cabinet
# --------------------------------------------------------------------------

class Rapport(db.Model):
    """Rapport d'accompagnement rédigé par le cabinet pour une entreprise.

    Rédigé dans l'application à partir d'un modèle pré-rempli avec les
    chiffres de la période. Reste modifiable tant qu'il n'est pas publié.
    Une fois publié, le client le voit à sa connexion.
    """
    id = db.Column(db.Integer, primary_key=True)
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'), nullable=False)

    titre = db.Column(db.String(200), nullable=False)
    periode = db.Column(db.String(100))
    annee = db.Column(db.Integer)

    # Sections modifiables, pré-remplies à la création.
    #   Structure reprise du modèle de rapport du cabinet :
    #   Contexte · Méthodologie · Constats par cycle · Analyse ·
    #   Recommandations · Conclusion. Toutes restent libres à la rédaction.
    synthese = db.Column(db.Text)          # aperçu chiffré (auto)
    contexte = db.Column(db.Text)
    methodologie = db.Column(db.Text)
    cycle_administratif = db.Column(db.Text)
    cycle_financier = db.Column(db.Text)
    cycle_comptable = db.Column(db.Text)
    cycle_marketing = db.Column(db.Text)
    constats = db.Column(db.Text)          # conservé (rapports historiques)
    analyse = db.Column(db.Text)
    recommandations = db.Column(db.Text)
    conclusion = db.Column(db.Text)

    publie = db.Column(db.Boolean, default=False, nullable=False)
    date_publication = db.Column(db.DateTime)

    cree_par_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, onupdate=datetime.utcnow)

    entreprise = db.relationship('Entreprise', backref='rapports')
    cree_par = db.relationship('User')

    def __repr__(self):
        return f'<Rapport {self.titre}>'


class SectionRapport(db.Model):
    """Section modifiable d'un rapport : un grand titre et son corps.

    Les rapports sont pré-remplis avec les sections du modèle du cabinet
    (Aperçu chiffré, Contexte, Méthodologie, cycles, Analyse…), mais le
    consultant peut renommer les titres, réécrire les corps, en ajouter ou
    en supprimer. C'est ce qui rend le plan du rapport entièrement libre.
    """
    id = db.Column(db.Integer, primary_key=True)
    rapport_id = db.Column(db.Integer, db.ForeignKey('rapport.id'), nullable=False)
    ordre = db.Column(db.Integer, default=0, nullable=False)
    titre = db.Column(db.String(200), nullable=False)
    corps = db.Column(db.Text)

    rapport = db.relationship(
        'Rapport',
        backref=db.backref('sections',
                           order_by='SectionRapport.ordre, SectionRapport.id',
                           cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<SectionRapport {self.titre!r}>'


# ==========================================================================
#  BUDGETS — la colonne « Prévu » des deux comptes
#
#  Le gérant ouvre « Budget », choisit s'il travaille sur l'exploitation ou
#  sur la trésorerie, et remplit un tableau Mois 1 → Mois 12 : une seule
#  colonne par mois, un montant par rubrique.
#
#  Ce montant devient ensuite la colonne « Prévu » du compte correspondant.
#  La colonne « Réalisé » est calculée en direct depuis le journal, et
#  l'écart vaut toujours Réalisé − Prévu.
# ==========================================================================

class TypeBudget:
    EXPLOITATION = 'exploitation'
    TRESORERIE = 'tresorerie'

    CHOIX = [EXPLOITATION, TRESORERIE]
    LABELS = {
        EXPLOITATION: "Budget d'exploitation",
        TRESORERIE: 'Budget de trésorerie',
    }
    DESCRIPTIONS = {
        EXPLOITATION: "Chiffre d'affaires prévu par famille de produits, "
                      "charges variables et charges fixes prévues.",
        TRESORERIE: 'Encaissements et décaissements prévus — la liquidité '
                    'réelle attendue, mois par mois.',
    }


class Budget(db.Model):
    """Un budget = une entreprise, un exercice, un type de compte."""
    id = db.Column(db.Integer, primary_key=True)
    entreprise_id = db.Column(db.Integer, db.ForeignKey('entreprise.id'),
                              nullable=False)
    annee = db.Column(db.Integer, nullable=False)
    type_budget = db.Column(db.String(20), nullable=False,
                            default=TypeBudget.EXPLOITATION)

    # Solde de trésorerie au 1er janvier — ligne (C) du compte de trésorerie.
    # Ignoré pour un budget d'exploitation.
    solde_initial = db.Column(db.Float, default=0.0)

    cree_par_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, onupdate=datetime.utcnow)

    entreprise = db.relationship('Entreprise', backref='budgets')
    cree_par = db.relationship('User')
    lignes = db.relationship('LigneBudget', backref='budget',
                             cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('entreprise_id', 'annee', 'type_budget',
                            name='budget_unique_par_exercice'),
    )

    def montants(self):
        """Dictionnaire {(rubrique, mois): montant} pour un accès direct."""
        return {(l.rubrique, l.mois): l.montant or 0.0 for l in self.lignes}

    def total_rubrique(self, rubrique):
        return sum(l.montant or 0.0 for l in self.lignes
                   if l.rubrique == rubrique)

    def est_renseigne(self):
        return any((l.montant or 0) for l in self.lignes)

    def __repr__(self):
        return f'<Budget {self.type_budget} {self.annee}>'


class LigneBudget(db.Model):
    """Montant prévu pour une rubrique et un mois donnés."""
    id = db.Column(db.Integer, primary_key=True)
    budget_id = db.Column(db.Integer, db.ForeignKey('budget.id'), nullable=False)

    rubrique = db.Column(db.String(60), nullable=False)   # code du plan
    mois = db.Column(db.Integer, nullable=False)          # 1 à 12
    montant = db.Column(db.Float, default=0.0, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('budget_id', 'rubrique', 'mois',
                            name='ligne_budget_unique'),
    )

    def __repr__(self):
        return f'<LigneBudget {self.rubrique} M{self.mois} {self.montant}>'
