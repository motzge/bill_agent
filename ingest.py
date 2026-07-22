"""Input discovery for bill_agent.

Scans input/, classifies every file (digital PDF / scanned PDF / image) and
returns InvoiceSource objects. Digital PDFs leave here with their text
already extracted; scanned PDFs and images leave with text=None and get
their text later from ocr.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber

from config import INPUT_DIR, SUPPORTED_EXTENSIONS, TEXT_LAYER_MIN_CHARS
from models import InvoiceSource, SourceKind

logger = logging.getLogger(__name__)


def discover_invoices(input_dir: Path = INPUT_DIR) -> list[InvoiceSource]:
    """Return classified sources for all supported files, sorted by name.

    Unsupported files are logged and left untouched -- they never enter the
    pipeline, so they can never be moved or half-processed.
    """
    sources: list[InvoiceSource] = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            logger.warning("Unsupported file type, skipping: %s", path.name)
            continue
        if suffix == ".pdf":
            sources.append(_classify_pdf(path))
        else:
            sources.append(InvoiceSource(path=path, kind=SourceKind.IMAGE))
    logger.info("Discovered %d invoice file(s) in %s", len(sources), input_dir)
    return sources


def _classify_pdf(path: Path) -> InvoiceSource:
    """Digital PDF (usable text layer) or scanned PDF (OCR needed)?"""
    try:
        text = _extract_pdf_text(path)
    except Exception:
        #Broad on purpose: pdfplumber can fail in many ways on damaged orexotic PDFs. Text extraction failing does not mean rasterization will fail,
        #so give OCR a chance. if the file is truly broken, the OCR step raises and main escalates it with a reason.
        logger.warning(
            "Text extraction failed for %s, treating as scanned", path.name,
            exc_info=True,
        )
        return InvoiceSource(path=path, kind=SourceKind.SCANNED_PDF)

    char_count = len(text.strip())
    if char_count >= TEXT_LAYER_MIN_CHARS:
        logger.debug("%s: digital PDF (%d chars)", path.name, char_count)
        return InvoiceSource(path=path, kind=SourceKind.DIGITAL_PDF, text=text)

    logger.info(
        "%s: text layer too thin (%d chars < %d), treating as scanned",
        path.name, char_count, TEXT_LAYER_MIN_CHARS,
    )
    return InvoiceSource(path=path, kind=SourceKind.SCANNED_PDF)


def _extract_pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n\n".join(pages)