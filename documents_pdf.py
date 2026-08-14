"""
Posiges V1 — génération des documents commerciaux en PDF.

La mise en page reproduit les modèles remis par le cabinet :

  · logo de l'entreprise émettrice en haut à gauche
  · titre centré et souligné — FACTURE ou REÇU DE PAIEMENT
  · bloc d'identification : n° de commande, date, n° de pièce, catégorie
    de la transaction, puis coordonnées du client
  · « RÉSUMÉ DE LA TRANSACTION » ou « RÉSUMÉ DU PAIEMENT », puis le tableau
    des articles — avec quantité et prix unitaire sur une facture, avec la
    colonne « Montant payé » sur un reçu
  · totaux : total à payer sur une facture ; prix total payé et reste à
    payer sur un reçu
  · bloc « Informations bancaires » sur une facture
  · « LA DIRECTION », signature ou cachet, puis nom du signataire
  · bandeau légal centré en pied de page

Toutes les informations proviennent du profil de facturation : le gérant
ne ressaisit jamais ses coordonnées, et son logo est repris tel quel.
"""
import os
from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Flowable, HRFlowable, Image, KeepTogether,
                                PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)
from reportlab.lib.utils import ImageReader

from models import (ConditionVente, ModePaiement, StatutDocument, TypeDocument)

import comptes
import graphiques_pdf

NOIR = colors.HexColor('#1A1A1A')
GRIS = colors.HexColor('#666666')
GRIS_CLAIR = colors.HexColor('#EFEFEF')
BLEU_BANDEAU = colors.HexColor('#DCE6F1')
BORDURE = colors.HexColor('#7F7F7F')
ROUGE = colors.HexColor('#C00000')
VERT = colors.HexColor('#2E8B57')

LARGEUR_UTILE = 17.0 * cm


def montant(valeur, devise='FCFA'):
    return f"{valeur:,.0f} {devise}".replace(',', ' ').strip()


# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------

def _styles():
    """Police à empattements, comme sur les modèles du cabinet."""
    base = getSampleStyleSheet()
    return {
        'titre': ParagraphStyle(
            'TitreDoc', parent=base['Normal'], fontName='Times-Bold',
            fontSize=15, textColor=NOIR, alignment=TA_CENTER, leading=19),
        'titre_rouge': ParagraphStyle(
            'TitreDocRouge', parent=base['Normal'], fontName='Times-Bold',
            fontSize=15, textColor=ROUGE, alignment=TA_CENTER, leading=19),
        'section': ParagraphStyle(
            'SectionDoc', parent=base['Normal'], fontName='Times-Bold',
            fontSize=11, textColor=NOIR, leading=15),
        'etiquette': ParagraphStyle(
            'Etiquette', parent=base['Normal'], fontName='Times-Roman',
            fontSize=9, textColor=NOIR, leading=12),
        'valeur': ParagraphStyle(
            'Valeur', parent=base['Normal'], fontName='Times-Bold',
            fontSize=9, textColor=NOIR, leading=12),
        'valeur_rouge': ParagraphStyle(
            'ValeurRouge', parent=base['Normal'], fontName='Times-Bold',
            fontSize=9, textColor=ROUGE, leading=12),
        'cellule': ParagraphStyle(
            'Cellule', parent=base['Normal'], fontName='Times-Roman',
            fontSize=9, textColor=NOIR, leading=12),
        'cellule_centre': ParagraphStyle(
            'CelluleCentre', parent=base['Normal'], fontName='Times-Roman',
            fontSize=9, textColor=NOIR, leading=12, alignment=TA_CENTER),
        'cellule_droite': ParagraphStyle(
            'CelluleDroite', parent=base['Normal'], fontName='Times-Roman',
            fontSize=9, textColor=NOIR, leading=12, alignment=TA_RIGHT),
        'entete_tableau': ParagraphStyle(
            'EnteteTableau', parent=base['Normal'], fontName='Times-Bold',
            fontSize=9, textColor=NOIR, leading=12, alignment=TA_CENTER),
        'note': ParagraphStyle(
            'NoteDoc', parent=base['Normal'], fontName='Times-Roman',
            fontSize=8, textColor=NOIR, leading=11, alignment=TA_LEFT),
        'pied': ParagraphStyle(
            'PiedDoc', parent=base['Normal'], fontName='Times-Roman',
            fontSize=7.5, textColor=NOIR, leading=10, alignment=TA_CENTER),
        'signataire': ParagraphStyle(
            'Signataire', parent=base['Normal'], fontName='Times-Roman',
            fontSize=10, textColor=NOIR, leading=14),
    }


# --------------------------------------------------------------------------
# Images : logo et signature
# --------------------------------------------------------------------------

def _image_ajustee(chemin, largeur_max, hauteur_max):
    """Charge une image en respectant ses proportions.

    Retourne None si le fichier est absent ou illisible : un logo manquant
    ne doit jamais empêcher l'émission d'une facture.
    """
    if not chemin or not os.path.exists(chemin):
        return None
    try:
        lecteur = ImageReader(chemin)
        largeur, hauteur = lecteur.getSize()
        if not largeur or not hauteur:
            return None
        echelle = min(largeur_max / largeur, hauteur_max / hauteur)
        return Image(chemin, width=largeur * echelle, height=hauteur * echelle)
    except Exception:
        return None


