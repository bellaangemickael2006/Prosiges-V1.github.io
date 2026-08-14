"""
Diagnostic de l'intégration Google Drive et Google Sheets.

    python diagnostic_google.py

Contrôle chaque maillon de la chaîne, dans l'ordre, et s'arrête au premier
qui casse en indiquant précisément quoi faire. Chaque test correspond à une
cause d'échec réellement rencontrée.

Aucune donnée n'est modifiée : les écritures de test sont supprimées après
vérification.
"""
import json
import os
import sys

VERT = '\033[92m'
ROUGE = '\033[91m'
JAUNE = '\033[93m'
GRAS = '\033[1m'
FIN = '\033[0m'

etapes_ok = 0
blocages = []


def titre(numero, texte):
    print(f"\n{GRAS}{numero}. {texte}{FIN}")
    print("─" * 68)


def ok(message):
    global etapes_ok
    etapes_ok += 1
    print(f"  {VERT}✓{FIN} {message}")


def echec(message, solution):
    print(f"  {ROUGE}✗ {message}{FIN}")
    blocages.append((message, solution))
    return False


def info(message):
    print(f"    {message}")


def note(message):
    print(f"  {JAUNE}!{FIN} {message}")


# ==========================================================================

def controler_bibliotheques():
    titre(1, "Les bibliothèques Google sont-elles installées ?")
    try:
        import google.oauth2.service_account          # noqa: F401
        import googleapiclient.discovery              # noqa: F401
        ok("google-auth et google-api-python-client sont présents")
        return True
    except ImportError as erreur:
        return echec(
            f"Bibliothèques Google absentes ({erreur})",
            "C'est LA cause la plus fréquente. Le lanceur n'installe pas ces\n"
            "     bibliothèques par défaut, pour que l'installation de base ne\n"
            "     puisse pas échouer. Installez-les :\n\n"
            "         Windows    : .venv\\Scripts\\pip install -r requirements-google.txt\n"
            "         Mac/Linux  : ./venv/bin/pip install -r requirements-google.txt\n\n"
            "     Puis relancez l'application.")


def controler_credentials(config):
    titre(2, "Le fichier credentials.json est-il exploitable ?")
    chemin = config.GOOGLE_CREDENTIALS_FILE

    if not chemin or not os.path.exists(chemin):
        return echec(
            f"Fichier introuvable : {chemin}",
            "Placez le fichier de clé du compte de service à la racine du\n"
            "     projet, sous le nom exact « credentials.json ».\n"
            "     Attention à l'extension : Windows masque parfois « .json »,\n"
            "     et le fichier peut s'appeler credentials.json.json"), None

    ok(f"Fichier trouvé : {chemin}")

    try:
        with open(chemin, encoding='utf-8') as fichier:
            donnees = json.load(fichier)
    except json.JSONDecodeError as erreur:
        return echec(
            f"Le fichier n'est pas un JSON valide ({erreur})",
            "Retéléchargez la clé depuis la console Google Cloud :\n"
            "     IAM et administration → Comptes de service → Clés →\n"
            "     Ajouter une clé → Créer une clé → JSON"), None

    if donnees.get('type') != 'service_account':
        return echec(
            f"Ce n'est pas une clé de compte de service (type = "
            f"{donnees.get('type')})",
            "Vous avez probablement téléchargé un identifiant OAuth client.\n"
            "     Il faut une clé de COMPTE DE SERVICE."), None

    email = donnees.get('client_email', '')
    ok(f"Clé de compte de service valide")
    info(f"Projet         : {donnees.get('project_id')}")
    info(f"Compte service : {GRAS}{email}{FIN}")
    return True, email


