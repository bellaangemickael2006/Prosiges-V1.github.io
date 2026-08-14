"""
Configuration automatique exécutée par les lanceurs.

Au premier démarrage :
  1. crée le fichier .env avec une clé secrète générée aléatoirement ;
  2. crée la base de données et le compte gérant ;
  3. propose de charger les données de démonstration.

Aux démarrages suivants, ce script ne fait rien : il détecte que tout est
déjà en place et rend la main immédiatement.
"""
import os
import secrets
import sys

RACINE = os.path.abspath(os.path.dirname(__file__))
CHEMIN_ENV = os.path.join(RACINE, '.env')
CHEMIN_MODELE = os.path.join(RACINE, '.env.example')
MARQUEUR_DEMO = os.path.join(RACINE, '.demo_chargee')


def creer_env():
    """Génère .env avec une vraie clé secrète, à partir du modèle."""
    if os.path.exists(CHEMIN_ENV):
        return False

    cle = secrets.token_hex(32)
    if os.path.exists(CHEMIN_MODELE):
        with open(CHEMIN_MODELE, encoding='utf-8') as fichier:
            contenu = fichier.read()
        contenu = contenu.replace(
            'SECRET_KEY=remplacez-par-une-cle-aleatoire-longue',
            f'SECRET_KEY={cle}')
    else:
        contenu = f"SECRET_KEY={cle}\nNOM_CABINET=AK World\nCOOKIE_SECURE=false\n"

    with open(CHEMIN_ENV, 'w', encoding='utf-8') as fichier:
        fichier.write(contenu)
    print("      → fichier .env créé avec une clé secrète unique")
    return True


def preparer_base():
    """Crée les tables et le compte gérant si nécessaire."""
    from app import app, initialiser_base
    from models import Operation, User

    initialiser_base()

    with app.app_context():
        nombre_utilisateurs = User.query.count()
        nombre_operations = Operation.query.count()
    return nombre_utilisateurs, nombre_operations


def charger_demo():
    """Charge le jeu de démonstration si la base est vide."""
    if os.path.exists(MARQUEUR_DEMO):
        return False
    try:
        from seed import peupler
        peupler()
        with open(MARQUEUR_DEMO, 'w', encoding='utf-8') as fichier:
            fichier.write('demo chargee\n')
        return True
    except Exception as erreur:      # pragma: no cover
        print(f"      ! Données de démonstration non chargées : {erreur}")
        return False


def principal():
    creer_env()

    try:
        utilisateurs, operations = preparer_base()
    except Exception as erreur:
        print(f"\n[X] Impossible de préparer la base de données : {erreur}\n")
        return 1

    # Base vierge (uniquement le gérant, aucune opération) → proposer la démo
    if utilisateurs <= 1 and operations == 0 and not os.path.exists(MARQUEUR_DEMO):
        print("      → base vide : chargement des données de démonstration")
        charger_demo()

    print("      → prêt.")
    return 0


if __name__ == '__main__':
    sys.exit(principal())