def _entete_logo(profil, entreprise, styles):
    """Logo en haut à gauche, ou raison sociale si aucun logo n'est chargé."""
    logo = _image_ajustee(
        (profil.logo if profil else None) or (entreprise.logo if entreprise else None),
        largeur_max=6.2 * cm, hauteur_max=2.2 * cm)
    if logo is not None:
        logo.hAlign = 'LEFT'
        return logo

    raison = ((profil.raison_sociale if profil and profil.raison_sociale else None)
              or (entreprise.nom if entreprise else 'Entreprise'))
    return Paragraph(raison, ParagraphStyle(
        'RaisonSansLogo', fontName='Times-Bold', fontSize=16,
        textColor=NOIR, leading=20, alignment=TA_LEFT))


# --------------------------------------------------------------------------
# Bloc d'identification
# --------------------------------------------------------------------------

def _bloc_identification(document, styles):
    """Références du document et coordonnées du client.

    Reprend exactement les intitulés des modèles : N° de Commande, Date de
    Transaction, N° de Facture (ou de Reçu), Catégorie de la Transaction,
    puis l'identité du client.
    """
    est_recu = document.type_document == TypeDocument.RECU
    libelle_piece = 'N° de Reçu :' if est_recu else 'N° de Facture :'

    date_affichee = document.date_document.strftime('%d/%m/%Y')
    if document.heure_document:
        date_affichee += f" à {document.heure_document}"

    client = document.client
    lignes = [
        [Paragraph('N° de Commande :', styles['etiquette']),
         Paragraph(document.numero_commande or '—', styles['valeur']),
         Paragraph('Date de Transaction :', styles['etiquette']),
         Paragraph(date_affichee, styles['valeur_rouge'])],
        [Paragraph(libelle_piece, styles['etiquette']),
         Paragraph(document.numero, styles['valeur_rouge']),
         Paragraph('Catégorie de la Transaction :', styles['etiquette']),
         Paragraph((document.categorie_transaction or '—').upper(),
                   styles['valeur'])],
        [Paragraph('Nom du Client :', styles['etiquette']),
         Paragraph((client.nom if client else 'Client de passage').upper(),
                   styles['valeur']), '', ''],
        [Paragraph('Adresse résidentielle :', styles['etiquette']),
         Paragraph(client.localisation if client and client.localisation else '',
                   styles['cellule']), '', ''],
        [Paragraph('Contact N° :', styles['etiquette']),
         Paragraph(client.contact if client and client.contact else '',
                   styles['cellule']), '', ''],
        [Paragraph('Adresse E-mail :', styles['etiquette']),
         Paragraph(getattr(client, 'email', '') or '' if client else '',
                   styles['cellule']), '', ''],
    ]

    table = Table(lignes, colWidths=[4.2 * cm, 4.6 * cm, 4.4 * cm, 3.8 * cm])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        # Les trois dernières lignes occupent toute la largeur
        ('SPAN', (1, 2), (3, 2)),
        ('SPAN', (1, 3), (3, 3)),
        ('SPAN', (1, 4), (3, 4)),
        ('SPAN', (1, 5), (3, 5)),
        # Filets horizontaux, comme sur les modèles
        ('LINEABOVE', (0, 0), (-1, 0), 0.8, NOIR),
        ('LINEBELOW', (0, 1), (-1, 1), 0.8, NOIR),
        ('LINEBELOW', (0, 2), (-1, 2), 0.5, BORDURE),
        ('LINEBELOW', (0, 3), (-1, 3), 0.5, BORDURE),
        ('LINEBELOW', (0, 4), (-1, 4), 0.5, BORDURE),
        ('LINEBELOW', (0, 5), (-1, 5), 0.8, NOIR),
    ]))
    return table


# --------------------------------------------------------------------------
# Tableau des articles
# --------------------------------------------------------------------------

def _tableau_recu(document, styles):
    """Tableau du reçu : article, produit, montant total, montant payé."""
    donnees = [[
        Paragraph('No. Article', styles['entete_tableau']),
        Paragraph('Produit', styles['entete_tableau']),
        Paragraph('Montant Total', styles['entete_tableau']),
        Paragraph('Montant payé', styles['entete_tableau']),
    ]]

    for rang, ligne in enumerate(document.lignes, start=1):
        if ligne.remise:
            paye = Paragraph('<i>(Remise)</i>', styles['cellule_droite'])
        else:
            paye = Paragraph(montant(ligne.paye_ligne, 'CFA'),
                             styles['cellule_droite'])
        donnees.append([
            Paragraph(str(rang), styles['cellule_centre']),
            Paragraph(ligne.designation, styles['cellule']),
            Paragraph(montant(ligne.total_ligne, 'CFA'), styles['cellule_droite']),
            paye,
        ])

    donnees.append([
        '', Paragraph('Prix total payé', styles['valeur_rouge']), '',
        Paragraph(montant(document.montant_paye or 0, 'CFA'),
                  ParagraphStyle('TotalRouge', fontName='Times-Bold',
                                 fontSize=9.5, textColor=ROUGE,
                                 alignment=TA_RIGHT)),
    ])
    donnees.append([
        '', Paragraph('Reste à Payer', styles['valeur']), '',
        Paragraph(montant(document.reste_a_payer, 'CFA'),
                  ParagraphStyle('ResteDu', fontName='Times-Bold', fontSize=9.5,
                                 textColor=(ROUGE if document.reste_a_payer > 0
                                            else NOIR),
                                 alignment=TA_RIGHT)),
    ])

    table = Table(donnees, colWidths=[2.2 * cm, 7.6 * cm, 3.6 * cm, 3.6 * cm],
                  repeatRows=1)
    table.setStyle(_style_tableau(len(donnees), lignes_totaux=2, colonne_span=1))
    return table


