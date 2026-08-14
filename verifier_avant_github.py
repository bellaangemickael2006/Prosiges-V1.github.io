"""
Contrôle de sécurité à lancer AVANT de publier le projet sur GitHub.

    python verifier_avant_github.py

Vérifie qu'aucun secret ne risque de partir en ligne : clé Google, mots de
passe, base de données, fichier .env. Une fois un secret publié sur GitHub —
même dans un dépôt privé, même supprimé ensuite — il faut le considérer
comme compromis et le régénérer. Ce script évite cette situation.
"""
import os
import re
import subprocess
import sys

RACINE = os.path.abspath(os.path.dirname(__file__))

VERT = '\033[92m'
ROUGE = '\033[91m'
JAUNE = '\033[93m'
GRAS = '\033[1m'
FIN = '\033[0m'

# Fichiers qui ne doivent JAMAIS partir sur GitHub
INTERDITS = [
    ('credentials.json', 'Clé privée du compte de service Google'),
    ('.env', 'Clé secrète de session et configuration'),
    ('ak_world.db', 'Base de données (contient des mots de passe hachés)'),
    ('token.json', 'Jeton d\'authentification Google'),
]

# Dossiers à ne pas versionner (volumineux ou régénérables)
DOSSIERS_EXCLUS = ['venv', '.venv', '__pycache__', 'uploads', 'instance']

# Motifs de secrets à chercher dans le code
MOTIFS = [
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----', 'Clé privée en clair'),
    (r'"private_key"\s*:', 'Bloc de clé de compte de service'),
    (r'AIza[0-9A-Za-z_-]{35}', 'Clé d\'API Google'),
    (r'SECRET_KEY\s*=\s*[\'"][0-9a-f]{32,}[\'"]', 'Clé secrète codée en dur'),
]

EXTENSIONS_ANALYSEES = {'.py', '.html', '.js', '.txt', '.md', '.yaml', '.yml',
                        '.json', '.cfg', '.bat', '.sh'}

problemes = []
avertissements = []


def titre(texte):
    print(f"\n{GRAS}{texte}{FIN}")
    print("─" * 66)


def controler_gitignore():
    titre("1. Le fichier .gitignore protège-t-il les secrets ?")
    chemin = os.path.join(RACINE, '.gitignore')

    if not os.path.exists(chemin):
        problemes.append("Aucun fichier .gitignore : TOUT partirait sur GitHub.")
        print(f"  {ROUGE}✗ .gitignore absent{FIN}")
        return

    contenu = open(chemin, encoding='utf-8').read()
    attendus = ['.env', 'credentials.json', '*.db', 'venv', '__pycache__', 'uploads']

    for motif in attendus:
        if motif in contenu:
            print(f"  {VERT}✓{FIN} {motif}")
        else:
            problemes.append(f".gitignore n'exclut pas « {motif} ».")
            print(f"  {ROUGE}✗ {motif} — NON exclu{FIN}")


def controler_fichiers_sensibles():
    titre("2. Fichiers sensibles présents dans le dossier")
    for nom, description in INTERDITS:
        chemin = os.path.join(RACINE, nom)
        if os.path.exists(chemin):
            print(f"  {JAUNE}!{FIN} {nom} présent — {description}")
            print(f"      → doit être exclu par .gitignore (vérifié ci-dessus)")
        else:
            print(f"  {VERT}·{FIN} {nom} absent")


def controler_suivi_git():
    """Le contrôle décisif : ces fichiers sont-ils réellement suivis par Git ?"""
    titre("3. Que Git s'apprête-t-il réellement à publier ?")

    if not os.path.exists(os.path.join(RACINE, '.git')):
        print(f"  {JAUNE}Dépôt Git non encore initialisé.{FIN}")
        print("  Ce contrôle deviendra actif après « git init ».")
        return

    try:
        sortie = subprocess.run(['git', 'ls-files'], cwd=RACINE,
                                capture_output=True, text=True, timeout=20)
        suivis = sortie.stdout.splitlines()
    except Exception as erreur:
        avertissements.append(f"Impossible d'interroger Git : {erreur}")
        print(f"  {JAUNE}Git non interrogeable.{FIN}")
        return

    dangereux = []
    for fichier in suivis:
        base = os.path.basename(fichier)
        if base in [n for n, _ in INTERDITS] or fichier.endswith('.db'):
            dangereux.append(fichier)
        if any(fichier.startswith(d + '/') for d in DOSSIERS_EXCLUS):
            dangereux.append(fichier)

    if dangereux:
        for fichier in sorted(set(dangereux))[:15]:
            print(f"  {ROUGE}✗ {fichier}{FIN}")
        problemes.append(
            f"{len(set(dangereux))} fichier(s) sensible(s) suivis par Git. "
            "Retirez-les avec : git rm --cached <fichier>")
    else:
        print(f"  {VERT}✓ Aucun fichier sensible suivi par Git "
              f"({len(suivis)} fichiers au total).{FIN}")


