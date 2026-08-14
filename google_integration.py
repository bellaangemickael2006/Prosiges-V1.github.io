"""
Intégration Google Drive (archivage des justificatifs) et Google Sheets
(journal partagé consultable par le gérant et les clients accompagnés).

PRINCIPE DE ROBUSTESSE : aucune de ces fonctions ne doit jamais faire échouer
l'enregistrement d'une opération. Si Google n'est pas configuré ou renvoie une
erreur, on journalise et on retourne None/False — l'opération reste enregistrée
en base de données, et une resynchronisation ultérieure reste possible.

Configuration : voir README.md, section "Activer Google Drive et Google Sheets".
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets',
]

ENTETES_JOURNAL = [
    'Date', 'Type', 'Catégorie', 'Libellé', 'Client', 'Montant',
    'Mode de paiement', 'Référence', 'N° Facture', 'Périmètre',
    'Saisi par', 'Lien justificatif',
]

_services = {}


# --------------------------------------------------------------------------
# Connexion
# --------------------------------------------------------------------------

def _credentials(fichier):
    if not fichier or not os.path.exists(fichier):
        logger.info("Google non configuré (fichier credentials absent : %s)", fichier)
        return None
    try:
        from google.oauth2 import service_account
    except ImportError:
        logger.warning("Bibliothèques Google non installées : "
                       "pip install google-api-python-client google-auth")
        return None
    try:
        return service_account.Credentials.from_service_account_file(fichier, scopes=SCOPES)
    except Exception:
        logger.exception("Fichier credentials Google illisible : %s", fichier)
        return None


def _service(nom, version, fichier):
    cle = (nom, version)
    if cle not in _services:
        creds = _credentials(fichier)
        if creds is None:
            return None
        try:
            from googleapiclient.discovery import build
            _services[cle] = build(nom, version, credentials=creds, cache_discovery=False)
        except ImportError:
            logger.warning("Bibliothèque googleapiclient non installée.")
            return None
        except Exception:
            logger.exception("Impossible d'initialiser le service Google %s", nom)
            return None
    return _services.get(cle)


def bibliotheques_disponibles():
    """Les bibliothèques Google sont-elles installées ?

    Elles ne font pas partie de l'installation de base : sans ce contrôle,
    l'interface annoncerait Google comme actif alors que rien ne peut
    fonctionner.
    """
    try:
        import google.oauth2.service_account          # noqa: F401
        import googleapiclient.discovery              # noqa: F401
        return True
    except ImportError:
        return False


def est_configure(fichier_credentials):
    """Indique si l'intégration Google est réellement utilisable.

    Trois conditions : les bibliothèques installées, le fichier de clé
    présent, et lisible. Un seul de ces éléments manquant suffit à faire
    échouer toute opération Google.
    """
    if not bibliotheques_disponibles():
        return False
    return bool(fichier_credentials) and os.path.exists(fichier_credentials)


def raison_indisponibilite(fichier_credentials):
    """Explique en une phrase pourquoi Google n'est pas utilisable.

    Retourne None si tout va bien. Sert à afficher un message utile
    plutôt qu'un échec générique.
    """
    if not bibliotheques_disponibles():
        return ("les bibliothèques Google ne sont pas installées "
                "(lancez : pip install -r requirements-google.txt)")
    if not fichier_credentials or not os.path.exists(fichier_credentials):
        return f"le fichier de clé est introuvable ({fichier_credentials})"
    return None


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------

def upload_fichier(chemin_local, nom_fichier, fichier_credentials, dossier_id):
    """Envoie un justificatif vers Drive. Retourne l'URL ou None."""
    if not dossier_id:
        logger.info("Upload Drive ignoré (aucun dossier configuré) : %s", nom_fichier)
        return None
    service = _service('drive', 'v3', fichier_credentials)
    if service is None:
        return None

    try:
        from googleapiclient.http import MediaFileUpload

        metadonnees = {'name': nom_fichier, 'parents': [dossier_id]}
        media = MediaFileUpload(chemin_local, resumable=True)
        fichier = (
            service.files()
            .create(body=metadonnees, media_body=media, fields='id, webViewLink')
            .execute()
        )
        logger.info("Justificatif envoyé sur Drive : %s", nom_fichier)
        return fichier.get('webViewLink')
    except Exception:
        logger.exception("Échec de l'upload Drive : %s", nom_fichier)
        return None