def _tableau_facture(document, profil, styles):
    """Tableau de la facture : article, produit, quantité, PU, total."""
    avec_tva = bool(profil and profil.regime_tva)
    donnees = [[
        Paragraph('No. Article', styles['entete_tableau']),
        Paragraph('Produit', styles['entete_tableau']),
        Paragraph('Quantité', styles['entete_tableau']),
        Paragraph('Montant unitaire', styles['entete_tableau']),
        Paragraph('Montant Total', styles['entete_tableau']),
    ]]

    for rang, ligne in enumerate(document.lignes, start=1):
        donnees.append([
            Paragraph(str(rang), styles['cellule_centre']),
            Paragraph(ligne.designation, styles['cellule']),
            Paragraph(f'{ligne.quantite:g}', styles['cellule_centre']),
            Paragraph(montant(ligne.prix_unitaire, 'CFA'), styles['cellule_droite']),
            Paragraph(montant(ligne.total_ligne, 'CFA'), styles['cellule_droite']),
        ])

    lignes_totaux = 1
    if avec_tva:
        lignes_totaux = 3
        donnees.append(['', Paragraph('Total HT', styles['valeur']), '', '',
                        Paragraph(montant(document.montant_ht, 'CFA'),
                                  styles['cellule_droite'])])
        donnees.append(['', Paragraph(f"TVA ({profil.taux_tva:g} %)",
                                      styles['valeur']), '', '',
                        Paragraph(montant(document.montant_tva, 'CFA'),
                                  styles['cellule_droite'])])

    donnees.append([
        '', Paragraph('Total à payer', styles['valeur']), '', '',
        Paragraph(montant(document.montant_ttc or 0, 'CFA'),
                  ParagraphStyle('TotalFacture', fontName='Times-Bold',
                                 fontSize=9.5, textColor=NOIR,
                                 alignment=TA_RIGHT)),
    ])

    table = Table(donnees,
                  colWidths=[2.2 * cm, 6.4 * cm, 2.2 * cm, 3.1 * cm, 3.1 * cm],
                  repeatRows=1)
    table.setStyle(_style_tableau(len(donnees), lignes_totaux=lignes_totaux,
                                  colonne_span=1))
    return table


def _style_tableau(nombre_lignes, lignes_totaux, colonne_span):
    """Encadrement commun aux deux tableaux."""
    premiere_totaux = nombre_lignes - lignes_totaux
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), BLEU_BANDEAU),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEABOVE', (0, 0), (-1, 0), 0.8, NOIR),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, NOIR),
        ('LINEBELOW', (0, 1), (-1, premiere_totaux - 1), 0.4, BORDURE),
        ('LINEABOVE', (0, premiere_totaux), (-1, premiere_totaux), 0.8, NOIR),
        ('LINEBELOW', (0, -1), (-1, -1), 0.8, NOIR),
    ]
    # Les lignes de totaux fusionnent le libellé sur les colonnes du milieu
    for index in range(premiere_totaux, nombre_lignes):
        style.append(('SPAN', (colonne_span, index), (-2, index)))
        style.append(('BACKGROUND', (0, index), (-1, index), GRIS_CLAIR))
    return TableStyle(style)


# --------------------------------------------------------------------------
# Blocs de bas de page
# --------------------------------------------------------------------------

def _bloc_bancaire(profil, styles):
    """Bandeau « Informations bancaires » de la facture."""
    if not profil or not (profil.banque or profil.numero_compte
                          or profil.mobile_money):
        return []

    bandeau = Table([[Paragraph('Informations Bancaires',
                                ParagraphStyle('TitreBancaire',
                                               fontName='Times-Bold',
                                               fontSize=9.5, textColor=NOIR,
                                               alignment=TA_CENTER))]],
                    colWidths=[LARGEUR_UTILE])
    bandeau.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BLEU_BANDEAU),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDURE),
    ]))

    gauche = []
    if profil.numero_compte:
        gauche.append(f"<b>IBAN :</b> {profil.numero_compte}")
    if profil.beneficiaire:
        gauche.append(f"<b>Client bénéficiaire :</b> {profil.beneficiaire}")
    if profil.mobile_money:
        gauche.append(f"<b>Mobile Money :</b> {profil.mobile_money}")

    droite = f"<b>Banque :</b> {profil.banque}" if profil.banque else ''

    detail = Table(
        [[Paragraph('<font color="#C00000"><b>Informations bancaires :</b></font>'
                    '<br/>' + '<br/>'.join(gauche), styles['cellule']),
          Paragraph(droite, styles['cellule'])]],
        colWidths=[11.0 * cm, 6.0 * cm])
    detail.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
    ]))
    return [Spacer(1, 14), bandeau, detail]