def controler_secrets_dans_le_code():
    titre("4. Secrets écrits en dur dans le code")
    trouves = 0

    for dossier, sous_dossiers, fichiers in os.walk(RACINE):
        sous_dossiers[:] = [d for d in sous_dossiers
                            if d not in DOSSIERS_EXCLUS and not d.startswith('.git')]
        for fichier in fichiers:
            if os.path.splitext(fichier)[1] not in EXTENSIONS_ANALYSEES:
                continue
            if fichier in ('verifier_avant_github.py', '.env.example'):
                continue
            chemin = os.path.join(dossier, fichier)
            try:
                contenu = open(chemin, encoding='utf-8', errors='ignore').read()
            except OSError:
                continue
            for motif, description in MOTIFS:
                if re.search(motif, contenu):
                    relatif = os.path.relpath(chemin, RACINE)
                    print(f"  {ROUGE}✗ {relatif} — {description}{FIN}")
                    problemes.append(f"Secret dans {relatif} : {description}")
                    trouves += 1

    if trouves == 0:
        print(f"  {VERT}✓ Aucun secret détecté dans le code.{FIN}")


def controler_configuration_production():
    titre("5. Configuration prête pour la production")
    for nom, description in [
        ('render.yaml', 'déploiement automatique Render'),
        ('requirements-production.txt', 'dépendances serveur'),
        ('Procfile', 'commande de démarrage'),
    ]:
        existe = os.path.exists(os.path.join(RACINE, nom))
        marque = f"{VERT}✓{FIN}" if existe else f"{ROUGE}✗{FIN}"
        print(f"  {marque} {nom} — {description}")
        if not existe:
            problemes.append(f"{nom} manquant.")

    chemin_env = os.path.join(RACINE, '.env')
    if os.path.exists(chemin_env):
        contenu = open(chemin_env, encoding='utf-8', errors='ignore').read()
        if 'dev-secret' in contenu or 'remplacez' in contenu:
            avertissements.append(
                "Votre .env contient encore une clé secrète d'exemple. "
                "En production, Render en génère une automatiquement.")


def main():
    print()
    print("=" * 66)
    print(f"{GRAS}  CONTRÔLE DE SÉCURITÉ AVANT PUBLICATION SUR GITHUB{FIN}")
    print("=" * 66)

    controler_gitignore()
    controler_fichiers_sensibles()
    controler_suivi_git()
    controler_secrets_dans_le_code()
    controler_configuration_production()

    print()
    print("=" * 66)
    if problemes:
        print(f"{ROUGE}{GRAS}  {len(problemes)} PROBLÈME(S) À CORRIGER AVANT DE PUBLIER{FIN}")
        print("=" * 66)
        for index, probleme in enumerate(problemes, 1):
            print(f"  {index}. {probleme}")
    else:
        print(f"{VERT}{GRAS}  AUCUN PROBLÈME — vous pouvez publier sur GitHub.{FIN}")
        print("=" * 66)

    if avertissements:
        print(f"\n{JAUNE}  Remarques :{FIN}")
        for avertissement in avertissements:
            print(f"    · {avertissement}")

    print()
    print(f"{JAUNE}  Rappel : si votre clé Google a déjà été exposée (envoyée par")
    print(f"  message, copiée dans un document, publiée par erreur), régénérez-la")
    print(f"  dans la console Google Cloud AVANT la mise en ligne.{FIN}")
    print()
    return 1 if problemes else 0


if __name__ == '__main__':
    sys.exit(main())
