#!/usr/bin/env bash
# =====================================================================
#  AK World — Démarrage automatique (macOS / Linux)
#  Utilisation :  double-cliquez sur ce fichier,
#                 ou dans un terminal : ./LANCER_MAC_LINUX.sh
# =====================================================================

cd "$(dirname "$0")" || exit 1

echo ""
echo "================================================================"
echo "   AK WORLD — Démarrage automatique"
echo "================================================================"
echo ""

# ---- 1. Vérifier Python ---------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[X] Python 3 n'est pas installé sur cet ordinateur."
    echo ""
    echo "    macOS  : brew install python3"
    echo "             (ou https://www.python.org/downloads/)"
    echo "    Ubuntu : sudo apt install python3 python3-venv python3-pip"
    echo ""
    read -r -p "Appuyez sur Entrée pour fermer..."
    exit 1
fi
echo "[1/5] Python détecté ($(python3 --version))."

# ---- 2. Environnement virtuel ---------------------------------------
# Reconnaît ".venv" (créé par VS Code) aussi bien que "venv" : pas de doublon.
ENVDIR=""
[ -x ".venv/bin/python" ] && ENVDIR=".venv"
[ -z "$ENVDIR" ] && [ -x "venv/bin/python" ] && ENVDIR="venv"

if [ -z "$ENVDIR" ]; then
    echo "[2/5] Création de l'environnement... (une seule fois, ~30 s)"
    if ! python3 -m venv venv; then
        echo "[X] Échec. Sur Ubuntu, installez d'abord : sudo apt install python3-venv"
        read -r -p "Appuyez sur Entrée pour fermer..."
        exit 1
    fi
    ENVDIR="venv"
else
    echo "[2/5] Environnement déjà présent ($ENVDIR)."
fi

# shellcheck disable=SC1091
source "$ENVDIR/bin/activate"

# ---- 3. Dépendances --------------------------------------------------
if [ ! -f "$ENVDIR/.installe" ]; then
    echo "[3/5] Installation des composants... (patientez 1 à 2 minutes)"
    echo ""
    python -m pip install --upgrade pip
    if ! pip install -r requirements.txt; then
        echo ""
        echo "================================================================"
        echo " [X] ÉCHEC DE L'INSTALLATION"
        echo "================================================================"
        echo ""
        echo " Lisez le message d'erreur ci-dessus. Causes habituelles :"
        echo ""
        echo "  - Pas de connexion internet"
        echo "    > vérifiez la connexion et relancez ce fichier"
        echo ""
        echo "  - Un composant tente de se compiler"
        echo "    > envoyez-moi le message complet, je corrigerai"
        echo "      la dépendance en cause"
        echo ""
        echo "================================================================"
        echo ""
        read -r -p "Appuyez sur Entrée pour fermer..."
        exit 1
    fi
    echo ok > "$ENVDIR/.installe"
    echo ""
else
    echo "[3/5] Composants déjà installés."
fi

# ---- 3 bis. Bibliothèques Google, si credentials.json est présent -----
# Sans elles, la publication vers Google Sheets échoue silencieusement.
if [ -f "credentials.json" ] && [ ! -f "$ENVDIR/.installe_google" ]; then
    echo "      credentials.json détecté : installation du support Google..."
    if pip install -r requirements-google.txt; then
        echo ok > "$ENVDIR/.installe_google"
        echo "      Support Google installé."
    else
        echo ""
        echo "      [!] Installation du support Google échouée."
        echo "          L'application démarre quand même, mais la publication"
        echo "          vers Google Sheets ne fonctionnera pas."
        echo "          Diagnostic : python diagnostic_google.py"
    fi
    echo ""
fi

# ---- 4. Configuration et base ---------------------------------------
echo "[4/5] Configuration..."
if ! python configurer.py; then
    echo "[X] Échec de la configuration."
    read -r -p "Appuyez sur Entrée pour fermer..."
    exit 1
fi

# ---- 5. Lancement ----------------------------------------------------
echo "[5/5] Démarrage du site..."
echo ""
echo "================================================================"
echo "   LE SITE EST EN LIGNE : http://localhost:5000"
echo "================================================================"
echo ""
echo "   Votre navigateur va s'ouvrir automatiquement."
echo ""
echo "   IDENTIFIANTS DE DÉMONSTRATION"
echo "   -------------------------------------------------------------"
echo "   Gérant      : gerant@akworld.com      / AkWorld2026!"
echo "   Consultant  : consultant@akworld.com  / Consultant2026!"
echo "   Client TPE  : bella@jusdebella.ci     / Bella2026!"
echo "   -------------------------------------------------------------"
echo ""
echo "   ATTENTION : NE FERMEZ PAS CETTE FENÊTRE."
echo "   Le site s'arrête si vous la fermez."
echo ""
echo "   Pour ARRÊTER le site : appuyez sur Ctrl + C."
echo ""
echo "================================================================"
echo ""

# Ouvrir le navigateur après un court délai, sans bloquer
(
    sleep 3
    if command -v open >/dev/null 2>&1; then
        open http://localhost:5000            # macOS
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open http://localhost:5000        # Linux
    fi
) >/dev/null 2>&1 &

python app.py
