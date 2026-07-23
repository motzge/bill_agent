"""Image preparation for OCR.

Tesseract is trained on roughly 300 DPI scans. Phone photos and low-res
scans land well below that, which is the single most common cause of bad
recognition on otherwise readable invoices.
"""

from __future__ import annotations

from PIL import Image, ImageOps

# A4 at 300 DPI. Anything narrower gets scaled up before recognition.
TARGET_WIDTH_PX = 2480

# Upscaling beyond this multiplies noise instead of adding detail.
MAX_SCALE_FACTOR = 4.0

# psm 6 = "assume a single uniform block of text", which fits invoice
# layouts better than the default page segmentation.
OCR_CONFIG = "--psm 6"


def prepare_for_ocr(image: Image.Image) -> Image.Image:
    """Return a grayscale, contrast-normalised, upscaled copy of the image."""
    prepared = ImageOps.exif_transpose(image)  # phone photos carry rotation in EXIF
    prepared = prepared.convert("L")
    prepared = ImageOps.autocontrast(prepared)
    return _upscale(prepared)


def _upscale(image: Image.Image) -> Image.Image:
    """Scale up towards 300 DPI equivalent, capped to avoid noise blowup."""
    if image.width >= TARGET_WIDTH_PX:
        return image

    factor = min(TARGET_WIDTH_PX / image.width, MAX_SCALE_FACTOR)
    new_size = (round(image.width * factor), round(image.height * factor))
    return image.resize(new_size, Image.LANCZOS)