# Rechnungsverarbeitung

Liest Lieferanten-Rechnungen (PDF, Scan oder Foto), extrahiert die Daten per
KI, prüft sie und schreibt sie in eine monatliche Excel-Datei. Unsichere Fälle
werden zur manuellen Prüfung aussortiert statt stillschweigend gebucht.

---

## Für Anwender

### Was das Programm tut

1. Rechnungen aus dem Ordner `input` werden eingelesen (PDF, JPG, PNG).
2. Die Daten werden ausgelesen und geprüft (Summen, Steuersätze, UID, Datum).
3. Korrekte Rechnungen landen in einer Excel-Datei pro Monat (Ordner `output`).
4. Auffällige Rechnungen wandern in den Ordner `review` mit einer Begründung –
   diese bitte von Hand prüfen und ggf. selbst in die Excel eintragen.
5. Fertig verarbeitete Originale werden im Ordner `processed` aufbewahrt.

### Einrichtung (einmalig)

Voraussetzungen, die einmal installiert werden müssen:

1. **Python 3.11 oder neuer** – von <https://www.python.org/downloads/>.
   Beim Setup unbedingt den Haken bei **"Add Python to PATH"** setzen.
2. **Tesseract OCR** (nur nötig für gescannte PDFs und Fotos) – vom
   UB-Mannheim-Installer <https://github.com/UB-Mannheim/tesseract/wiki>.
   Bei der Installation unter "Additional language data" **German** anhaken.

Danach genügt ein Doppelklick auf **`run.bat`**. Beim ersten Start richtet sich
das Programm selbst ein (das dauert einige Minuten – Fenster offen lassen). Bei
jedem weiteren Start öffnet sich die Anwendung direkt im Browser.