def creer_dossier_entreprise(nom_entreprise, fichier_credentials, dossier_parent_id):
    """Crée un sous-dossier Drive dédié à une entreprise accompagnée.
    Retourne l'ID du dossier créé ou None."""
    if not dossier_parent_id:
        return None
    service = _service('drive', 'v3', fichier_credentials)
    if service is None:
        return None

    try:
        metadonnees = {
            'name': nom_entreprise,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [dossier_parent_id],
        }
        dossier = service.files().create(body=metadonnees, fields='id').execute()
        return dossier.get('id')
    except Exception:
        logger.exception("Échec de la création du dossier Drive pour %s", nom_entreprise)
        return None


# --------------------------------------------------------------------------
# Sheets
# --------------------------------------------------------------------------

def _plage_onglet(nom_onglet, debut='A1', fin=None):
    """Construit une plage Google Sheets compatible pour un onglet donné."""
    if re.fullmatch(r"[A-Za-z0-9_.-]+", nom_onglet):
        base = nom_onglet
    else:
        base = "'{}'".format(nom_onglet.replace("'", "''"))
    if fin is None:
        return f"{base}!{debut}"
    return f"{base}!{debut}:{fin}"


def _ligne_operation(operation):
    from models import Departement, ModePaiement, TypeOperation

    return [
        operation.date_operation.strftime('%d/%m/%Y'),
        TypeOperation.LABELS.get(operation.type_operation, operation.type_operation),
        operation.categorie,
        operation.libelle or '',
        operation.client.nom if operation.client else '',
        operation.montant,
        ModePaiement.LABELS.get(operation.mode_paiement, operation.mode_paiement),
        operation.reference or '',
        operation.numero_facture or '',
        operation.libelle_perimetre(),
        operation.cree_par.nom if operation.cree_par else '',
        operation.fichier_url or '',
    ]


def _assurer_entetes(service, sheet_id, feuille):
    """Crée la feuille attendue si nécessaire, puis écrit la ligne d'en-tête."""
    try:
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        existants = {f['properties']['title']: f['properties']['sheetId']
                     for f in meta.get('sheets', [])}

        if feuille not in existants:
            requete = {'addSheet': {'properties': {'title': feuille}}}
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id, body={'requests': [requete]}).execute()

        plage = f"{feuille}!A1:L1"
        reponse = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=plage).execute()
        if not reponse.get('values'):
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=plage,
                valueInputOption='RAW',
                body={'values': [ENTETES_JOURNAL]},
            ).execute()
    except Exception:
        logger.debug("Vérification des en-têtes impossible (feuille absente ?)", exc_info=True)


def ajouter_ligne_sheet(operation, fichier_credentials, sheet_id, feuille='Journal'):
    """Ajoute l'opération dans le Google Sheet partagé. True si succès."""
    if not sheet_id:
        logger.info("Synchro Sheets ignorée (aucun sheet configuré) pour l'opération %s",
                    operation.id)
        return False
    service = _service('sheets', 'v4', fichier_credentials)
    if service is None:
        return False

    try:
        _assurer_entetes(service, sheet_id, feuille)
        # APPEND et non UPDATE : « update » sur A1 réécrirait la première
        # ligne à chaque opération (donc l'en-tête, puis l'opération
        # précédente). « append » ajoute la ligne à la suite des données
        # existantes, ce qui est le comportement attendu d'un journal.
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=_plage_onglet(feuille, 'A', 'L'),
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body={'values': [_ligne_operation(operation)]},
        ).execute()
        return True
    except Exception:
        logger.exception("Échec de la synchro Sheets pour l'opération %s", operation.id)
        return False


