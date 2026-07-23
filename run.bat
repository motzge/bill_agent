@echo off
REM ===================================================================
REM  Rechnungsverarbeitung starten (Windows)
REM  Erster Start: richtet sich selbst ein (dauert einige Minuten).
REM  Danach: startet nur noch die Anwendung.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo   Rechnungsverarbeitung wird gestartet ...
echo.

REM --- 1. Python 3.11+ vorhanden? -----------------------------------
python --version >nul 2>&1
if errorlevel 1 goto no_python

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto no_python

REM --- 2. Erststart: virtuelle Umgebung + Pakete --------------------
REM Sentinel file is written only after a fully successful install,
REM so an aborted setup is retried instead of silently skipped.
if not exist ".venv\.setup_complete" (
    echo   Erste Einrichtung laeuft. Das dauert einige Minuten,
    echo   bitte das Fenster nicht schliessen ...
    echo.

    if not exist ".venv\Scripts\python.exe" (
        python -m venv .venv
        if errorlevel 1 (
            echo   FEHLER: Die virtuelle Umgebung konnte nicht angelegt werden.
            echo.
            pause
            exit /b 1
        )
    )

    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip >nul
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   FEHLER: Die benoetigten Pakete konnten nicht installiert werden.
        echo   Besteht eine Internetverbindung?
        echo.
        pause
        exit /b 1
    )

    echo ok> ".venv\.setup_complete"
    echo.
    echo   Einrichtung abgeschlossen.
    echo.
) else (
    call ".venv\Scripts\activate.bat"
)

REM --- 3. Streamlit-Erststart-Abfrage unterdruecken ------------------
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
    >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

REM --- 4. Anwendung starten -----------------------------------------
echo   Die Anwendung oeffnet sich gleich im Browser.
echo   Falls nicht, im Browser aufrufen: http://localhost:8501
echo   Zum Beenden dieses Fenster schliessen.
echo.

python -m streamlit run app.py --server.port=8501

echo.
echo   Die Anwendung wurde beendet.
pause
exit /b 0

REM --- Fehlerausgabe -------------------------------------------------
:no_python
echo   FEHLER: Es wurde kein Python 3.11 oder neuer gefunden.
echo.
echo   Bitte Python 3.11 oder neuer von https://www.python.org/downloads/
echo   installieren. WICHTIG: beim Setup den Haken bei
echo   "Add Python to PATH" setzen.
echo.
pause
exit /b 1