> **Beim allerersten Start** kann Windows eine Warnung zeigen ("Der Computer
> wurde durch Windows geschützt"). Das ist bei solchen Start-Dateien normal.
> Auf **"Weitere Informationen"** und dann **"Trotzdem ausführen"** klicken –
> diese Abfrage erscheint nur einmal.

### Verknüpfung auf dem Desktop (empfohlen)

Damit das Programm bequem vom Desktop aus startet, **keine** Kopie der
`run.bat` auf den Desktop legen (sie funktioniert nur in ihrem Ordner),
sondern eine Verknüpfung:

1. Im Projektordner mit der rechten Maustaste auf `run.bat` klicken → **Kopieren**.
2. Auf dem Desktop mit der rechten Maustaste klicken → **Verknüpfung einfügen**
   (nicht "Einfügen").
3. Die Verknüpfung nach Belieben umbenennen, z. B. "Rechnungsverarbeitung".

Der Projektordner selbst darf verschoben werden, wohin man möchte – nur die
Dateien **innerhalb** des Ordners dürfen nicht umsortiert werden.

### API-Key eintragen

Das Programm nutzt einen KI-Dienst. Beim ersten Start fragt die Oberfläche nach
einem API-Key und speichert ihn lokal – danach erscheint die Abfrage nicht mehr.

Einen kostenlosen Google-Gemini-Key gibt es hier:

1. <https://aistudio.google.com/app/apikey> öffnen und mit einem Google-Konto
   anmelden.
2. Auf **"Create API key"** klicken und den Key kopieren.
3. Im Programm in das Eingabefeld einfügen, **"Key speichern"** klicken.

Der Key verlässt den eigenen Rechner nicht.

> **Hinweis Datenschutz:** Kostenlose KI-Zugänge verarbeiten die übermittelten
> Daten unter Umständen zu Trainingszwecken. Für echte Geschäftsdaten sollte ein
> kostenpflichtiger Zugang mit Auftragsverarbeitungsvertrag genutzt werden
> (siehe "KI-Anbieter wechseln"). Für lokale Verarbeitung ohne jeden
> Datenversand eignet sich Ollama (siehe unten).

### Bedienung

1. Rechnungen per Drag-and-drop in das Upload-Feld ziehen.
2. Auf **"… Rechnung(en) verarbeiten"** klicken.
3. Nach dem Lauf: gebuchte Rechnungen stehen in der Excel-Datei (unten zum
   Download), auffällige Fälle unter "Manuelle Prüfung". Jeden geprüften Fall
   mit **"Erledigt"** aus der Liste entfernen.

### Wenn der KI-Dienst nicht erreichbar ist

Cloud-Dienste haben gelegentlich Störungen oder Kapazitätsgrenzen. Passiert das
mitten in einem Lauf, bricht das Programm **kontrolliert ab** – es geht nichts
verloren und nichts wird doppelt gebucht. Die noch nicht verarbeiteten
Rechnungen bleiben im Eingang. Einfach etwas später erneut auf "verarbeiten"
klicken; das Programm macht dort weiter, wo es aufgehört hat.

---

## Für Entwickler / Administration

Technische Details, Architektur und Anbieter-Konfiguration stehen in der
`README.md` im Proj-Repository. Das Wichtigste in Kürze:

### Projektstruktur

```
bill_agent/
├── app.py            Streamlit-Oberflaeche
├── main.py           Ablaufsteuerung (auch als CLI: python main.py)
├── config.py         Konfiguration, .env-Handling, Provider-Registry
├── models.py         Pydantic-Schema (Grenze fuer KI-Ausgaben)
├── ingest.py         Datei-Erkennung, Textlayer-Erkennung
├── ocr.py            OCR fuer Scans/Fotos (Tesseract)
├── llm.py            Provider (Ollama, Gemini, OpenAI-kompatibel)
├── validation.py     Geschaeftsregeln, Eskalationsgruende
├── excel_writer.py   Monats-Excel, Dedup, Spaltenformat
├── logging_setup.py  Logging mit Run-ID
├── prompts/          Versionierte Prompt-Dateien
├── tools/            Entwickler-Werkzeuge (Testdaten-Generator)
├── .env.example      Vorlage fuer .env
└── run.bat           Windows-Starter (self-setup)
```

### Manuelle Einrichtung (ohne run.bat)

```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # dann .env ausfuellen
streamlit run app.py
```

Tesseract als Systempaket (für OCR):

```
# Debian/Ubuntu
sudo apt install tesseract-ocr tesseract-ocr-deu
# Arch
sudo pacman -S tesseract tesseract-data-deu
```

### KI-Anbieter wechseln

Der Anbieter wird allein über `.env` bestimmt (`LLM_PROVIDER`). Nach jeder
Änderung an `.env` die Anwendung neu starten.

**Ollama** – lokal, kostenlos, keine Datenübertragung:

```
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3-coder
```

**Google Gemini** – kostenloser Zugang, guter Frontier-Vergleich:

```
LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-3.5-flash
GEMINI_API_KEY=...
```

**OpenAI-kompatibel** – Groq, Mistral, OpenRouter u. a.:

```
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile
OPENAI_COMPAT_API_KEY=...
```

Eine Anbindung an die Claude-API ist vorbereitet, aber noch nicht aktiviert
(`LLM_PROVIDER=claude` – auf Anfrage).

### Prüf- und Eskalationsregeln

Eine Rechnung wandert nach `review`, wenn: Pflichtfelder fehlen; Summe aus
Netto + Steuer nicht zum Bruttobetrag passt (Toleranz 0,02); die Steuer eines
Postens nicht zum Satz passt; die UID-Nummer ein ungültiges Format hat; das
Rechnungsdatum in der Zukunft liegt; die Fälligkeit nicht ermittelbar ist; ein
unüblicher Steuersatz auftritt; die KI keine gültigen Daten liefert (nach einem
Korrekturversuch). Schwellwerte stehen in `config.py`.

### Testdaten

`python tools/make_test_invoices.py` erzeugt einen Satz synthetischer
Rechnungen (verschiedene Layouts, Scans, Fotos, bewusste Fehlerfälle) im Ordner
`testdata`. Reine Entwicklerhilfe, nicht Teil der Auslieferung.

### Support

Jeder Lauf hat eine Referenz-Nummer (Run-ID), die in der Oberfläche und in
`logs/bill_agent.log` erscheint. Bei Problemen die Referenz nennen – damit lässt
sich der betreffende Lauf im Log eindeutig nachvollziehen.