def message_erreur_google(erreur):
    """Traduit une erreur de l'API Google en consigne compréhensible."""
    texte = str(erreur)
    if 'storage quota' in texte.lower():
        return ("le compte de service n'a pas d'espace de stockage Drive et ne "
                "peut donc pas créer de classeur. Créez-en un vous-même dans "
                "votre Drive, partagez-le en Éditeur avec le compte de service, "
                "puis renseignez son identifiant dans Entreprises suivies → "
                "Modifier → « ID du classeur de publication ».")
    if 'has not been used' in texte or 'is disabled' in texte:
        return ("l'API Google Sheets ou Drive n'est pas activée dans votre "
                "projet Google Cloud (console.cloud.google.com → APIs et "
                "services → Bibliothèque).")
    if '404' in texte or 'notFound' in texte:
        return ("le classeur ou le dossier est introuvable : vérifiez "
                "l'identifiant et le partage avec le compte de service.")
    if '403' in texte or 'permission' in texte.lower():
        return ("le compte de service n'a pas le droit d'écrire : partagez le "
                "classeur avec lui en tant qu'ÉDITEUR, et non lecteur.")
    if 'invalid_grant' in texte:
        return ("la clé du compte de service est invalide ou révoquée, ou "
                "l'horloge de votre ordinateur est décalée.")
    return texte[:180]


def creer_classeur(titre, fichier_credentials, dossier_id=None):
    """Crée un nouveau Google Sheet et le range dans le dossier Drive indiqué.
    Retourne (sheet_id, url, erreur)."""
    indisponible = raison_indisponibilite(fichier_credentials)
    if indisponible:
        return None, None, f"Google indisponible : {indisponible}"

    service = _service('sheets', 'v4', fichier_credentials)
    if service is None:
        return None, None, "impossible d'initialiser le service Google Sheets"

    try:
        classeur = service.spreadsheets().create(
            body={'properties': {'title': titre, 'locale': 'fr_FR'}},
            fields='spreadsheetId,spreadsheetUrl').execute()
        sheet_id = classeur['spreadsheetId']
        url = classeur['spreadsheetUrl']
    except Exception as erreur:
        logger.exception("Échec de la création du classeur « %s »", titre)
        return None, None, message_erreur_google(erreur)

    # Déplacer le classeur dans le dossier Drive de l'entreprise
    if dossier_id:
        drive = _service('drive', 'v3', fichier_credentials)
        if drive is not None:
            try:
                fichier = drive.files().get(fileId=sheet_id, fields='parents').execute()
                anciens = ','.join(fichier.get('parents', []))
                drive.files().update(fileId=sheet_id, addParents=dossier_id,
                                     removeParents=anciens, fields='id').execute()
            except Exception:
                logger.warning("Classeur créé mais non déplacé dans le dossier Drive.",
                               exc_info=True)
    return sheet_id, url, None


def _ecrire_onglet(service, sheet_id, nom_onglet, valeurs, index=None):
    """Crée l'onglet s'il n'existe pas, l'efface, puis y écrit les valeurs."""
    try:
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        existants = {f['properties']['title']: f['properties']['sheetId']
                     for f in meta.get('sheets', [])}

        if nom_onglet not in existants:
            requete = {'addSheet': {'properties': {'title': nom_onglet}}}
            if index is not None:
                requete['addSheet']['properties']['index'] = index
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id, body={'requests': [requete]}).execute()
        else:
            service.spreadsheets().values().clear(
                spreadsheetId=sheet_id, range=_plage_onglet(nom_onglet, 'A', 'Z'), body={}).execute()

        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=_plage_onglet(nom_onglet, 'A1'),
            valueInputOption='USER_ENTERED',
            body={'values': valeurs}).execute()
        return True, None
    except Exception as erreur:
        logger.exception("Échec de l'écriture de l'onglet « %s »", nom_onglet)
        return False, message_erreur_google(erreur)


BLEU_RGB = {'red': 0.11, 'green': 0.24, 'blue': 0.35}
GRIS_CLAIR_RGB = {'red': 0.94, 'green': 0.95, 'blue': 0.96}
GRIS_BORDURE_RGB = {'red': 0.85, 'green': 0.85, 'blue': 0.85}