def _bloc_direction(profil, styles):
    """« LA DIRECTION », signature ou cachet, puis nom du signataire."""
    elements = [Spacer(1, 18),
                Paragraph('<u>LA DIRECTION</u>', styles['section'])]

    signature = _image_ajustee(profil.signature if profil else None,
                               largeur_max=6.0 * cm, hauteur_max=2.6 * cm)
    if signature is not None:
        signature.hAlign = 'LEFT'
        elements += [Spacer(1, 4), signature]
    else:
        elements.append(Spacer(1, 42))

    signataire = (profil.signataire if profil and profil.signataire else '')
    if signataire:
        elements += [Spacer(1, 4), Paragraph(signataire, styles['signataire'])]
    return elements


def _dessiner_pied(texte, styles):
    """Fabrique la fonction qui ancre le bandeau légal en bas de chaque page.

    Le pied de page ne suit pas le fil du document : il reste collé au bas
    de la feuille, filet compris, comme sur les modèles du cabinet.
    """
    def _peindre(canevas, doc):
        if not texte:
            return
        canevas.saveState()
        paragraphe = Paragraph(texte, styles['pied'])
        _, hauteur = paragraphe.wrap(doc.width, 2 * cm)
        base = doc.bottomMargin - hauteur - 0.15 * cm
        canevas.setStrokeColor(NOIR)
        canevas.setLineWidth(0.8)
        canevas.line(doc.leftMargin, base + hauteur + 0.22 * cm,
                     doc.leftMargin + doc.width, base + hauteur + 0.22 * cm)
        paragraphe.drawOn(canevas, doc.leftMargin, base)
        canevas.restoreState()
    return _peindre


# --------------------------------------------------------------------------
# Document complet
# --------------------------------------------------------------------------

def generer_document_pdf(document, profil=None):
    """Produit le PDF d'une facture, d'une proforma ou d'un reçu."""
    styles = _styles()
    entreprise = document.entreprise
    est_recu = document.type_document == TypeDocument.RECU
    flux = BytesIO()

    modele = SimpleDocTemplate(
        flux, pagesize=A4,
        leftMargin=2.0 * cm, rightMargin=2.0 * cm,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title=f"{TypeDocument.LABELS.get(document.type_document, 'Document')} "
              f"{document.numero}",
        author=entreprise.nom if entreprise else 'Posiges',
    )

    contenu = [_entete_logo(profil, entreprise, styles), Spacer(1, 16)]

    # --- Titre centré et souligné ---
    titre = TypeDocument.TITRES.get(document.type_document, 'DOCUMENT')
    contenu.append(Paragraph(
        f'<u>{titre}</u>',
        styles['titre'] if est_recu else styles['titre_rouge']))
    contenu.append(Spacer(1, 16))

    # --- Identification ---
    contenu.append(_bloc_identification(document, styles))
    contenu.append(Spacer(1, 18))

    # --- Résumé ---
    contenu.append(Paragraph(
        '<u>RÉSUMÉ DU PAIEMENT</u>' if est_recu
        else '<u>RÉSUMÉ DE LA TRANSACTION</u>', styles['section']))
    contenu.append(Spacer(1, 10))
    contenu.append(_tableau_recu(document, styles) if est_recu
                   else _tableau_facture(document, profil, styles))

    # --- Mentions ---
    contenu.append(Spacer(1, 10))
    piece = 'ce reçu' if est_recu else 'cette facture'
    courriel = (profil.email if profil and profil.email else '')
    contenu.append(Paragraph(
        f"Veuillez IMPRIMER {piece} pour référence. Pour toutes questions et "
        f"commentaires, veuillez nous contacter par e-mail : {courriel}",
        styles['note']))

    if document.note:
        contenu.append(Spacer(1, 6))
        contenu.append(Paragraph(
            f"<b>Informations complémentaires :</b> {document.note}",
            styles['note']))

    if document.type_document == TypeDocument.PROFORMA:
        contenu.append(Spacer(1, 6))
        contenu.append(Paragraph(
            "Cette facture proforma ne constitue pas une demande de paiement. "
            "Elle vaut devis et devient exigible après acceptation.",
            styles['note']))

    if document.statut == StatutDocument.ANNULE:
        contenu.append(Spacer(1, 8))
        contenu.append(Paragraph(
            '<font color="#C00000"><b>DOCUMENT ANNULÉ</b></font>', styles['note']))

    # --- Informations bancaires (facture et proforma) ---
    if not est_recu:
        contenu += _bloc_bancaire(profil, styles)

    # --- Signature et pied de page ---
    contenu += _bloc_direction(profil, styles)

    peindre_pied = _dessiner_pied(
        profil.ligne_pied_legal() if profil else '', styles)
    modele.build(contenu, onFirstPage=peindre_pied, onLaterPages=peindre_pied)
    flux.seek(0)
    return flux


