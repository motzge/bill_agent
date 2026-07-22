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

REM --- 1. Python vorhanden? -----------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo   FEHLER: Python wurde nicht gefunden.
    echo.
    echo   Bitte Python 3.11 oder neuer von https://www.python.org/downloads/
    echo   installieren. WICHTIG: beim Setup den Haken bei
    echo   "Add Python to PATH" setzen.
    echo.
    pause
    exit /b 1
)

REM --- 2. Erststart: virtuelle Umgebung + Pakete --------------------
if not exist ".venv\Scripts\python.exe" (
    echo   Erste Einrichtung laeuft. Das dauert einige Minuten,
    echo   bitte das Fenster nicht schliessen ...
    echo.
    python -m venv .venv
    if errorlevel 1 (
        echo   FEHLER: Die virtuelle Umgebung konnte nicht angelegt werden.
        pause
        exit /b 1
    )
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip >nul
    pip install -r requirements.txt
    if errorlevel 1 (
        echo   FEHLER: Die benoetigten Pakete konnten nicht installiert werden.
        echo   Besteht eine Internetverbindung?
        pause
        exit /b 1
    )
    echo.
    echo   Einrichtung abgeschlossen.
    echo.
) else (
    call ".venv\Scripts\activate.bat"
)

REM --- 3. Anwendung starten (oeffnet den Browser automatisch) -------
echo   Die Anwendung oeffnet sich gleich im Browser.
echo   Zum Beenden dieses Fenster schliessen.
echo.
streamlit run app.py

pause
