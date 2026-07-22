"""Streamlit UI for bill_agent.

Run with: streamlit run app.py

Everything the operator sees is German; code, comments and logs stay
English. The UI shares the exact same pipeline as the CLI (main.run_batch),
so both entry points can never drift apart.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import (
    INPUT_DIR,
    OUTPUT_DIR,
    REVIEW_DIR,
    SUPPORTED_EXTENSIONS,
    ConfigError,
    api_key_label,
    ensure_directories,
    load_settings,
    needs_api_key,
    save_api_key,
)
from llm import LlmError, create_provider
from logging_setup import new_run_id, setup_logging
from main import run_batch
from ocr import OcrError, assert_tesseract_available

OUTCOME_LABELS = {
    "booked": "gebucht",
    "review": "zur manuellen Prüfung",
    "duplicate": "Duplikat (bereits gebucht)",
    "aborted": "abgebrochen",
}


def _input_files() -> list[Path]:
    return sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _review_pairs() -> list[tuple[Path, Path]]:
    """(reason_file, original_file) pairs, newest first."""
    pairs = []
    for reason in REVIEW_DIR.glob("*.reason.txt"):
        original = reason.with_name(reason.name.removesuffix(".reason.txt"))
        if original.exists():
            pairs.append((reason, original))
    return sorted(pairs, key=lambda p: p[0].stat().st_mtime, reverse=True)


def _month_files() -> list[Path]:
    return sorted(OUTPUT_DIR.glob("Rechnungen_*.xlsx"), reverse=True)


st.set_page_config(page_title="Rechnungsverarbeitung", page_icon="🧾", layout="wide")
ensure_directories()

st.title("🧾 Rechnungsverarbeitung")

#one-time API key setup (only in Claude mode, only while key missing)
if needs_api_key():
    label = api_key_label()
    st.warning(f"Einmalige Einrichtung: Für die Verarbeitung wird ein {label}-API-Key benötigt.")
    with st.form("api-key-setup"):
        entered_key = st.text_input(
            f"{label}-API-Key hier einfügen",
            type="password",
            help="Der Key wird nur lokal auf diesem Rechner gespeichert.",
        )
        if st.form_submit_button("Key speichern", type="primary"):
            try:
                save_api_key(entered_key)
            except ConfigError as exc:
                st.error(str(exc))
            else:
                st.success("API-Key gespeichert. Die Einrichtung ist abgeschlossen.")
                st.rerun()
    st.stop()  #nothing else renders until the key is in place

#upload
#GOTCHA: st.file_uploader keeps its files across reruns. After a run moved the originals out of input/, the old code re-saved every widget file 
#processed invoices got resurrected. Rotating the widget key after saving gives us a fresh, empty uploader instead.
if "upload_round" not in st.session_state:
    st.session_state.upload_round = 0

uploaded = st.file_uploader(
    "Rechnungen hier ablegen (PDF, JPG, PNG)",
    type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
    accept_multiple_files=True,
    key=f"uploader-{st.session_state.upload_round}",
)
if uploaded:
    saved = 0
    for file in uploaded:
        # never trust client-side names: strip any path components
        target = INPUT_DIR / Path(file.name).name
        if not target.exists():
            target.write_bytes(file.getbuffer())
            saved += 1
    st.session_state.flash = f"{saved} Datei(en) in den Eingang übernommen."
    st.session_state.upload_round += 1
    st.rerun()

if flash := st.session_state.pop("flash", None):
    st.success(flash)

#status
pending = _input_files()
review_pairs = _review_pairs()
col1, col2, col3 = st.columns(3)
col1.metric("Im Eingang", len(pending))
col2.metric("Zur Prüfung", len(review_pairs))
col3.metric("Monatsdateien", len(_month_files()))
if pending:
    with st.expander(f"Dateien im Eingang ({len(pending)})"):
        st.caption(f"Ordner: {INPUT_DIR}")  #which folder the app really uses
        for path in pending:
            st.text(path.name)
        with st.popover("Eingang leeren"):
            st.warning("Alle Dateien im Eingang werden endgültig gelöscht.")
            if st.button("Ja, Eingang leeren", type="primary"):
                for path in pending:
                    path.unlink(missing_ok=True)
                st.session_state.flash = f"Eingang geleert ({len(pending)} Datei(en) gelöscht)."
                st.rerun()

#run 
if st.button(
    f"{len(pending)} Rechnung(en) verarbeiten",
    type="primary",
    disabled=not pending,
):
    run_id = new_run_id()
    setup_logging(run_id)
    try:
        settings = load_settings()
        assert_tesseract_available()
        provider = create_provider(settings)
        provider.verify()
    except (ConfigError, OcrError, LlmError, NotImplementedError) as exc:
        st.error(f"Start nicht möglich: {exc}")
        st.stop()

    progress = st.progress(0.0)
    with st.status("Verarbeitung läuft ...", expanded=True) as status:

        def on_progress(index: int, total: int, name: str, outcome: str) -> None:
            progress.progress(index / total)
            status.write(f"{name} → {OUTCOME_LABELS.get(outcome, outcome)}")

        stats = run_batch(provider, run_id, on_progress)
        status.update(label="Verarbeitung abgeschlossen", state="complete")

    if stats["aborted"]:
        st.error(
            "KI-Dienst nicht erreichbar — Lauf abgebrochen. Verbleibende "
            "Rechnungen bleiben im Eingang und werden beim nächsten Lauf "
            f"verarbeitet. (Referenz: {run_id})"
        )
    elif stats["review"] or stats["duplicate"]:
        st.warning(
            f"{stats['booked']} gebucht, "
            f"{stats['review'] + stats['duplicate']} zur manuellen Prüfung "
            f"(siehe unten). (Referenz: {run_id})"
        )
    else:
        st.success(f"Alle {stats['booked']} Rechnungen gebucht. (Referenz: {run_id})")
    st.button("Ansicht aktualisieren")  #any click triggers a Streamlit rerun

#review queue
st.divider()
st.subheader("Manuelle Prüfung")
if not review_pairs:
    st.caption("Nichts zu prüfen. 👍")
for reason_file, original in review_pairs:
    with st.expander(original.name):
        st.text(reason_file.read_text(encoding="utf-8"))
        st.download_button(
            "Original herunterladen",
            data=original.read_bytes(),
            file_name=original.name,
            key=f"dl-{original.name}",
        )
        if st.button("Erledigt – aus der Prüfliste entfernen", key=f"done-{original.name}"):
            original.unlink(missing_ok=True)
            reason_file.unlink(missing_ok=True)
            original.with_name(original.name + ".llm_output.txt").unlink(missing_ok=True)
            st.session_state.flash = f"'{original.name}' aus der Prüfung entfernt."
            st.rerun()
        st.caption(
            "Nach der Prüfung die Rechnung ggf. manuell in die Monats-Excel "
            "eintragen, dann auf Erledigt klicken."
        )

#monthly files
st.divider()
st.subheader("Monats-Excel-Dateien")
if not _month_files():
    st.caption("Noch keine Buchungen vorhanden.")
for month_file in _month_files():
    st.download_button(
        month_file.name,
        data=month_file.read_bytes(),
        file_name=month_file.name,
        key=f"dl-{month_file.name}",
    )