# ==========================================================================
# RAPPORT D'ACCOMPAGNEMENT
# ==========================================================================

_STATIC_IMG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'static', 'img')


def _chemin_logo(chemin_cabinet, fichier_defaut):
    """Chemin d'un logo : celui chargé par le cabinet s'il existe, sinon le
    logo livré avec l'application (static/img)."""
    if chemin_cabinet and os.path.exists(chemin_cabinet):
        return chemin_cabinet
    defaut = os.path.join(_STATIC_IMG, fichier_defaut)
    return defaut if os.path.exists(defaut) else None


def _echapper_pdf(texte):
    return (str(texte).strip().replace('&', '&amp;')
            .replace('<', '&lt;').replace('>', '&gt;'))


def _lignes_de(texte):
    """Découpe un texte multi-lignes en lignes non vides."""
    return [l.strip() for l in (texte or '').split('\n') if l.strip()]


def _peintre_rapport(canvas, doc, logo_ak, entete, raison, pied):
    """En-tête (logo AK à gauche + identité) et pied de page (bandeau légal +
    numéro) répétés à l'identique sur CHAQUE page du rapport."""
    largeur, hauteur = A4
    canvas.saveState()

    # --- En-tête : logo AK toujours en haut à gauche ---
    if logo_ak:
        try:
            lecteur = ImageReader(logo_ak)
            lw, lh = lecteur.getSize()
            h = 1.15 * cm
            w = lw * h / lh
            canvas.drawImage(logo_ak, 2 * cm, hauteur - 1.7 * cm, width=w,
                             height=h, preserveAspectRatio=True, mask='auto')
        except Exception:
            pass
    canvas.setFillColor(colors.HexColor('#1C3D5A'))
    canvas.setFont('Helvetica-Bold', 8.5)
    canvas.drawRightString(largeur - 2 * cm, hauteur - 1.05 * cm,
                           raison or 'AK World Business Services')
    if entete:
        canvas.setFillColor(GRIS)
        canvas.setFont('Helvetica-Oblique', 6.6)
        canvas.drawRightString(largeur - 2 * cm, hauteur - 1.42 * cm, entete[:120])
    canvas.setStrokeColor(colors.HexColor('#1C3D5A'))
    canvas.setLineWidth(0.6)
    canvas.line(2 * cm, hauteur - 1.85 * cm, largeur - 2 * cm, hauteur - 1.85 * cm)

    # --- Pied : bandeau légal centré + numéro de page ---
    canvas.setStrokeColor(colors.HexColor('#CCCCCC'))
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, 1.55 * cm, largeur - 2 * cm, 1.55 * cm)
    canvas.setFont('Helvetica', 6.4)
    canvas.setFillColor(GRIS)
    y = 1.2 * cm
    for ligne in _envelopper(pied, 135)[:2]:
        canvas.drawCentredString(largeur / 2, y, ligne)
        y -= 0.32 * cm
    canvas.setFont('Helvetica', 7)
    canvas.drawRightString(largeur - 2 * cm, 1.2 * cm, f'Page {doc.page}')
    canvas.restoreState()


def _envelopper(texte, largeur_max):
    """Coupe un texte en lignes d'au plus `largeur_max` caractères, sans
    briser les mots — pour le bandeau légal de pied de page."""
    mots = (texte or '').split()
    lignes, courante = [], ''
    for mot in mots:
        if len(courante) + len(mot) + 1 > largeur_max:
            lignes.append(courante)
            courante = mot
        else:
            courante = f'{courante} {mot}'.strip()
    if courante:
        lignes.append(courante)
    return lignes or ['']


