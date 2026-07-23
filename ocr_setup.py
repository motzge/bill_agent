"""Resolve the Tesseract binary without relying on the system PATH."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytesseract

ENV_OVERRIDE = "TESSERACT_CMD"


def _candidate_paths() -> list[Path]:
    """Known Windows install locations, most likely first."""
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    raw = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    if local_appdata:
        raw.append(str(Path(local_appdata) / "Programs" / "Tesseract-OCR" / "tesseract.exe"))
    return [Path(p) for p in raw]


def resolve_tesseract() -> Path:
    """Return the Tesseract executable, checking override, PATH, then defaults."""
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(f"{ENV_OVERRIDE} points to a missing file: {path}")
        return path

    on_path = shutil.which("tesseract")
    if on_path:
        return Path(on_path)

    for candidate in _candidate_paths():
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Tesseract-OCR wurde nicht gefunden. Bitte von "
        "https://github.com/UB-Mannheim/tesseract/wiki installieren "
        f"oder den Pfad zur tesseract.exe in der Umgebungsvariable {ENV_OVERRIDE} setzen."
    )


def configure_tesseract() -> Path:
    """Point pytesseract at the resolved binary and its language data."""
    binary = resolve_tesseract()
    pytesseract.pytesseract.tesseract_cmd = str(binary)

    # Language files live next to the binary; pytesseract needs this for non-eng.
    tessdata = binary.parent / "tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))

    return binary


def available_languages() -> list[str]:
    """Language codes Tesseract can actually use — 'deu' must be in here."""
    return sorted(pytesseract.get_languages(config=""))