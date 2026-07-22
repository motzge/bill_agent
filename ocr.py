"""OCR module for bill_agent.

Deliberately isolated: this is the ONLY file that imports pytesseract,
pypdfium2 and PIL. When the pipeline moves to the Claude API (native
document input), this file gets deleted along with its single call site
in main.py -- nothing else depends on it.

System requirement (not pip-installable): the Tesseract binary plus the
German language pack. Linux: apt install tesseract-ocr tesseract-ocr-deu.
Windows: UB-Mannheim installer, tick "German" under additional languages.
"""

from __future__ import annotations

import logging

import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from models import InvoiceSource, SourceKind

logger = logging.getLogger(__name__)

OCR_LANG = "deu"
RENDER_SCALE = 300 / 72  #pypdfium2 base is 72 dpi; scale up to 300 dpi for OCR


class OcrError(RuntimeError):
    """OCR could not produce usable text for a file."""


def assert_tesseract_available() -> None:
    """Fail loud at startup instead of at invoice #1. Call once from main."""
    try:
        version = pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError(
            "Tesseract ist nicht installiert. Bitte tesseract-ocr und das "
            "deutsche Sprachpaket (tesseract-ocr-deu) installieren, siehe README."
        ) from exc
    languages = pytesseract.get_languages(config="")
    if OCR_LANG not in languages:
        raise OcrError(
            f"Das Tesseract-Sprachpaket '{OCR_LANG}' fehlt "
            f"(installiert: {', '.join(sorted(languages))})."
        )
    logger.debug("Tesseract %s available, '%s' installed", version, OCR_LANG)


def extract_text(source: InvoiceSource) -> str:
    """OCR a scanned PDF or photo. Raises OcrError on unusable results."""
    if source.kind is SourceKind.SCANNED_PDF:
        text = _ocr_pdf(source)
    elif source.kind is SourceKind.IMAGE:
        text = _ocr_image(source)
    else:
        #Digital PDFs already carry their text; calling OCR on them is a programming error, not a data problem - so crash, don't escalate.
        raise ValueError(f"OCR called for {source.kind.value}: {source.path.name}")

    cleaned = text.strip()
    if not cleaned:
        raise OcrError(f"Die Texterkennung hat keinen Text in {source.path.name} gefunden (Scan unlesbar oder leer)")
    logger.info("OCR finished: %s (%d chars)", source.path.name, len(cleaned))
    return cleaned


def _ocr_pdf(source: InvoiceSource) -> str:
    """Render each page to a 300 dpi image, OCR it, join the pages."""
    pages_text: list[str] = []
    pdf = pdfium.PdfDocument(source.path)
    try:
        for number, page in enumerate(pdf, start=1):
            image = page.render(scale=RENDER_SCALE).to_pil()
            pages_text.append(pytesseract.image_to_string(image, lang=OCR_LANG))
            logger.debug("OCR page %d of %s done", number, source.path.name)
    finally:
        pdf.close()  #pdfium holds native resources; never rely on GC here
    return "\n\n".join(pages_text)


def _ocr_image(source: InvoiceSource) -> str:
    with Image.open(source.path) as image:
        return pytesseract.image_to_string(image, lang=OCR_LANG)