def controler_variables(config):
    titre(3, "Les identifiants Drive et Sheets sont-ils renseignés ?")
    dossier = config.GOOGLE_DRIVE_FOLDER_ID
    sheet = config.GOOGLE_SHEET_ID

    if dossier:
        ok(f"GOOGLE_DRIVE_FOLDER_ID = {dossier}")
    else:
        note("GOOGLE_DRIVE_FOLDER_ID est vide")
        info("Les justificatifs resteront stockés localement.")

    if sheet:
        ok(f"GOOGLE_SHEET_ID = {sheet}")
    else:
        note("GOOGLE_SHEET_ID est vide")
        info("La resynchronisation du journal partagé sera impossible.")

    if not dossier and not sheet:
        return echec(
            "Aucun identifiant Google renseigné dans le fichier .env",
            "Ouvrez le fichier .env et complétez :\n"
            "         GOOGLE_DRIVE_FOLDER_ID=...\n"
            "         GOOGLE_SHEET_ID=...\n\n"
            "     L'identifiant d'un dossier Drive se lit dans son URL :\n"
            "         drive.google.com/drive/folders/IDENTIFIANT\n"
            "     Celui d'un Sheet également :\n"
            "         docs.google.com/spreadsheets/d/IDENTIFIANT/edit")
    return True


def controler_authentification(config):
    titre(4, "L'authentification auprès de Google fonctionne-t-elle ?")
    from google.oauth2 import service_account

    scopes = ['https://www.googleapis.com/auth/drive',
              'https://www.googleapis.com/auth/spreadsheets']
    try:
        credentials = service_account.Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE, scopes=scopes)
    except Exception as erreur:
        return echec(f"Clé illisible ({erreur})",
                     "Régénérez la clé depuis la console Google Cloud."), None

    try:
        from google.auth.transport.requests import Request
        credentials.refresh(Request())
        ok("Authentification réussie auprès de Google")
        return True, credentials
    except Exception as erreur:
        message = str(erreur)
        if 'invalid_grant' in message or 'Invalid JWT' in message:
            solution = (
                "La clé a été révoquée ou l'horloge de votre ordinateur est\n"
                "     décalée. Vérifiez la date et l'heure de Windows, puis\n"
                "     régénérez la clé si le problème persiste.")
        else:
            solution = ("Vérifiez votre connexion internet et que le compte de\n"
                        "     service n'a pas été supprimé.")
        return echec(f"Authentification refusée : {message[:120]}", solution), None


def controler_apis(credentials):
    titre(5, "Les API Drive et Sheets sont-elles activées ?")
    from googleapiclient.discovery import build

    resultat = True
    for nom, version, libelle in [('drive', 'v3', 'Google Drive API'),
                                  ('sheets', 'v4', 'Google Sheets API')]:
        try:
            service = build(nom, version, credentials=credentials,
                            cache_discovery=False)
            if nom == 'drive':
                service.files().list(pageSize=1, fields='files(id)').execute()
            ok(f"{libelle} active")
        except Exception as erreur:
            message = str(erreur)
            if 'has not been used' in message or 'disabled' in message.lower():
                resultat = echec(
                    f"{libelle} n'est pas activée",
                    f"Ouvrez console.cloud.google.com → votre projet →\n"
                    f"     APIs et services → Bibliothèque → cherchez\n"
                    f"     « {libelle} » → Activer.\n"
                    f"     Comptez 1 à 2 minutes avant que ce soit effectif.")
            else:
                resultat = echec(f"{libelle} inaccessible : {message[:110]}",
                                 "Vérifiez l'activation de l'API dans la console.")
    return resultat


