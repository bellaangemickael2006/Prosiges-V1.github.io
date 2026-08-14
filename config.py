import os

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


def _chemin_absolu(chemin, defaut=None):
    if not chemin:
        return defaut if defaut is None else os.path.abspath(defaut)
    if os.path.isabs(chemin):
        return chemin
    return os.path.abspath(os.path.join(basedir, chemin))


class Config:
    # ---- Sécurité ----
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-a-changer-en-production')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Mettre à True en production derrière HTTPS
    SESSION_COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'

    # ---- Base de données ----
    # SQLite en local ; en production, définir DATABASE_URL (PostgreSQL recommandé)
    _url = os.environ.get('DATABASE_URL', '')
    if _url.startswith('postgres://'):        # normalisation Render/Heroku
        _url = _url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _url or f"sqlite:///{os.path.join(basedir, 'ak_world.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}

    # ---- Google Drive / Sheets ----
    # Voir README.md, section "Activer Google Drive et Google Sheets".
    #
    # Deux façons de fournir la clé du compte de service :
    #   1. un fichier credentials.json à la racine (usage local) ;
    #   2. la variable GOOGLE_CREDENTIALS_JSON contenant le JSON complet
    #      (usage en production : aucun fichier secret à déposer sur le serveur).
    GOOGLE_CREDENTIALS_FILE = _chemin_absolu(
        os.environ.get('GOOGLE_CREDENTIALS_FILE', 'credentials.json'),
        os.path.join(basedir, 'credentials.json'))
    _json_inline = os.environ.get('GOOGLE_CREDENTIALS_JSON', '').strip()
    if _json_inline and not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        try:
            import tempfile
            _fd, _chemin = tempfile.mkstemp(suffix='.json', prefix='gcreds_')
            with os.fdopen(_fd, 'w', encoding='utf-8') as _f:
                _f.write(_json_inline)
            GOOGLE_CREDENTIALS_FILE = _chemin
        except OSError:
            pass

    GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '')
    GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')

    # ---- Initialisation automatique ----
    # Crée les tables et le compte gérant au démarrage si la base est vide.
    # Indispensable en production (gunicorn n'exécute pas le bloc __main__).
    AUTO_INIT = os.environ.get('AUTO_INIT', 'true').lower() == 'true'

    # ---- Fichiers ----
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(basedir, 'uploads'))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024      # 10 Mo par fichier
    EXTENSIONS_AUTORISEES = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.heic',
                             '.doc', '.docx', '.xls', '.xlsx'}

    # ---- Application ----
    DEVISE = 'FCFA'
    NOM_CABINET = os.environ.get('NOM_CABINET', 'AK World')
