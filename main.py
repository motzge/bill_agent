"""bill_agent entry point.

Orchestrates the pipeline: discover -> (OCR) -> extract -> validate ->
book into Excel -> move file. Every invoice runs inside its own error
boundary: whatever happens at invoice 17 cannot touch invoices 1-16.

File flow guarantees:
- input/      untouched until an invoice reached a final state
- processed/  the invoice is booked in the monthly Excel
- review/     a human must look; <name>.reason.txt explains why
- LLM/network outage aborts the run cleanly; remaining files simply stay
  in input/ and are picked up by the next run (booking is idempotent).
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from config import (
    INPUT_DIR,
    OUTPUT_DIR,
    PROCESSED_DIR,
    PROCESSED_RETENTION_DAYS,
    REPAIR_ATTEMPTS,
    REVIEW_DIR,
    ConfigError,
    ensure_directories,
    load_settings,
)
from excel_writer import append_invoice
from ingest import discover_invoices
from llm import LLMProvider, LlmError, create_provider
from logging_setup import new_run_id, setup_logging
from models import InvoiceData, InvoiceSource, SourceKind, invoice_from_llm_output
from ocr import OcrError, assert_tesseract_available, extract_text
from validation import validate

logger = logging.getLogger(__name__)


class EscalationNeeded(Exception):
    """Raised inside the pipeline when an invoice needs human review."""

    def __init__(self, reasons: list[str], raw_output: str | None = None) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        #last raw LLM output for extraction failures - the key evidence
        #for prompt debugging, saved next to the reason file
        self.raw_output = raw_output


def main() -> int:
    run_id = new_run_id()

    try:
        settings = load_settings()
    except ConfigError as exc:
        #logging is not up yet - plain print is all we have
        print(f"Konfigurationsfehler: {exc}")
        return 1

    ensure_directories()
    setup_logging(run_id)
    logger.info("bill_agent run %s starting (provider=%s)", run_id, settings.llm_provider)

    try:
        assert_tesseract_available()
        provider = create_provider(settings)
        provider.verify()
    except (OcrError, LlmError, NotImplementedError) as exc:
        logger.error("Startup check failed: %s", exc)
        print(f"Start nicht möglich: {exc}")
        return 1

    stats = run_batch(provider, run_id)
    _print_summary(run_id, stats)
    return 0


def run_batch(
    provider: LLMProvider,
    run_id: str,
    on_progress: Callable[[int, int, str, str], None] | None = None,
) -> Counter[str]:
    """Process everything in input/. Shared by CLI and the Streamlit UI.

    on_progress(index, total, filename, outcome) fires after every invoice.
    """
    _cleanup_processed()
    stats: Counter[str] = Counter()
    sources = discover_invoices()
    total = len(sources)
    for index, source in enumerate(sources, start=1):
        try:
            outcome = _process_one(source, provider, run_id)
        except LlmError as exc:
            #infrastructure problem, not an invoice problem: stop cleanly,
            #leave this and all remaining files in input/ for the next run
            logger.error("LLM unreachable, aborting run: %s", exc)
            stats["aborted"] += 1
            if on_progress:
                on_progress(index, total, source.path.name, "aborted")
            break
        stats[outcome] += 1
        if on_progress:
            on_progress(index, total, source.path.name, outcome)
    return stats


def _cleanup_processed(retention_days: int = PROCESSED_RETENTION_DAYS) -> None:
    """Booked originals are kept as an audit trail, but not forever: the
    customer archives originals elsewhere, so processed/ cleans itself."""
    cutoff = time.time() - retention_days * 86_400
    removed = 0
    for path in PROCESSED_DIR.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    if removed:
        logger.info("Retention: deleted %d file(s) older than %d days from processed/",
                    removed, retention_days)


def _process_one(source: InvoiceSource, provider: LLMProvider, run_id: str) -> str:
    """Process a single invoice. Returns the outcome for the summary."""
    name = source.path.name
    logger.info("Processing %s (%s)", name, source.kind.value)
    try:
        source = _ensure_text(source)
        invoice = _extract_invoice(source, provider)
        result = validate(invoice)
        if not result.ok:
            _escalate(source.path, result.reasons, run_id)
            return "review"

        if not append_invoice(result.invoice, datetime.now(), OUTPUT_DIR):
            _escalate(source.path, [
                "Diese Rechnung ist bereits in der Monats-Excel gebucht "
                "(gleiche Rechnungsnummer und gleicher Lieferant).",
                "Falls es sich wirklich um eine andere Rechnung handelt, "
                "bitte manuell eintragen.",
            ], run_id)
            return "duplicate"

        _move_safe(source.path, PROCESSED_DIR)
        return "booked"

    except EscalationNeeded as exc:
        _escalate(source.path, exc.reasons, run_id, raw_output=exc.raw_output)
        return "review"
    except LlmError:
        raise  #outage handling belongs to the run loop, not per invoice
    except Exception:
        #last line of defense: full traceback goes to the log only, the reason file stays generic (security rule: no tracebacks to users)
        logger.exception("Unexpected error while processing %s", name)
        _escalate(source.path, [
            "Unerwarteter technischer Fehler bei der Verarbeitung.",
            f"Details stehen im Logfile unter Referenz {run_id}.",
        ], run_id)
        return "review"


def _ensure_text(source: InvoiceSource) -> InvoiceSource:
    """Digital PDFs already carry text; everything else goes through OCR."""
    if source.kind is SourceKind.DIGITAL_PDF:
        return source
    try:
        return dataclasses.replace(source, text=extract_text(source))
    except OcrError as exc:
        raise EscalationNeeded([f"Texterkennung (OCR) fehlgeschlagen: {exc}"]) from exc


def _extract_invoice(source: InvoiceSource, provider: LLMProvider) -> InvoiceData:
    """LLM extraction with up to REPAIR_ATTEMPTS repair rounds."""
    raw = provider.extract(source)
    last_error: Exception | None = None

    for attempt in range(REPAIR_ATTEMPTS + 1):
        try:
            return invoice_from_llm_output(raw)
        except (ValueError, ValidationError) as exc:
            last_error = exc
            if attempt < REPAIR_ATTEMPTS:
                logger.warning(
                    "Schema-invalid LLM output for %s, repair attempt %d",
                    source.path.name, attempt + 1,
                )
                raw = provider.repair(source, raw, str(exc))

    raise EscalationNeeded([
        "Die automatische Datenextraktion hat kein gültiges Ergebnis geliefert "
        "(auch nach Korrekturversuch nicht).",
        f"Letzter Fehler: {last_error}",
    ], raw_output=raw) from last_error


def _escalate(
    path: Path, reasons: list[str], run_id: str, raw_output: str | None = None
) -> None:
    moved = _move_safe(path, REVIEW_DIR)
    if raw_output:
        debug_file = moved.with_name(moved.name + ".llm_output.txt")
        debug_file.write_text(raw_output, encoding="utf-8")
        reasons = reasons + [
            f"Die Rohausgabe des KI-Modells liegt zur Analyse in {debug_file.name}."
        ]
    reason_file = moved.with_name(moved.name + ".reason.txt")
    lines = [
        f"Rechnung:  {moved.name}",
        f"Zeitpunkt: {datetime.now():%d.%m.%Y %H:%M}",
        f"Referenz:  {run_id}",
        "",
        "Gründe für die manuelle Prüfung:",
        *[f"  - {reason}" for reason in reasons],
        "",
        "Bitte prüfen und die Rechnung ggf. manuell in die Monats-Excel eintragen.",
        "",
    ]
    reason_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Escalated %s -> %s", moved.name, reason_file.name)


def _move_safe(path: Path, target_dir: Path) -> Path:
    """Move without ever overwriting: name.pdf -> name_1.pdf -> name_2.pdf"""
    target = target_dir / path.name
    counter = 1
    while target.exists():
        target = target_dir / f"{path.stem}_{counter}{path.suffix}"
        counter += 1
    shutil.move(str(path), str(target))
    return target


def _print_summary(run_id: str, stats: Counter[str]) -> None:
    total = sum(stats.values())
    logger.info("Run %s finished: %s", run_id, dict(stats) or "no files")
    print()
    print("-" * 52)
    print(f"Lauf abgeschlossen (Referenz: {run_id})")
    if total == 0:
        print("Keine Rechnungen im Ordner 'input' gefunden.")
    else:
        print(f"  Gebucht:                 {stats['booked']}")
        print(f"  Manuell zu prüfen:       {stats['review']}  (Ordner 'review')")
        print(f"  Duplikate:               {stats['duplicate']}  (Ordner 'review')")
        if stats["aborted"]:
            print("  Lauf vorzeitig beendet: KI-Dienst nicht erreichbar.")
            print("  Verbleibende Rechnungen werden beim nächsten Lauf verarbeitet.")
    print("-" * 52)


if __name__ == "__main__":
    sys.exit(main())