def _analyser_structure(valeurs):
    """Devine la structure d'un onglet à partir de son contenu.

    Les onglets publiés n'ont pas tous le même nombre de lignes de titre
    (« Journal de bord » en a deux, « Données » aucune, les comptes
    détaillés ont un en-tête sur deux lignes) : plutôt que de figer la
    mise en forme sur la ligne 1, on la déduit du contenu, ce qui reste
    correct quelle que soit la construction de l'onglet.
    """
    nb_lignes = len(valeurs)
    nb_colonnes = max((len(l) for l in valeurs), default=0)

    lignes_titre = []
    index = 0
    while index < nb_lignes:
        non_vides = sum(1 for c in valeurs[index] if c not in (None, ''))
        if non_vides <= 1 and index < nb_lignes - 1:
            if non_vides == 1:
                lignes_titre.append(index)
            index += 1
            continue
        break

    lignes_entete = [index] if index < nb_lignes else []
    if (lignes_entete and index + 1 < nb_lignes
            and 'Prévu' in valeurs[index + 1]):
        lignes_entete.append(index + 1)

    lignes_total = [i for i, l in enumerate(valeurs)
                    if l and str(l[0]).strip().upper().startswith('TOTAL')]

    debut_donnees = (lignes_entete[-1] + 1) if lignes_entete else 0
    colonnes_numeriques = set()
    for col in range(nb_colonnes):
        echantillon = [l[col] for i, l in enumerate(valeurs)
                       if i >= debut_donnees and i not in lignes_total
                       and col < len(l) and l[col] not in (None, '')]
        if echantillon and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                               for v in echantillon):
            colonnes_numeriques.add(col)

    return {
        'lignes_titre': lignes_titre,
        'lignes_entete': lignes_entete,
        'lignes_total': lignes_total,
        'colonnes_numeriques': colonnes_numeriques,
        'nb_colonnes': nb_colonnes,
        'nb_lignes': nb_lignes,
    }


def _requetes_mise_en_forme(gid, valeurs):
    """Construit les requêtes de mise en forme d'un onglet (titres fusionnés,
    en-têtes de colonnes en surbrillance, lignes de total en gras,
    bordures, format numérique à milliers) à partir de son contenu réel."""
    structure = _analyser_structure(valeurs)
    largeur = max(structure['nb_colonnes'], 1)
    requetes = []

    for ligne in structure['lignes_titre']:
        plage = {'sheetId': gid, 'startRowIndex': ligne, 'endRowIndex': ligne + 1,
                 'startColumnIndex': 0, 'endColumnIndex': largeur}
        requetes.append({'mergeCells': {'range': plage, 'mergeType': 'MERGE_ALL'}})
        requetes.append({'repeatCell': {
            'range': plage,
            'cell': {'userEnteredFormat': {
                'textFormat': {'bold': True, 'fontSize': 13, 'foregroundColor': BLEU_RGB},
                'horizontalAlignment': 'CENTER'}},
            'fields': 'userEnteredFormat(textFormat,horizontalAlignment)'}})

    for ligne in structure['lignes_entete']:
        requetes.append({'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': ligne, 'endRowIndex': ligne + 1,
                      'startColumnIndex': 0, 'endColumnIndex': largeur},
            'cell': {'userEnteredFormat': {
                'backgroundColor': BLEU_RGB,
                'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                'horizontalAlignment': 'CENTER'}},
            'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)'}})

    if structure['lignes_entete']:
        requetes.append({'updateSheetProperties': {
            'properties': {'sheetId': gid, 'gridProperties': {
                'frozenRowCount': structure['lignes_entete'][-1] + 1}},
            'fields': 'gridProperties.frozenRowCount'}})

    for ligne in structure['lignes_total']:
        requetes.append({'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': ligne, 'endRowIndex': ligne + 1,
                      'startColumnIndex': 0, 'endColumnIndex': largeur},
            'cell': {'userEnteredFormat': {
                'textFormat': {'bold': True},
                'backgroundColor': GRIS_CLAIR_RGB,
                'borders': {'top': {'style': 'SOLID', 'width': 1, 'color': BLEU_RGB}}}},
            'fields': 'userEnteredFormat(textFormat,backgroundColor,borders.top)'}})

    debut_donnees = (structure['lignes_entete'][-1] + 1) if structure['lignes_entete'] else 0
    fin_donnees = structure['nb_lignes']
    if fin_donnees > debut_donnees and structure['nb_colonnes']:
        requetes.append({'updateBorders': {
            'range': {'sheetId': gid, 'startRowIndex': debut_donnees, 'endRowIndex': fin_donnees,
                      'startColumnIndex': 0, 'endColumnIndex': structure['nb_colonnes']},
            'innerHorizontal': {'style': 'SOLID', 'width': 1, 'color': GRIS_BORDURE_RGB},
            'innerVertical': {'style': 'SOLID', 'width': 1, 'color': GRIS_BORDURE_RGB}}})

    for col in structure['colonnes_numeriques']:
        requetes.append({'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': debut_donnees, 'endRowIndex': fin_donnees,
                      'startColumnIndex': col, 'endColumnIndex': col + 1},
            'cell': {'userEnteredFormat': {
                'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'},
                'horizontalAlignment': 'RIGHT'}},
            'fields': 'userEnteredFormat(numberFormat,horizontalAlignment)'}})

    requetes.append({'autoResizeDimensions': {
        'dimensions': {'sheetId': gid, 'dimension': 'COLUMNS',
                       'startIndex': 0, 'endIndex': structure['nb_colonnes']}}})
    return requetes


