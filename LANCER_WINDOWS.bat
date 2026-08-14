@echo off
chcp 65001 >nul 2>&1
title AK World - Application de gestion
cd /d "%~dp0"

echo.
echo ================================================================
echo    AK WORLD - Demarrage automatique
echo ================================================================
echo.

REM ---- 1. Verifier que Python est installe ----------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python n'est pas installe sur cet ordinateur.
    echo.
    echo     Telechargez-le ici : https://www.python.org/downloads/
    echo     IMPORTANT : cochez la case "Add Python to PATH"
    echo     pendant l'installation, puis relancez ce fichier.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [1/5] %%v detecte.

REM ---- 2. Trouver ou creer l'environnement virtuel --------------
REM Reconnait ".venv" (cree par VS Code) comme "venv" : pas de doublon.
set "ENVDIR="
if exist ".venv\Scripts\python.exe" set "ENVDIR=.venv"
if not defined ENVDIR if exist "venv\Scripts\python.exe" set "ENVDIR=venv"

if not defined ENVDIR (
    echo [2/5] Creation de l'environnement... ^(une seule fois, ~30 s^)
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo [X] Echec de la creation de l'environnement.
        echo     Reinstallez Python en cochant "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )
    set "ENVDIR=venv"
) else (
    echo [2/5] Environnement deja present ^(%ENVDIR%^).
)

call "%ENVDIR%\Scripts\activate.bat"

REM ---- 3. Installer les dependances -----------------------------
if not exist "%ENVDIR%\.installe" (
    echo [3/5] Installation des composants... ^(patientez 1-2 minutes^)
    echo.
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ================================================================
        echo  [X] ECHEC DE L'INSTALLATION
        echo ================================================================
        echo.
        echo  Lisez le message rouge ci-dessus. Les causes habituelles :
        echo.
        echo   - Pas de connexion internet
        echo     ^> verifiez votre connexion et relancez ce fichier
        echo.
        echo   - "Microsoft Visual C++ 14.0 is required"
        echo     ^> un composant tente de se compiler. Envoyez-moi le
        echo       message complet, je corrigerai la dependance.
        echo.
        echo   - Un pare-feu ou antivirus bloque pip
        echo     ^> desactivez-le temporairement et relancez
        echo.
        echo ================================================================
        echo.
        pause
        exit /b 1
    )
    echo ok > "%ENVDIR%\.installe"
    echo.
) else (
    echo [3/5] Composants deja installes.
)

REM ---- 3 bis. Bibliotheques Google, si credentials.json est present -----
REM Sans elles, la publication vers Google Sheets echoue silencieusement.
if exist "credentials.json" (
    if not exist "%ENVDIR%\.installe_google" (
        echo       credentials.json detecte : installation du support Google...
        pip install -r requirements-google.txt
        if errorlevel 1 (
            echo.
            echo       [!] Installation du support Google echouee.
            echo           L'application demarre quand meme, mais la publication
            echo           vers Google Sheets ne fonctionnera pas.
            echo           Diagnostic : python diagnostic_google.py
            echo.
        ) else (
            echo ok > "%ENVDIR%\.installe_google"
            echo       Support Google installe.
        )
        echo.
    )
)

REM ---- 4. Configurer et preparer la base ------------------------
echo [4/5] Configuration...
python configurer.py
if errorlevel 1 (
    echo.
    echo [X] Echec de la configuration. Message ci-dessus.
    echo.
    pause
    exit /b 1
)

REM ---- 5. Lancer -------------------------------------------------
echo [5/5] Demarrage du site...
echo.
echo ================================================================
echo    LE SITE EST EN LIGNE : http://localhost:5000
echo ================================================================
echo.
echo    Votre navigateur va s'ouvrir automatiquement.
echo.
echo    ATTENTION : NE FERMEZ PAS CETTE FENETRE.
echo    Le site s'arrete si vous la fermez.
echo.
echo    IDENTIFIANTS DE DEMONSTRATION
echo    ----------------------------------------------------------
echo    Gerant      : gerant@akworld.com      / AkWorld2026!
echo    Consultant  : consultant@akworld.com  / Consultant2026!
echo    Client TPE  : bella@jusdebella.ci     / Bella2026!
echo    ----------------------------------------------------------
echo.
echo    Pour ARRETER le site : appuyez sur Ctrl + C.
echo.
echo ================================================================
echo.

start "" http://localhost:5000
python app.py

echo.
echo Le site est arrete.
pause
