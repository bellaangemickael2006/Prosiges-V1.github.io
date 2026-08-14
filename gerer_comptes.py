"""
Gestion des comptes utilisateurs en ligne de commande.

À utiliser quand un mot de passe est perdu, quand un compte a disparu, ou
pour créer les vrais comptes lors de la mise en production.

    python gerer_comptes.py liste
        Affiche tous les comptes, leur rôle et leur état.

    python gerer_comptes.py reparer
        Recrée les comptes de démonstration manquants et remet leurs mots de
        passe d'origine. Ne touche à aucune donnée comptable.

    python gerer_comptes.py motdepasse <email> <nouveau_mot_de_passe>
        Change le mot de passe d'un compte précis.

    python gerer_comptes.py creer <nom> <email> <mot_de_passe> <role> [option]
        Crée un compte.
          role = gerant | responsable | consultant | client
          option = le département (si responsable)
                   ou le nom de l'entreprise (si client)

    python gerer_comptes.py activer <email>
    python gerer_comptes.py desactiver <email>

    python gerer_comptes.py purger-demo
        Supprime les comptes et les données de démonstration.
        À FAIRE avant de mettre le site en production.
"""
import sys

from app import app, initialiser_base
from models import Departement, Entreprise, Operation, Role, User, db

# Comptes de démonstration et leurs mots de passe d'origine
COMPTES_DEMO = [
    # Côté cabinet AK World
    ('Direction AK World', 'cabinet@akworld.com', 'AkWorld2026!', Role.CABINET, None),
    ('Awa Traoré', 'consultant@akworld.com', 'Consultant2026!', Role.CONSULTANT, None),
    ('Yao Kouassi', 'commercial@akworld.com', 'Commercial2026!',
     Role.RESPONSABLE, Departement.COMMERCIAL),
    ('Fatou Diallo', 'comptabilite@akworld.com', 'Comptabilite2026!',
     Role.RESPONSABLE, Departement.COMPTABILITE),
    # Côté entreprises accompagnées : les trois niveaux d'accès
    ('Kouadio Bella', 'bella@jusdebella.ci', 'Bella2026!',
     Role.GERANT, 'Jus de Bella'),
    ('Aya Koffi', 'comptable@jusdebella.ci', 'Comptable2026!',
     Role.COMPTABLE, 'Jus de Bella'),
    ('Sery Gogo', 'caisse@jusdebella.ci', 'Caisse2026!',
     Role.STANDARD, 'Jus de Bella'),
    ('Konan Michel', 'michel@quincaillerie.ci', 'Michel2026!',
     Role.GERANT, 'Quincaillerie Konan'),
]

VERT = '\033[92m'
ROUGE = '\033[91m'
JAUNE = '\033[93m'
FIN = '\033[0m'


def _tableau(utilisateurs):
    print()
    print(f"  {'NOM':<22} {'EMAIL':<30} {'RÔLE':<14} {'PÉRIMÈTRE':<22} ÉTAT")
    print("  " + "-" * 96)
    for u in utilisateurs:
        if u.est_responsable:
            perimetre = Departement.LABELS.get(u.departement, '—')
        elif u.est_client:
            perimetre = u.entreprise.nom if u.entreprise else '(aucune entreprise !)'
        elif u.est_consultant:
            noms = [e.nom for e in u.entreprises_suivies]
            perimetre = ', '.join(noms) if noms else '(aucune assignée)'
        else:
            perimetre = 'Toutes les entreprises'
        etat = f"{VERT}actif{FIN}" if u.actif else f"{ROUGE}désactivé{FIN}"
        print(f"  {u.nom:<22} {u.email:<30} {u.role:<14} {perimetre[:22]:<22} {etat}")
    print()


def commande_liste():
    with app.app_context():
        utilisateurs = User.query.order_by(User.role, User.nom).all()
        if not utilisateurs:
            print(f"{ROUGE}Aucun compte dans la base.{FIN} "
                  "Lancez : python gerer_comptes.py reparer")
            return
        _tableau(utilisateurs)
        print(f"  {len(utilisateurs)} compte(s) — "
              f"{Operation.query.count()} opération(s) enregistrée(s).")


def commande_reparer():
    """Recrée les comptes de démonstration manquants et réinitialise leurs
    mots de passe. Les données comptables ne sont jamais touchées."""
    with app.app_context():
        initialiser_base()
        crees, reinitialises, ignores = [], [], []

        for nom, email, mdp, role, option in COMPTES_DEMO:
            utilisateur = User.query.filter_by(email=email).first()

            if utilisateur is None:
                utilisateur = User(nom=nom, email=email, role=role)
                if role == Role.RESPONSABLE:
                    utilisateur.departement = option
                elif role in Role.COTE_ENTREPRISE:
                    entreprise = Entreprise.query.filter_by(nom=option).first()
                    if entreprise is None:
                        ignores.append(f"{email} (entreprise « {option} » absente)")
                        continue
                    utilisateur.entreprise_id = entreprise.id
                utilisateur.set_password(mdp)
                db.session.add(utilisateur)
                crees.append(email)
            else:
                utilisateur.set_password(mdp)
                utilisateur.actif = True
                reinitialises.append(email)

            # Un consultant sans entreprise assignée ne voit rien : on rattache
            if role == Role.CONSULTANT:
                db.session.flush()
                if not utilisateur.entreprises_suivies:
                    for entreprise in Entreprise.query.all():
                        utilisateur.entreprises_suivies.append(entreprise)

        db.session.commit()

        print()
        if crees:
            print(f"  {VERT}Créés{FIN}          : {', '.join(crees)}")
        if reinitialises:
            print(f"  {VERT}Réinitialisés{FIN}  : {', '.join(reinitialises)}")
        if ignores:
            print(f"  {JAUNE}Ignorés{FIN}        : {', '.join(ignores)}")
            print("                   → lancez d'abord : python seed.py")

        print()
        print("  " + "=" * 62)
        print("  IDENTIFIANTS RÉTABLIS")
        print("  " + "=" * 62)
        for nom, email, mdp, role, _ in COMPTES_DEMO:
            print(f"  {Role.LABELS[role]:<28} {email:<28} {mdp}")
        print("  " + "=" * 62)