def controler_dossier_drive(credentials, config, email):
    titre(6, "Le dossier Drive est-il partagé avec le compte de service ?")
    if not config.GOOGLE_DRIVE_FOLDER_ID:
        note("Aucun dossier configuré — contrôle ignoré")
        return True

    from googleapiclient.discovery import build
    service = build('drive', 'v3', credentials=credentials, cache_discovery=False)

    try:
        dossier = service.files().get(
            fileId=config.GOOGLE_DRIVE_FOLDER_ID,
            fields='id,name,mimeType,capabilities(canAddChildren)').execute()
    except Exception as erreur:
        message = str(erreur)
        if '404' in message or 'notFound' in message:
            return echec(
                "Dossier introuvable ou non partagé",
                f"Deux causes possibles :\n"
                f"     · l'identifiant est erroné (recopiez-le depuis l'URL) ;\n"
                f"     · le dossier n'est pas partagé avec le compte de service.\n\n"
                f"     Dans Drive : clic droit sur le dossier → Partager →\n"
                f"     collez cette adresse → droit ÉDITEUR :\n\n"
                f"         {GRAS}{email}{FIN}")
        return echec(f"Accès au dossier refusé : {message[:110]}",
                     "Vérifiez le partage du dossier avec le compte de service.")

    ok(f"Dossier accessible : « {dossier.get('name')} »")

    if dossier.get('mimeType') != 'application/vnd.google-apps.folder':
        return echec("Cet identifiant ne désigne pas un dossier",
                     "GOOGLE_DRIVE_FOLDER_ID doit contenir l'identifiant d'un\n"
                     "     DOSSIER, pas d'un fichier.")

    if dossier.get('capabilities', {}).get('canAddChildren'):
        ok("Le compte de service peut y écrire")
    else:
        return echec(
            "Le compte de service n'a que le droit de lecture",
            f"Dans Drive : clic droit sur le dossier → Partager →\n"
            f"     modifiez le droit de {email} en ÉDITEUR.")
    return True


def controler_sheet(credentials, config, email):
    titre(7, "Le Google Sheet est-il accessible en écriture ?")
    if not config.GOOGLE_SHEET_ID:
        note("Aucun Sheet configuré — contrôle ignoré")
        return True

    from googleapiclient.discovery import build
    service = build('sheets', 'v4', credentials=credentials, cache_discovery=False)

    try:
        classeur = service.spreadsheets().get(
            spreadsheetId=config.GOOGLE_SHEET_ID).execute()
    except Exception as erreur:
        message = str(erreur)
        if '404' in message or 'notFound' in message:
            return echec(
                "Classeur introuvable ou non partagé",
                f"Ouvrez le Google Sheet → bouton Partager → collez cette\n"
                f"     adresse → droit ÉDITEUR :\n\n"
                f"         {GRAS}{email}{FIN}\n\n"
                f"     Vérifiez aussi l'identifiant, lisible dans l'URL entre\n"
                f"     « /d/ » et « /edit ».")
        if '403' in message:
            return echec(
                "Accès refusé au classeur",
                f"Le classeur est partagé en lecture seule. Passez\n"
                f"     {email} en ÉDITEUR.")
        return echec(f"Classeur inaccessible : {message[:110]}",
                     "Vérifiez le partage et l'identifiant.")

    titre_classeur = classeur['properties']['title']
    onglets = [f['properties']['title'] for f in classeur.get('sheets', [])]
    ok(f"Classeur accessible : « {titre_classeur} »")
    info(f"Onglets : {', '.join(onglets)}")

    # Écriture réelle dans une cellule éloignée, puis nettoyage
    premier = onglets[0]
    try:
        service.spreadsheets().values().update(
            spreadsheetId=config.GOOGLE_SHEET_ID,
            range=f"'{premier}'!ZZ999",
            valueInputOption='RAW',
            body={'values': [['test']]}).execute()
        service.spreadsheets().values().clear(
            spreadsheetId=config.GOOGLE_SHEET_ID,
            range=f"'{premier}'!ZZ999", body={}).execute()
        ok("Écriture dans le classeur réussie")
    except Exception as erreur:
        return echec(
            f"Écriture refusée : {str(erreur)[:110]}",
            f"Le compte de service est en lecture seule. Partagez le\n"
            f"     classeur avec {email} en ÉDITEUR.")
    return True