def generer_rapport_pdf(rapport, profil=None, cabinet=None):
    """Produit le PDF d'un rapport rédigé dans l'application.

    Structure calquée sur le modèle de rapport du cabinet :
      · en-tête (logo AK) et pied (bandeau légal) sur chaque page ;
      · 4 pages de préambule automatiques et paramétrables — couverture avec
        logo FDFP, sommaire, présentation du cabinet, habilitations FDFP ;
      · les sections du rapport (titres et corps modifiables, ajoutables) ;
      · l'annexe « Analyse graphique » et la signature de la direction.
    """
    import gestion_commerciale as gc
    from reportlab.lib.enums import TA_JUSTIFY

    base = getSampleStyleSheet()
    styles = {
        'titre': ParagraphStyle(
            'TitreR', parent=base['Title'], fontName='Helvetica-Bold',
            fontSize=17, textColor=colors.HexColor('#1C3D5A'), spaceAfter=6),
        'couverture': ParagraphStyle(
            'CouvR', parent=base['Title'], fontName='Helvetica-Bold',
            fontSize=22, leading=27, textColor=colors.HexColor('#1C3D5A'),
            alignment=TA_CENTER, spaceAfter=8),
        'sous': ParagraphStyle(
            'SousR', parent=base['Normal'], fontName='Helvetica-Oblique',
            fontSize=10, textColor=GRIS, alignment=TA_CENTER, spaceAfter=20),
        'centre': ParagraphStyle(
            'CentreR', parent=base['Normal'], fontName='Helvetica',
            fontSize=10.5, leading=16, alignment=TA_CENTER, spaceAfter=4),
        'section': ParagraphStyle(
            'SectionR', parent=base['Heading2'], fontName='Helvetica-Bold',
            fontSize=13, textColor=colors.HexColor('#1C3D5A'),
            spaceBefore=16, spaceAfter=8),
        'corps': ParagraphStyle(
            'CorpsR', parent=base['Normal'], fontName='Helvetica',
            fontSize=10, leading=15, alignment=TA_JUSTIFY, spaceAfter=6),
        'signataire': ParagraphStyle(
            'SignataireR', parent=base['Normal'], fontName='Helvetica',
            fontSize=10, leading=14),
    }

    raison = cabinet.raison_sociale if cabinet else 'AK World Business Services'
    entete = (cabinet.entete_pages if cabinet else '') or ''
    pied = (cabinet.ligne_pied_legal() if cabinet else '') or (
        'AK World Business Services — Abidjan, Côte d\'Ivoire')
    logo_ak = _chemin_logo(cabinet.logo if cabinet else None, 'logo_ak_world.png')
    logo_fdfp = _chemin_logo(cabinet.logo_fdfp if cabinet else None, 'logo_fdfp.png')

    sections = gc.garantir_sections(rapport)

    def peintre(canvas, doc):
        _peintre_rapport(canvas, doc, logo_ak, entete, raison, pied)

    def construire(pages_toc):
        """Compose la liste des flowables. Les marqueurs invisibles servent au
        sommaire à connaître la page réelle de chaque partie. Chaque saut de
        page est géré ici (les fonctions de page n'en portent pas), pour éviter
        toute page blanche."""
        story = []
        story += _page_couverture(rapport, cabinet, logo_fdfp, styles)

        story.append(PageBreak())
        story += _page_sommaire(rapport, sections, styles, pages_toc)

        story += [PageBreak(), _Marqueur('presentation')]
        story += _page_presentation(cabinet, styles)

        habilitations = _page_habilitations(cabinet, styles)
        if habilitations:
            story += [PageBreak(), _Marqueur('habilitations')]
            story += habilitations

        story.append(PageBreak())
        story += [
            Paragraph(rapport.titre, styles['titre']),
            Paragraph(
                f"{rapport.periode or ''} — édité le "
                f"{rapport.date_creation.strftime('%d/%m/%Y')}"
                + (f" par {rapport.cree_par.nom}" if rapport.cree_par else ''),
                styles['sous']),
        ]
        for section in sections:
            if not (section.corps or '').strip() and not section.titre.strip():
                continue
            story.append(_Marqueur(f'section-{section.id}'))
            story.append(Paragraph(_echapper_pdf(section.titre), styles['section']))
            for paragraphe in _lignes_de(section.corps):
                story.append(Paragraph(_echapper_pdf(paragraphe), styles['corps']))

        annexe = _annexe_analyse_graphique(rapport, styles)
        if annexe:
            story += [PageBreak(), _Marqueur('annexe')]
            story += annexe
        story += _bloc_direction_rapport(cabinet, raison, styles)
        return story

    def nouveau_doc(destination):
        return _RapportDoc(
            destination, pagesize=A4,
            leftMargin=2 * cm, rightMargin=2 * cm,
            topMargin=2.4 * cm, bottomMargin=2 * cm,
            title=rapport.titre, author=raison)

    # 1re passe : relever la page réelle de chaque partie (sommaire vide).
    brouillon = nouveau_doc(BytesIO())
    brouillon.build(construire({}), onFirstPage=peintre, onLaterPages=peintre)

    # 2e passe : le sommaire porte désormais les vrais numéros de page.
    flux = BytesIO()
    nouveau_doc(flux).build(construire(brouillon.pages_toc),
                            onFirstPage=peintre, onLaterPages=peintre)
    flux.seek(0)
    return flux


class _Marqueur(Flowable):
    """Repère invisible : le document note la page où il tombe, pour renseigner
    le sommaire avec les vrais numéros de page (construction en deux passes)."""
    def __init__(self, label):
        Flowable.__init__(self)
        self.label = label
        self.width = self.height = 0

    def draw(self):
        pass


class _RapportDoc(SimpleDocTemplate):
    """Document qui mémorise la page atteinte par chaque marqueur."""
    def __init__(self, *args, **kwargs):
        SimpleDocTemplate.__init__(self, *args, **kwargs)
        self.pages_toc = {}

    def afterFlowable(self, flowable):
        if isinstance(flowable, _Marqueur):
            self.pages_toc[flowable.label] = self.page