def _mettre_en_forme_classeur(service, sheet_id, valeurs_par_onglet):
    """Applique la mise en forme professionnelle à chaque onglet du classeur,
    en s'appuyant sur le contenu réellement publié pour chacun d'eux."""
    try:
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        requetes = []
        for feuille in meta.get('sheets', []):
            gid = feuille['properties']['sheetId']
            valeurs = valeurs_par_onglet.get(feuille['properties']['title'])
            if valeurs:
                requetes += _requetes_mise_en_forme(gid, valeurs)
        if requetes:
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id, body={'requests': requetes}).execute()
    except Exception:
        logger.debug("Mise en forme du classeur ignorée.", exc_info=True)


def publier_classeur_complet(sheet_id, onglets, fichier_credentials):
    """Écrit tous les onglets d'un classeur d'un coup.

    `onglets` : liste de tuples (nom, valeurs) où valeurs est une liste de
    lignes. Retourne (nombre_d_onglets_ecrits, erreur_eventuelle).
    """
    indisponible = raison_indisponibilite(fichier_credentials)
    if indisponible:
        return 0, f"Google indisponible : {indisponible}"
    if not sheet_id:
        return 0, "aucun classeur de destination n'est défini"

    service = _service('sheets', 'v4', fichier_credentials)
    if service is None:
        return 0, "impossible d'initialiser le service Google Sheets"

    reussis = 0
    derniere_erreur = None
    valeurs_par_onglet = {}
    for index, (nom, valeurs) in enumerate(onglets):
        succes, erreur = _ecrire_onglet(service, sheet_id, nom, valeurs, index=index)
        if succes:
            reussis += 1
            valeurs_par_onglet[nom] = valeurs
        else:
            derniere_erreur = erreur

    # Supprimer l'onglet « Feuille 1 » créé par défaut, s'il est resté vide
    try:
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        noms_voulus = {nom for nom, _ in onglets}
        a_supprimer = [
            f['properties']['sheetId'] for f in meta.get('sheets', [])
            if f['properties']['title'] not in noms_voulus
        ]
        if a_supprimer and len(a_supprimer) < len(meta.get('sheets', [])):
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={'requests': [{'deleteSheet': {'sheetId': gid}}
                                   for gid in a_supprimer]}).execute()
    except Exception:
        logger.debug("Nettoyage des onglets par défaut ignoré.", exc_info=True)

    _mettre_en_forme_classeur(service, sheet_id, valeurs_par_onglet)
    return reussis, derniere_erreur


def url_classeur(sheet_id):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def resynchroniser_journal(operations, fichier_credentials, sheet_id, feuille='Journal'):
    """Réécrit intégralement la feuille à partir des opérations fournies.
    Utile après des modifications/suppressions, ou pour un premier envoi
    quand Google a été configuré après coup. Retourne le nombre de lignes."""
    if not sheet_id:
        return 0
    service = _service('sheets', 'v4', fichier_credentials)
    if service is None:
        return 0

    try:
        # Cibler explicitement l'onglet du journal : sans nom d'onglet,
        # Google écrit dans la PREMIÈRE feuille du classeur, qui n'est pas
        # forcément la bonne.
        _assurer_entetes(service, sheet_id, feuille)
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=_plage_onglet(feuille, 'A', 'L'), body={}).execute()
        valeurs = [ENTETES_JOURNAL] + [_ligne_operation(op) for op in operations]
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=_plage_onglet(feuille, 'A1'),
            valueInputOption='USER_ENTERED',
            body={'values': valeurs},
        ).execute()
        return len(operations)
    except Exception:
        logger.exception("Échec de la resynchronisation du journal")
        return 0