def commande_motdepasse(email, nouveau):
    if len(nouveau) < 8:
        print(f"{ROUGE}Le mot de passe doit contenir au moins 8 caractères.{FIN}")
        return 1
    with app.app_context():
        utilisateur = User.query.filter_by(email=email.lower()).first()
        if utilisateur is None:
            print(f"{ROUGE}Aucun compte avec l'email {email}.{FIN}")
            return 1
        utilisateur.set_password(nouveau)
        utilisateur.actif = True
        db.session.commit()
        print(f"{VERT}Mot de passe modifié pour {utilisateur.nom} ({email}).{FIN}")
    return 0


def commande_creer(nom, email, mdp, role, option=None):
    if role not in Role.CHOIX:
        print(f"{ROUGE}Rôle invalide.{FIN} Choix : {', '.join(Role.CHOIX)}")
        return 1
    if len(mdp) < 8:
        print(f"{ROUGE}Le mot de passe doit contenir au moins 8 caractères.{FIN}")
        return 1

    with app.app_context():
        if User.query.filter_by(email=email.lower()).first():
            print(f"{ROUGE}Un compte existe déjà avec cet email.{FIN}")
            return 1

        utilisateur = User(nom=nom, email=email.lower(), role=role)

        if role == Role.RESPONSABLE:
            if option not in Departement.CHOIX:
                print(f"{ROUGE}Département requis.{FIN} "
                      f"Choix : {', '.join(Departement.CHOIX)}")
                return 1
            utilisateur.departement = option

        elif role in Role.COTE_ENTREPRISE:
            entreprise = Entreprise.query.filter_by(nom=option).first()
            if entreprise is None:
                noms = [e.nom for e in Entreprise.query.all()]
                print(f"{ROUGE}Entreprise « {option} » introuvable.{FIN}")
                print(f"  Entreprises existantes : {', '.join(noms) or 'aucune'}")
                return 1
            utilisateur.entreprise_id = entreprise.id

        utilisateur.set_password(mdp)
        db.session.add(utilisateur)
        db.session.commit()
        print(f"{VERT}Compte créé : {nom} ({email}) — {Role.LABELS[role]}{FIN}")
    return 0


def commande_basculer(email, actif):
    with app.app_context():
        utilisateur = User.query.filter_by(email=email.lower()).first()
        if utilisateur is None:
            print(f"{ROUGE}Aucun compte avec l'email {email}.{FIN}")
            return 1
        utilisateur.actif = actif
        db.session.commit()
        etat = 'activé' if actif else 'désactivé'
        print(f"{VERT}Compte {etat} : {utilisateur.nom}{FIN}")
    return 0


def commande_purger_demo():
    """Retire les comptes et données de démonstration avant mise en production."""
    emails = [c[1] for c in COMPTES_DEMO if c[3] != Role.CABINET]
    noms_entreprises = ['Jus de Bella', 'Quincaillerie Konan']

    print()
    print(f"{JAUNE}  Cette commande va supprimer :{FIN}")
    print(f"    · les comptes de démonstration : {', '.join(emails)}")
    print(f"    · les entreprises : {', '.join(noms_entreprises)}")
    print("    · toutes leurs opérations et leurs clients")
    print()
    print("    Le compte gérant est conservé.")
    print()
    reponse = input("  Confirmer ? Tapez OUI en majuscules : ").strip()
    if reponse != 'OUI':
        print("  Annulé.")
        return 0

    with app.app_context():
        for email in emails:
            utilisateur = User.query.filter_by(email=email).first()
            if utilisateur:
                db.session.delete(utilisateur)

        for nom in noms_entreprises:
            entreprise = Entreprise.query.filter_by(nom=nom).first()
            if entreprise:
                Operation.query.filter_by(entreprise_id=entreprise.id).delete()
                from models import Client
                Client.query.filter_by(entreprise_id=entreprise.id).delete()
                for u in User.query.filter_by(entreprise_id=entreprise.id).all():
                    db.session.delete(u)
                db.session.delete(entreprise)

        # Opérations internes de démonstration
        Operation.query.filter(Operation.entreprise_id.is_(None)).delete()
        db.session.commit()

        restants = User.query.count()
        print(f"{VERT}  Purge effectuée. {restants} compte(s) restant(s).{FIN}")
        print(f"{JAUNE}  Changez maintenant le mot de passe du gérant :{FIN}")
        print("    python gerer_comptes.py motdepasse gerant@akworld.com <votre_mot_de_passe>")
    return 0


def principal():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    commande = sys.argv[1]
    args = sys.argv[2:]

    if commande == 'liste':
        commande_liste()
        return 0
    if commande == 'reparer':
        commande_reparer()
        return 0
    if commande == 'motdepasse' and len(args) == 2:
        return commande_motdepasse(args[0], args[1])
    if commande == 'creer' and len(args) >= 4:
        return commande_creer(args[0], args[1], args[2], args[3],
                              args[4] if len(args) > 4 else None)
    if commande == 'activer' and len(args) == 1:
        return commande_basculer(args[0], True)
    if commande == 'desactiver' and len(args) == 1:
        return commande_basculer(args[0], False)
    if commande == 'purger-demo':
        return commande_purger_demo()

    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(principal())