def _page_couverture(rapport, cabinet, logo_fdfp, styles):
    """Page 1 : domaines, distinctions et contacts réunis dans un cadre (le
    « carré » du modèle), le logo FDFP au-dessus, le titre du rapport en bas."""
    contenu = [Spacer(1, 12)]

    if logo_fdfp is not None:
        contenu.append(Paragraph('Agréé par le', styles['centre']))
        img = _image_ajustee(logo_fdfp, largeur_max=4 * cm, hauteur_max=2.8 * cm)
        if img is not None:
            img.hAlign = 'CENTER'
            contenu.append(img)
        contenu.append(Spacer(1, 16))

    # --- Le carré : contenu du cabinet encadré ---
    style_cadre = ParagraphStyle('CadreR', parent=styles['centre'],
                                 fontSize=10, leading=15, spaceAfter=2)
    style_dist = ParagraphStyle('CadreDistR', parent=styles['centre'],
                               fontSize=8.5, leading=12, textColor=GRIS)
    interieur = []
    for domaine in _lignes_de(cabinet.domaines_activite if cabinet else ''):
        interieur.append(Paragraph('• ' + _echapper_pdf(domaine), style_cadre))
    distinctions = _lignes_de(cabinet.distinctions if cabinet else '')
    if distinctions:
        interieur.append(Spacer(1, 8))
        for ligne in distinctions:
            interieur.append(Paragraph(_echapper_pdf(ligne), style_dist))
    contacts = _lignes_de(cabinet.contacts_referent if cabinet else '')
    if contacts:
        interieur.append(Spacer(1, 8))
        for contact in contacts:
            interieur.append(Paragraph('<b>' + _echapper_pdf(contact) + '</b>',
                                       style_cadre))

    if interieur:
        cadre = Table([[interieur]], colWidths=[13 * cm])
        cadre.hAlign = 'CENTER'
        cadre.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1.3, colors.HexColor('#1C3D5A')),
            ('INNERGRID', (0, 0), (-1, -1), 0, colors.white),
            ('LEFTPADDING', (0, 0), (-1, -1), 18),
            ('RIGHTPADDING', (0, 0), (-1, -1), 18),
            ('TOPPADDING', (0, 0), (-1, -1), 16),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 16),
        ]))
        contenu.append(cadre)

    contenu.append(Spacer(1, 44))
    titre_couv = (cabinet.type_rapport if cabinet and cabinet.type_rapport
                  else "RAPPORT DE MISSION D'ÉTAT DES LIEUX")
    contenu.append(Paragraph(_echapper_pdf(titre_couv), styles['couverture']))
    nom_entreprise = rapport.entreprise.nom if rapport.entreprise else ''
    if nom_entreprise:
        contenu.append(Paragraph(_echapper_pdf(nom_entreprise.upper()),
                                 styles['couverture']))
    contenu.append(Paragraph(rapport.periode or '', styles['sous']))
    return contenu


def _page_sommaire(rapport, sections, styles, pages_toc):
    """Page 2 : sommaire en tableau LIBELLÉ | PAGE, avec les vrais numéros de
    page relevés lors de la première passe de composition."""
    contenu = [Paragraph('SOMMAIRE', styles['section']), Spacer(1, 8)]

    entrees = [('Présentation du cabinet', pages_toc.get('presentation')),
               ("Nos domaines d'habilitation FDFP", pages_toc.get('habilitations'))]
    for section in sections:
        if (section.corps or '').strip() or section.titre.strip():
            entrees.append((section.titre, pages_toc.get(f'section-{section.id}')))
    entrees.append(('Annexe — Analyse graphique', pages_toc.get('annexe')))

    style_lib = ParagraphStyle('SomLib', parent=styles['corps'], fontSize=11,
                               leading=15, spaceAfter=0)
    lignes = [[Paragraph('<b>LIBELLÉ</b>', style_lib), Paragraph('<b>PAGE</b>', style_lib)]]
    for libelle, page in entrees:
        lignes.append([Paragraph(_echapper_pdf(libelle), style_lib),
                       str(page) if page else '—'])
    table = Table(lignes, colWidths=[14.5 * cm, 2.5 * cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1C3D5A')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (1, 1), (1, -1), 11),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F8FB')]),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    contenu.append(table)
    return contenu