def controler_quota(credentials, config, email):
    titre(8, "Le compte de service peut-il créer un classeur ?")
    info("C'est ce que fait « Publier vers Google Sheets » la première fois.")

    from googleapiclient.discovery import build
    sheets = build('sheets', 'v4', credentials=credentials, cache_discovery=False)
    drive = build('drive', 'v3', credentials=credentials, cache_discovery=False)

    identifiant = None
    try:
        classeur = sheets.spreadsheets().create(
            body={'properties': {'title': 'Posiges — test de diagnostic'}},
            fields='spreadsheetId').execute()
        identifiant = classeur['spreadsheetId']
        ok("Création de classeur autorisée")
    except Exception as erreur:
        message = str(erreur)
        if 'storage quota' in message.lower():
            return echec(
                "Le compte de service n'a pas d'espace de stockage Drive",
                f"C'est une limite de Google, pas un défaut de l'application.\n"
                f"     Un compte de service ne peut pas CRÉER de fichier ; il ne\n"
                f"     peut qu'écrire dans un fichier existant.\n\n"
                f"     {GRAS}La solution :{FIN}\n"
                f"     1. Dans VOTRE Drive : Nouveau → Google Sheets\n"
                f"     2. Nommez-le, par exemple « Posiges — Jus de Bella »\n"
                f"     3. Partager → {email} → ÉDITEUR\n"
                f"     4. Copiez l'identifiant depuis l'URL\n"
                f"     5. Dans l'application : Entreprises suivies → Modifier →\n"
                f"        champ « ID du classeur de publication » → collez-le\n\n"
                f"     L'application écrira alors dans VOTRE classeur.")
        return echec(f"Création refusée : {message[:110]}",
                     "Utilisez un classeur créé manuellement (voir README).")
    finally:
        if identifiant:
            try:
                drive.files().delete(fileId=identifiant).execute()
                info("Classeur de test supprimé")
            except Exception:
                note(f"Classeur de test à supprimer manuellement : {identifiant}")
    return True


def controler_entreprises():
    titre(9, "Les entreprises ont-elles un classeur de publication ?")
    try:
        from app import app
        from models import Entreprise
    except Exception as erreur:
        note(f"Base non consultable ({erreur})")
        return True

    with app.app_context():
        entreprises = Entreprise.query.all()
        if not entreprises:
            note("Aucune entreprise enregistrée")
            return True
        for entreprise in entreprises:
            if entreprise.sheet_rapport_id:
                ok(f"{entreprise.nom} → classeur {entreprise.sheet_rapport_id}")
            else:
                note(f"{entreprise.nom} → aucun classeur")
                info("L'application tentera d'en créer un à la publication.")
    return True


# ==========================================================================

def main():
    print()
    print("=" * 68)
    print(f"{GRAS}  DIAGNOSTIC DE L'INTÉGRATION GOOGLE{FIN}")
    print("=" * 68)

    if not controler_bibliotheques():
        return terminer()

    try:
        from config import Config
    except Exception as erreur:
        print(f"\n{ROUGE}Impossible de lire la configuration : {erreur}{FIN}")
        return 1

    resultat = controler_credentials(Config)
    if resultat is False or (isinstance(resultat, tuple) and not resultat[0]):
        return terminer()
    email = resultat[1] if isinstance(resultat, tuple) else ''

    if not controler_variables(Config):
        return terminer()

    resultat = controler_authentification(Config)
    if resultat is False or (isinstance(resultat, tuple) and not resultat[0]):
        return terminer()
    credentials = resultat[1] if isinstance(resultat, tuple) else None

    controler_apis(credentials)
    controler_dossier_drive(credentials, Config, email)
    controler_sheet(credentials, Config, email)
    controler_quota(credentials, Config, email)
    controler_entreprises()

    return terminer()


def terminer():
    print()
    print("=" * 68)
    if not blocages:
        print(f"{VERT}{GRAS}  TOUT FONCTIONNE — la publication Google devrait aboutir.{FIN}")
        print("=" * 68)
        print()
        return 0

    print(f"{ROUGE}{GRAS}  {len(blocages)} PROBLÈME(S) À CORRIGER{FIN}")
    print("=" * 68)
    for index, (probleme, solution) in enumerate(blocages, 1):
        print(f"\n  {GRAS}{index}. {probleme}{FIN}\n")
        print(f"     {solution}")
    print()
    print("=" * 68)
    print("  Corrigez le premier problème, puis relancez ce diagnostic.")
    print("=" * 68)
    print()
    return 1


if __name__ == '__main__':
    sys.exit(main())
