# POSIGES V1 — Programme Simplifié de Gestion pour les Entreprises

*Développé pour AK World Business Services*

Application web Flask/Python articulée autour de **deux parties prenantes** :

- **AK World Business Services** — le cabinet. Il installe l'application chez
  ses clients, paramètre les comptes à distance, accède à la totalité de leurs
  données, publie les classeurs de suivi et rédige les rapports
  d'accompagnement.
- **L'entreprise accompagnée** — le client. Elle gère son activité au
  quotidien : gérant, comptable / caissier / trésorier, et utilisateurs
  standards.

L'application n'est pas un outil qu'on livre en disant « allez gérer votre
entreprise ». Elle s'inscrit dans une logique d'accompagnement : au lieu
d'installer trois ou quatre tableaux Excel chez le client, on lui donne un seul
endroit où saisir, et le cabinet garde la main sur le paramétrage et le suivi.

**Le gérant saisit une seule fois ; le compte d'exploitation, le compte de
trésorerie et le journal de bord se remplissent tout seuls.** Personne ne
saisit jamais un compte à la main.

---

## Table des matières

1. [Installation](#1-installation)
2. [Premier démarrage](#2-premier-démarrage)
3. [Les six rôles](#3-les-six-rôles)
4. [Comment tout fonctionne](#4-comment-tout-fonctionne)
5. [Le compte d'exploitation et le compte de trésorerie](#5-le-compte-dexploitation-et-le-compte-de-trésorerie)
6. [Les budgets — la colonne « Prévu »](#6-les-budgets--la-colonne-prévu)
7. [Logos, factures et reçus](#7-logos-factures-et-reçus)
8. [Activer Google Drive et Google Sheets](#8-activer-google-drive-et-google-sheets)
9. [Mise en route pour une nouvelle entreprise cliente](#9-mise-en-route-pour-une-nouvelle-entreprise-cliente)
10. [Déploiement en production](#10-déploiement-en-production)
11. [Structure du code](#11-structure-du-code)
12. [Maintenance et dépannage](#12-maintenance-et-dépannage)

---

## 1. Installation

**Le plus simple : double-cliquez sur le lanceur.**

- Windows → `LANCER_WINDOWS.bat`
- macOS / Linux → `LANCER_MAC_LINUX.sh`

Il installe tout, crée la base et ouvre votre navigateur. Rien d'autre à faire.

### Installation manuelle

```bash
pip install -r requirements.txt
```

Puis copiez `.env.example` en `.env`.

### Les trois fichiers de dépendances

| Fichier | Quand l'utiliser | Contenu |
|---|---|---|
| `requirements.txt` | **Toujours** — usage local | Flask, SQLAlchemy, openpyxl, reportlab |
| `requirements-google.txt` | Seulement pour activer Drive/Sheets | Bibliothèques Google |
| `requirements-production.txt` | Seulement pour le déploiement en ligne | Tout + gunicorn + PostgreSQL |

Cette séparation est volontaire. Le pilote PostgreSQL (`psycopg2`) exige que
PostgreSQL soit installé sur la machine : l'inclure dans l'installation locale
ferait échouer l'installation sans aucun bénéfice, puisque SQLite suffit en
local.

Pour générer une clé secrète solide :

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 2. Premier démarrage

```bash
python app.py
```

L'application démarre sur **http://localhost:5000**.

Au tout premier lancement, le compte de direction du cabinet est créé
automatiquement :

| Champ | Valeur |
|---|---|
| Adresse e-mail | `cabinet@akworld.com` |
| Mot de passe | `AkWorld2026!` |

> ⚠️ **Changez ce mot de passe immédiatement** via « Mon profil ».

### Charger des données de démonstration (développement uniquement)

```bash
python seed.py
```

Cela crée 2 entreprises accompagnées, 8 comptes couvrant tous les rôles,
7 familles de produits, un catalogue, environ 250 opérations réparties sur
l'année et 4 budgets déjà renseignés — pour que les colonnes « Prévu » et
« Écart » soient parlantes dès la première connexion. Les identifiants
s'affichent dans le terminal.

### Vérifier que tout fonctionne

```bash
python test_app.py
```

158 tests contrôlent que chaque rôle accède bien à ce qui le concerne — et à
rien d'autre —, que les enchaînements automatiques fonctionnent, et que le
chiffre d'affaires ne se confond jamais avec la trésorerie. Lancez-les après
chaque modification du code.

---

## 3. Les six rôles

### Côté cabinet AK World

| Rôle | Ce qu'il voit | Ce qu'il peut faire |
|---|---|---|
| **Cabinet** | Toutes les entreprises accompagnées et le périmètre interne | Tout : paramètres, création des comptes, budgets, rapports, historique |
| **Consultant** | Uniquement les entreprises qui lui sont assignées | Consulter, corriger, publier, rédiger les rapports de ses clients |
| **Responsable de département** | Uniquement son département interne AK World | Créer, modifier, supprimer les opérations de son département |

### Côté entreprise accompagnée

| Rôle | Ce qu'il voit | Ce qu'il peut faire |
|---|---|---|
| **Gérant** | Tout de son entreprise | Vue complète, **saisie des budgets**, exports, création de comptes dans son entreprise, lecture des rapports |
| **Comptable / Caissier / Trésorier** | Tout de son entreprise | Mêmes tableaux de bord et mêmes exports que le gérant, **sans** la saisie des budgets |
| **Utilisateur standard** | **Uniquement ses propres saisies** | Enregistrer les entrées et sorties, gérer le stock, tableau de bord simplifié |

L'utilisateur standard est le point important : son tableau de bord ne montre
que les opérations qu'il a lui-même enregistrées. Il ne voit ni les chiffres de
ses collègues, ni ceux de la direction.

### Tableau récapitulatif des droits

| Fonction | Cabinet | Consultant | Responsable | Gérant | Comptable | Standard |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Saisir des opérations | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Gérer le stock | ✔ | ✔ | — | ✔ | ✔ | ✔ |
| Tableau de bord complet | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| Tableau de bord de ses seules saisies | — | — | — | — | — | ✔ |
| Compte d'exploitation et de trésorerie | ✔ | ✔ | — | ✔ | ✔ | — |
| Graphiques d'analyse | ✔ | ✔ | — | ✔ | ✔ | — |
| Exports Excel et publication Sheets | ✔ | ✔ | — | ✔ | ✔ | — |
| **Saisir les budgets** | ✔ | — | — | ✔ | — | — |
| **Rédiger les rapports** | ✔ | ✔ | — | — | — | — |
| Lire les rapports publiés | ✔ | ✔ | — | ✔ | ✔ | — |
| Créer des comptes | ✔ | — | — | ✔ (les siens) | — | — |
| Paramètres et historique | ✔ | — | — | — | — | — |

Ces cloisonnements sont appliqués **côté serveur**. Masquer un bouton ne
suffirait pas : les routes elles-mêmes renvoient une erreur 403, et une
quarantaine de tests automatiques le vérifient.

---

## 4. Comment tout fonctionne

### Le principe : une seule saisie, tout le reste automatique

```
              SAISIE (le seul geste manuel)
   +--------------------------------------------+
   |  Une operation, ou une facture / un recu    |
   |  - Date                                     |
   |  - Entree ou sortie                         |
   |  - Categorie (liste fermee)                 |
   |  - Client (auto-completion)                 |
   |  - Montant + mode de paiement               |
   |  - Reference + photo du justificatif        |
   +---------------------+----------------------+
                         |
     +---------+---------+---------+-------------+
     v         v         v         v             v
  Journal    Compte    Compte    Stock      Google Drive
  de bord  d'exploit. de treso.            + Google Sheets
     |         |         |         |             |
     +---------+----+----+---------+-------------+
                    v
       Tableau de bord, Excel et rapports PDF
```

### Le journal de bord

Colonnes : **Date, Libellé, Catégorie, Entrée, Sortie, Mode de paiement,
Solde, Pièce justificative**.

Le solde est **cumulatif** : chaque ligne montre la position de caisse après
l'opération. C'est ce qui fait du journal un outil de contrôle — dès que le
solde devient négatif, il y a une erreur de saisie ou un trou de caisse, et
l'application le signale.

### Les catégories sont une liste fermée

L'utilisateur ne tape jamais une catégorie au clavier : il en choisit une dans
une liste. Sans cela, comme l'a résumé le cabinet, « on va avoir des
informations qui vont dans tous les sens » et le rapprochement budget / réalisé
devient impossible.

La liste change selon le sens de l'opération :

- **une entrée** propose les *familles de produits* de l'entreprise, ainsi que
  les encaissements de trésorerie (recouvrement de créances, subventions,
  apports en capital, emprunts, découverts) ;
- **une sortie** propose les *postes de charges*, variables puis fixes.

### Les familles de produits

Le compte d'exploitation ne présente pas un chiffre d'affaires global mais un
chiffre d'affaires **par famille**. Un éleveur ne crée pas une ligne par race de
chien : il crée la famille « Canins », y range ses articles, et lit le chiffre
d'affaires de l'ensemble.

Chaque famille créée dans l'onglet **Familles** devient automatiquement une
ligne de la section « I - PRODUITS ». Les articles du catalogue sont rattachés
à une famille depuis leur fiche.

### Chiffre d'affaires et trésorerie ne se confondent jamais

C'est la règle la plus importante de l'application.

> Vous vendez 10 chiots à 100 000 F. Vous établissez une **facture** de
> 1 000 000 F en juillet. Le client vous verse 500 000 F d'avance.
>
> - Le **compte d'exploitation** de juillet enregistre **1 000 000 F** de
>   chiffre d'affaires : la vente est faite.
> - Le **compte de trésorerie** de juillet n'enregistre que **500 000 F** :
>   c'est tout ce qui est entré en caisse.
> - Les 500 000 F restants apparaîtront en trésorerie le mois où le client
>   paiera, sur la ligne « Encaissement des créances clients ».

Concrètement, une facture réglée intégralement au comptant engendre une
écriture par famille de produits, au mode de paiement réel. Une facture à
crédit ou partiellement réglée engendre une écriture de chiffre d'affaires au
mode « crédit » — qui ne touche pas la trésorerie — plus une écriture
d'encaissement pour la somme réellement reçue.

Une entreprise peut ainsi être rentable sans avoir d'argent (elle vend à
crédit), ou avoir beaucoup de liquidités sans être rentable (elle travaille
avec l'argent de ses fournisseurs). Les deux comptes servent précisément à
distinguer ces deux situations.

### Ce qui se passe à chaque enregistrement

1. Un **numéro de pièce** est généré automatiquement (`FAC-2026-00042`).
2. Le **client** est reconnu s'il existe déjà, sinon créé à la volée.
3. Le **justificatif** est archivé dans Google Drive (si configuré).
4. L'action est inscrite dans l'**historique** (qui, quoi, quand).
5. Le **stock** des produits vendus est décrémenté.
6. Tous les **comptes se recalculent** — rien n'est stocké, tout est agrégé à
   la demande. Une opération corrigée se répercute immédiatement partout.

---

## 5. Le compte d'exploitation et le compte de trésorerie

Ce sont les deux tableaux remis par le cabinet. Ils partagent la même ossature :
une ligne par rubrique et, **pour chaque mois, trois colonnes** — Prévu,
Réalisé, Écart.

```
                    Janvier                Février              ...
   Libelle    Prevu Realise Ecart    Prevu Realise Ecart
   -------------------------------------------------------------
   I - PRODUITS
   CA Canins  600k   580k   -20k     650k   700k   +50k
   CA Volaille 300k  310k   +10k     320k   290k   -30k
   Total Produit ...
```

- **Prévu** — le budget saisi par le gérant (voir section 6).
- **Réalisé** — calculé en direct depuis le journal de bord.
- **Écart** — toujours **Réalisé − Prévu**. Un écart négatif signale qu'on est
  en dessous de ce qui était prévu.

### Compte d'exploitation — la rentabilité

| Section | Contenu |
|---|---|
| **I - PRODUITS** | Chiffre d'affaires, une ligne par famille de produits |
| **II - CHARGES VARIABLES** | Achats, intrants, emballages, sous-traitance, transports, stockage, commissions |
| **III - MARGE BRUTE** | Total produit − Total charges variables |
| **IV - CHARGES FIXES** | Loyer, salaires, charges sociales, impôts, entretien, énergie, assurances, honoraires, publicité, transport, agios |
| **V - RÉSULTAT D'EXPLOITATION** | Marge brute − Total charges fixes |

### Compte de trésorerie — la liquidité

| Section | Contenu |
|---|---|
| **I - Encaissements (A)** | Ventes au comptant, recouvrement des créances clients, subventions, apports en capital, emprunts, découverts autorisés |
| **II - Décaissements (B)** | Les mêmes postes de charges, effectivement payés |
| **Solde de début (C)** | Report du solde de fin du mois précédent |
| **Solde de fin (D)** | **D = C + A − B** |

Le solde de fin d'un mois ouvre automatiquement le mois suivant.

### Une ligne de recueil

Le plan de rubriques comporte une ligne « Autres charges diverses » et une ligne
« Autres encaissements » qui **n'apparaissent que si elles portent un montant**.
Elles servent de filet : une reprise de données ou une dépense atypique ne
disparaît jamais silencieusement des comptes.

---

## 6. Les budgets — la colonne « Prévu »

Onglet **Budget**, accessible au cabinet et au gérant de l'entreprise.

Le gérant choisit entre le **budget d'exploitation** et le **budget de
trésorerie**, puis remplit un tableau : une ligne par rubrique, une colonne par
mois, de janvier à décembre. Une seule valeur par case — le prévisionnel.

- Les cases laissées vides ne sont pas enregistrées.
- Le total annuel de chaque ligne se met à jour pendant la saisie.
- Le bouton **« Reprendre <année précédente> »** recopie le budget de l'exercice
  précédent : beaucoup de rubriques ne bougent pas d'une année sur l'autre (le
  loyer, les assurances, les honoraires).
- Le budget de trésorerie demande en plus le **solde au 1er janvier**, point de
  départ du calcul des soldes mensuels.

Ce que le gérant saisit ici devient la colonne « Prévu » du compte
correspondant, et l'écart se recalcule immédiatement.

---

## 7. Logos, factures et reçus

### Charger un logo

| Qui | Où | Ce que ça habille |
|---|---|---|
| Le cabinet | Onglet **Paramètres** | L'en-tête de l'application et les rapports d'accompagnement |
| Chaque entreprise | Onglet **Ventes → Informations de facturation** | L'en-tête de ses factures, proformas et reçus |

Formats acceptés : PNG, JPG, GIF. Un fond transparent donne le meilleur rendu.
Une **signature ou un cachet** peut être chargé au même endroit : il apparaît
sous la mention « LA DIRECTION » en bas des documents.

Les logos des clients ne se mélangent jamais : une facture émise par une
entreprise porte **son** logo, jamais celui du cabinet.

### Les trois pièces

| Pièce | Ce qu'elle constate | Effet sur les comptes |
|---|---|---|
| **Facture** | La vente | Chiffre d'affaires ; trésorerie seulement si encaissée |
| **Facture proforma** | Rien — c'est un devis | Aucun |
| **Reçu de paiement** | L'encaissement | Chiffre d'affaires et trésorerie |

Chaque document reprend la mise en page des modèles du cabinet : logo en haut à
gauche, titre centré et souligné, bloc d'identification (N° de commande, date de
transaction, N° de pièce, catégorie de la transaction, coordonnées du client),
tableau des articles, totaux, informations bancaires pour une facture, signature
de la direction, et bandeau légal centré en pied de page.

Sur un reçu, chaque ligne peut porter son propre **montant payé**, ou la mention
**(Remise)** lorsqu'une prestation est offerte au client.

---

## 8. Activer Google Drive et Google Sheets

L'application fonctionne parfaitement **sans** cette étape (les justificatifs
sont alors stockés localement dans `uploads/`). Suivez ces étapes si vous voulez
l'archivage Drive et le classeur de suivi partagé.

### Étape 0 — Installer les bibliothèques Google

```bash
pip install -r requirements-google.txt
```

### Étape 1 — Projet Google Cloud

1. Ouvrez https://console.cloud.google.com
2. Créez un projet (ex. « AK World Gestion »).
3. Menu **APIs et services → Bibliothèque** : activez **Google Drive API**
   puis **Google Sheets API**.

### Étape 2 — Compte de service

1. Menu **IAM et administration → Comptes de service → Créer**.
2. Donnez-lui un nom (ex. « akworld-app »), validez.
3. Onglet **Clés → Ajouter une clé → Créer une clé → JSON**.
4. Renommez le fichier téléchargé en **`credentials.json`** et placez-le à la
   racine du projet, à côté de `app.py`.
5. Ouvrez ce fichier et notez la valeur de `client_email`.

### Étape 3 — Partager le dossier Drive

1. Dans Google Drive, créez un dossier (ex. « AK World — Justificatifs »).
2. Clic droit → **Partager** → collez le `client_email` → droit **Éditeur**.
3. L'ID du dossier est la fin de l'URL
   `https://drive.google.com/drive/folders/`**`1AbCdEf...`**
4. Reportez cet ID dans `.env` : `GOOGLE_DRIVE_FOLDER_ID=1AbCdEf...`

### Étape 4 — Partager le Google Sheet

1. Créez un Google Sheet et **partagez-le** avec le `client_email` en
   droit **Éditeur**.
2. L'ID est dans l'URL :
   `https://docs.google.com/spreadsheets/d/`**`1XyZ...`**`/edit`
3. Reportez-le dans `.env` : `GOOGLE_SHEET_ID=1XyZ...`

### Le classeur publié

Bouton **« Publier vers Google Sheets »** (onglet Rapports, ou depuis un compte).
Le classeur contient :

| Onglet | Contenu |
|---|---|
| **Journal de bord** | Date, libellé, catégorie, entrée, sortie, mode de paiement, solde cumulé, pièce |
| **Compte d'exploitation** | Janvier à décembre, trois colonnes par mois, plus le total annuel |
| **Compte de trésorerie** | Même présentation, côté liquidité |
| **Produits et services** | Catalogue complet et familles de produits |
| **Données** | Journal à plat, pour vos propres tris, filtres et tableaux croisés |

L'adresse du classeur **ne change jamais** d'une publication à l'autre : le lien
communiqué au client reste valable.

### Un Drive et un Sheet par entreprise

Dans « Entreprises → Modifier », chaque entreprise peut avoir son propre
`drive_folder_id` et son propre `sheet_id`. Laissés vides, le dossier Drive est
créé automatiquement au nom de l'entreprise et le Sheet global est utilisé.

### Si la synchronisation échoue

**Aucune opération n'est jamais perdue.** Si Google est indisponible ou mal
configuré, l'opération reste enregistrée en base et un message précis explique
la cause. L'export Excel, lui, reste toujours disponible.

Pour un diagnostic complet : `python diagnostic_google.py`

---

## 9. Mise en route pour une nouvelle entreprise cliente

Le parcours complet, dans l'ordre :

1. **Cabinet** → onglet « Entreprises » → **+ Nouvelle entreprise**.
   Renseignez le nom, le secteur, le contact, et cochez le ou les consultants
   qui la suivront.
2. **Cabinet** → onglet « Utilisateurs » → **Créer un compte**, rôle
   *Gérant de l'entreprise*, en sélectionnant l'entreprise créée. Notez le mot
   de passe provisoire.
3. Transmettez au gérant : l'adresse du site, son e-mail, son mot de passe
   provisoire, et demandez-lui de le changer à la première connexion.
4. Faites-lui renseigner ses **informations de facturation** et charger son
   **logo** — une seule fois, elles apparaîtront sur tous ses documents.
5. Créez ensemble ses **familles de produits**, puis son **catalogue**.
6. Le gérant saisit son **budget** d'exploitation et de trésorerie.
7. Le gérant crée lui-même les comptes de son comptable et de ses caissiers.
8. Côté cabinet, le consultant retrouve l'entreprise dans son tableau de bord et
   peut publier le classeur et rédiger les rapports quand il le souhaite.

**Conseil terrain :** les gérants de TPE/PME saisissent rarement tout, tout de
suite. Commencez par leur demander uniquement les ventes du jour et les achats
importants. On enrichit ensuite.

---

## 10. Déploiement en production

### Préparation

```bash
# 1. Générer une vraie clé secrète
python -c "import secrets; print(secrets.token_hex(32))"
```

Puis dans `.env` (ou les variables d'environnement de l'hébergeur) :

```
SECRET_KEY=<la clé générée>
COOKIE_SECURE=true
DATABASE_URL=<URL PostgreSQL fournie par l'hébergeur>
```

> **Important :** SQLite convient pour tester, mais **pas** pour la production
> avec plusieurs utilisateurs simultanés. Utilisez PostgreSQL. Tous les calculs
> comptables sont écrits pour fonctionner sur les deux moteurs.

### Sur Render (le plus simple)

1. Poussez le code sur GitHub (le `.gitignore` exclut déjà `.env`,
   `credentials.json` et la base — **vérifiez-le avant de pousser**).
2. Sur render.com : **New → Web Service**, connectez le dépôt.
3. Build command : `pip install -r requirements.txt`
4. Start command : `gunicorn "app:app"`
5. **New → PostgreSQL**, puis copiez l'*Internal Database URL* dans la variable
   d'environnement `DATABASE_URL` du service web.
6. Ajoutez `SECRET_KEY`, `COOKIE_SECURE=true`, et les variables Google si vous
   les utilisez.
7. Pour `credentials.json` : utilisez un *Secret File* Render.

Les tables et le compte de direction sont créés automatiquement au premier
démarrage.

### Sur un VPS (Ubuntu)

```bash
gunicorn "app:app" --bind 127.0.0.1:8000 --workers 3 --timeout 120
```

Configurez ensuite nginx en reverse proxy avec un certificat TLS
(Let's Encrypt / certbot), puis créez un service systemd pour le redémarrage
automatique.

### Sauvegardes — à ne pas négliger

- une **sauvegarde quotidienne** de la base de données ;
- une copie régulière du dossier `uploads/`, qui contient les justificatifs et
  les logos.

---

## 11. Structure du code

```
posiges/
├── app.py                    Routes Flask, autorisations, orchestration
├── config.py                 Configuration (base, Google, fichiers)
├── models.py                 Modèles et PLAN DE RUBRIQUES
├── migrations.py             Mise à niveau automatique des bases existantes
├── comptes.py                Calculs comptables — les deux comptes détaillés
├── budgets.py                Saisie et structure des budgets
├── gestion_commerciale.py    Ventes, stock, fiches, rapports — logique métier
├── routes_commerciales.py    Écrans catalogue, familles, ventes, stock, rapports
├── documents_pdf.py          Factures, proformas, reçus et rapports en PDF
├── exports.py                Classeur Excel à formules vivantes
├── google_integration.py     Drive (justificatifs) et Sheets (journal partagé)
├── sheets_publication.py     Publication du classeur de suivi
├── seed.py                   Données de démonstration (développement)
├── test_app.py               158 tests fonctionnels
├── gerer_comptes.py          Gestion des comptes en ligne de commande
├── diagnostic_google.py      Diagnostic de la configuration Google
├── templates/
│   ├── base.html               Mise en page, navigation et feuille de style
│   ├── login.html              Connexion
│   ├── dashboard.html          Tableau de bord complet
│   ├── espace_client.html      Tableau de bord simplifié (utilisateur standard)
│   ├── _tableau_compte.html    Macro du tableau Prévu / Réalisé / Écart
│   ├── compte_exploitation.html
│   ├── compte_tresorerie.html
│   ├── budget_accueil.html     Choix du budget à saisir
│   ├── budget_form.html        Grille de saisie Mois 1 → Mois 12
│   ├── familles.html           Familles de produits
│   ├── journal.html            Journal de bord avec solde cumulé
│   ├── operation_form.html     Saisie d'opération (catégories en liste fermée)
│   ├── catalogue.html          Produits et services
│   ├── article_form.html       Fiche article, avec sa famille
│   ├── ventes.html             Factures, proformas et reçus
│   ├── document_form.html      Émission d'un document
│   ├── document_detail.html    Détail et encaissement
│   ├── stock.html              Mouvements de stock
│   ├── clients.html / fiche_client.html
│   ├── fournisseurs.html / fiche_fournisseur.html
│   ├── rapports.html           Exports Excel, PDF et publication Sheets
│   ├── rapports_cabinet.html / rapport_form.html
│   ├── profil_facturation.html Coordonnées, logo et signature de l'entreprise
│   ├── parametres.html         Identité du cabinet
│   ├── utilisateurs.html       Gestion des comptes
│   ├── entreprises.html / entreprise_form.html
│   ├── historique.html         Journal d'audit
│   ├── profil.html             Changement de mot de passe
│   ├── choisir_entreprise.html
│   └── erreur.html             Pages 403 / 404
├── static/js/
│   ├── chart.min.js            Chart.js servi en local (fonctionne hors connexion)
│   └── graphiques.js           Graphiques du tableau de bord
├── requirements*.txt
├── LANCER_WINDOWS.bat / LANCER_MAC_LINUX.sh
├── render.yaml / Procfile
├── .env.example
└── .gitignore
```

### Où intervenir pour les modifications courantes

| Je veux… | Fichier à modifier |
|---|---|
| Ajouter une rubrique de charge ou d'encaissement | `models.py` → `CHARGES_VARIABLES`, `CHARGES_FIXES`, `ENCAISSEMENTS_DIRECTS` |
| Ajouter un mode de paiement | `models.py` → classe `ModePaiement` |
| Ajouter un département interne | `models.py` → classe `Departement` |
| Changer les droits d'un rôle | `models.py` → propriétés `peut_*` de `User`, et décorateurs dans `app.py` |
| Changer un calcul comptable | `comptes.py` |
| Modifier la présentation d'un compte | `templates/_tableau_compte.html` |
| Modifier la mise en forme d'un export Excel | `exports.py` |
| Modifier les onglets du classeur publié | `sheets_publication.py` |
| Changer la mise en page des factures et reçus | `documents_pdf.py` |
| Changer un graphique du tableau de bord | `static/js/graphiques.js` |
| Changer les couleurs ou le style | `templates/base.html` (bloc `<style>`) |
| Ajouter une page | `app.py` (nouvelle route) + un fichier dans `templates/` |

---

## 12. Maintenance et dépannage

### Après toute modification du code

```bash
python test_app.py
```

Si un test échoue, la modification a cassé quelque chose — corrigez avant de
déployer.

### Si vous modifiez `models.py`

**Rien à faire.** Le module `migrations.py` compare le schéma des modèles à
celui de la base au démarrage et ajoute les colonnes manquantes. C'est
indispensable ici : l'application est installée chez des clients et mise à jour
à distance, on ne peut pas demander à un gérant de TPE de lancer une commande.

Les reprises de **données** (et non de schéma) ne s'appliquent qu'une seule
fois, grâce au champ `version_donnees` des paramètres du cabinet. C'est ce qui
évite qu'une reprise de rôles se rejoue et renomme à tort les comptes créés
depuis.

En développement, pour repartir de zéro :

```bash
python -c "from app import creer_app; from models import db; app=creer_app(); app.app_context().push(); db.drop_all()"
python seed.py
```

### Reprise d'une base antérieure à Posiges V1

Elle est automatique au premier démarrage :

- l'ancien rôle `gerant` (direction du cabinet) devient `cabinet` ;
- l'ancien rôle `client` (gérant de l'entreprise) devient `gerant` ;
- les catégories saisies librement sont converties vers le plan de rubriques —
  « Loyer » devient la charge fixe *Loyer*, « Achat matières premières » devient
  la charge variable *Intrants de production*, et tout ce qui n'est pas reconnu
  bascule vers *Autres charges diverses* : rien n'est perdu.

### Problèmes courants

| Symptôme | Cause probable | Solution |
|---|---|---|
| Les colonnes « Prévu » sont à zéro | Aucun budget saisi pour l'exercice | Onglet Budget → saisir le budget |
| Un graphique affiche « pas assez de données » | Aucune opération sur l'exercice choisi | Vérifiez l'exercice sélectionné en haut de l'écran |
| Le chiffre d'affaires paraît trop élevé | Un emprunt ou une subvention mal catégorisé | Ces entrées doivent utiliser une rubrique « Autres encaissements », pas une famille de produits |
| Le solde du journal est négatif | Entrée oubliée ou montant saisi deux fois | Le journal signale l'anomalie ; reprenez les lignes du mois |
| « Fichier credentials Google introuvable » | Google non configuré | Normal si vous ne l'utilisez pas. Sinon, voir section 8 |
| Erreur 403 inattendue | L'utilisateur n'a pas le rôle requis | Vérifiez son rôle dans « Utilisateurs » |
| Justificatif refusé | Extension non autorisée | Voir `EXTENSIONS_AUTORISEES` dans `config.py` |
| Logo absent des documents | Chargé côté cabinet au lieu de l'entreprise | Les factures portent le logo du **profil de facturation** de l'entreprise |