def _page_presentation(cabinet, styles):
    """Page 3 : présentation du cabinet reproduite comme le modèle papier — un
    tableau encadré où chaque sous-titre (« ## » dans le texte) forme une bande
    bleue, suivie de son contenu (les « - » deviennent des puces)."""
    contenu = [Paragraph('PRÉSENTATION DU CABINET', styles['section']),
               Spacer(1, 6)]
    if cabinet is None:
        return contenu

    style_bande = ParagraphStyle(
        'PresBande', fontName='Helvetica-Bold', fontSize=10,
        textColor=colors.white, alignment=TA_CENTER, leading=13)
    style_intro = ParagraphStyle(
        'PresIntro', parent=styles['corps'], fontName='Helvetica-Oblique',
        fontSize=9.5, leading=13, spaceAfter=2)
    style_puce = ParagraphStyle(
        'PresPuce', parent=styles['corps'], fontSize=9.5, leading=13,
        spaceAfter=1, leftIndent=10, firstLineIndent=-8)

    lignes = []                 # lignes du tableau (une colonne)
    commandes = []              # styles par index de ligne
    corps_courant = []
    index = 0

    def vider_corps():
        nonlocal index
        if corps_courant:
            lignes.append([list(corps_courant)])
            index += 1
            corps_courant.clear()

    for brut in (cabinet.presentation or '').split('\n'):
        texte = brut.strip()
        if not texte:
            continue
        if texte.startswith('## '):
            vider_corps()
            lignes.append([Paragraph(_echapper_pdf(texte[3:].strip()), style_bande)])
            commandes += [
                ('BACKGROUND', (0, index), (0, index), colors.HexColor('#1C3D5A')),
                ('TOPPADDING', (0, index), (0, index), 5),
                ('BOTTOMPADDING', (0, index), (0, index), 5),
            ]
            index += 1
        elif texte.startswith('- '):
            corps_courant.append(Paragraph('•&nbsp; ' + _echapper_pdf(texte[2:]),
                                           style_puce))
        else:
            corps_courant.append(Paragraph(_echapper_pdf(texte), style_intro))
    vider_corps()

    if lignes:
        table = Table(lignes, colWidths=[17 * cm])
        table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#1C3D5A')),
            ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#BBBBBB')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ] + commandes))
        contenu.append(table)
    return contenu


def _page_habilitations(cabinet, styles):
    habilitations = _lignes_de(cabinet.habilitations_fdfp if cabinet else '')
    if not habilitations:
        return []
    contenu = [Paragraph("NOS DOMAINES D'HABILITATION PAR FDFP", styles['section']),
               Spacer(1, 6)]
    donnees = [['N°', "Domaine d'habilitation (agrément)"]]
    for index, domaine in enumerate(habilitations, start=1):
        donnees.append([str(index), _echapper_pdf(domaine)])
    table = Table(donnees, colWidths=[1.5 * cm, 15.5 * cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1C3D5A')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9.5),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F8FB')]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    contenu.append(table)
    return contenu


def _bloc_direction_rapport(cabinet, raison, styles):
    contenu = [Spacer(1, 22), Paragraph('<u>LA DIRECTION</u>', styles['section'])]
    signature = _image_ajustee(cabinet.signature if cabinet else None,
                               largeur_max=5.5 * cm, hauteur_max=2.4 * cm)
    if signature is not None:
        signature.hAlign = 'LEFT'
        contenu += [Spacer(1, 4), signature]
    else:
        contenu.append(Spacer(1, 38))
    if cabinet and cabinet.signataire:
        contenu.append(Paragraph(cabinet.signataire, styles['signataire']))
    contenu.append(Spacer(1, 14))
    contenu.append(Paragraph(
        f"Rapport établi par {raison} dans le cadre de l'accompagnement "
        f"en gestion. Montants exprimés en francs CFA.",
        ParagraphStyle('PiedR', parent=styles['corps'],
                       fontName='Helvetica-Oblique', fontSize=8.5,
                       textColor=GRIS)))
    return contenu


def _annexe_analyse_graphique(rapport, styles):
    """Annexe reprenant les graphiques du tableau de bord (mêmes séries,
    mêmes couleurs) : c'est l'analyse graphique remise avec le rapport,
    et non plus seulement le texte rédigé par le consultant."""
    if not rapport.entreprise_id or not rapport.annee:
        return []

    donnees = comptes.donnees_graphiques(rapport.annee, entreprise_id=rapport.entreprise_id)
    exploitation = comptes.compte_exploitation(
        entreprise_id=rapport.entreprise_id,
        date_debut=date(rapport.annee, 1, 1), date_fin=date(rapport.annee, 12, 31))
    tresorerie = comptes.compte_tresorerie(
        entreprise_id=rapport.entreprise_id,
        date_debut=date(rapport.annee, 1, 1), date_fin=date(rapport.annee, 12, 31))

    contenu = [Paragraph('Annexe — Analyse graphique', styles['section'])]

    kpi = [
        ['Indicateur', 'Valeur'],
        ["Chiffre d'affaires", f"{exploitation['chiffre_affaires']:,.0f} FCFA".replace(',', ' ')],
        ['Charges', f"{exploitation['charges']:,.0f} FCFA".replace(',', ' ')],
        ['Résultat net', f"{exploitation['resultat_net']:,.0f} FCFA".replace(',', ' ')],
        ['Taux de marge', f"{exploitation['taux_marge']:.1f} %"],
        ['Trésorerie disponible', f"{tresorerie['total_disponible']:,.0f} FCFA".replace(',', ' ')],
    ]
    table = Table(kpi, colWidths=[8 * cm, 6 * cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1C3D5A')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    contenu += [table, Spacer(1, 14)]

    for titre_graphique, image_png in graphiques_pdf.generer_planches(donnees):
        contenu.append(Paragraph(titre_graphique, styles['sous']))
        lecteur = ImageReader(image_png)
        largeur, hauteur = lecteur.getSize()
        echelle = min(15 * cm / largeur, 8 * cm / hauteur)
        image_png.seek(0)
        contenu.append(Image(image_png, width=largeur * echelle, height=hauteur * echelle))
        contenu.append(Spacer(1, 10))

    return contenu
