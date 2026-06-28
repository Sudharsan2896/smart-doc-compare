"""
OCR — reading text out of SCANNED documents and images.

A "scanned" PDF (or a photo/screenshot) has no real text inside — it's just a
picture of words. OCR ("optical character recognition") looks at the picture and
works out what the words are.

Engine choice: we use **Tesseract**, the long-standing free open-source OCR
engine. It's light enough to run on the free Streamlit host (heavier engines like
PaddleOCR tend to exceed the free memory limit). The Tesseract program itself is
installed on the server via `packages.txt`; this file is just the Python side.

How a scanned PDF is read:
    each page is rendered to an image with PyMuPDF, then handed to Tesseract.
We render in greyscale at a moderate resolution — enough for accuracy, light on
memory.
"""

from __future__ import annotations

import io

# Resolution for rendering PDF pages before OCR. ~200 DPI is a good accuracy /
# memory trade-off on a small free server.
_OCR_DPI = 200


def tesseract_available() -> bool:
    """True if the Tesseract engine is installed and callable on this machine."""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_image_bytes(image_bytes: bytes) -> str:
    """Read text from a single image (PNG/JPG/TIFF/…)."""
    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image).strip()


def ocr_pdf_bytes(pdf_bytes: bytes, dpi: int = _OCR_DPI) -> str:
    """Read text from a scanned PDF, one page at a time, and join it together."""
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    pages: list[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            # Render the page to a greyscale image (lighter on memory than colour).
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
            image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            pages.append(pytesseract.image_to_string(image))

    return "\n".join(pages).strip()
