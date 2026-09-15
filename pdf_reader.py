"""
pdf_reader.py — PDF text extraction.

Strategy:
  1. pdfplumber  → fast, works for PDFs with a real text layer
  2. PyMuPDF + pytesseract  → OCR fallback for scanned / image-only PDFs

Returns a single string of all pages joined with double newlines.
"""
from __future__ import annotations

import io
import logging
import pathlib

log = logging.getLogger(__name__)

# Minimum character count to consider pdfplumber output "meaningful"
_MIN_CHARS = 80
_MIN_WORDS = 10


def extract_text(pdf_path: pathlib.Path) -> str:
    """Extract text from *pdf_path*. Tries text layer first, OCR second."""
    text = _pdfplumber(pdf_path)
    if _meaningful(text):
        log.info(f"   pdfplumber: {len(text)} chars")
        return text

    log.info("   pdfplumber gave thin text → OCR")
    text = _ocr(pdf_path)
    log.info(f"   OCR: {len(text)} chars")
    return text


# ── backend: pdfplumber ───────────────────────────────────────────────────────

def _pdfplumber(pdf_path: pathlib.Path) -> str:
    try:
        import pdfplumber

        pages: list[str] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                pages.append(t)
        return "\n\n".join(pages)
    except Exception as exc:
        log.debug(f"   pdfplumber error: {exc}")
        return ""


# ── backend: PyMuPDF + pytesseract ───────────────────────────────────────────

def _ocr(pdf_path: pathlib.Path) -> str:
    """Render each page as a 300-DPI PNG and run Tesseract on it."""
    try:
        import pymupdf          # replaces deprecated `import fitz`
        import pytesseract
        from PIL import Image

        # Tell pytesseract where tesseract.exe is on Windows.
        # Change this path if you installed Tesseract somewhere else.
        import sys, os
        if sys.platform == "win32":
            tess_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.exists(tess_path):
                pytesseract.pytesseract.tesseract_cmd = tess_path

        doc = pymupdf.open(str(pdf_path))
        pages: list[str] = []

        for page in doc:
            # 300 DPI gives good OCR quality without being huge
            mat = pymupdf.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # --oem 3 = LSTM engine; --psm 6 = assume uniform block of text
            page_text = pytesseract.image_to_string(
                img,
                lang="eng+deu+est+fra+por+msa",   # cover the doc languages in the kit
                config="--oem 3 --psm 6",
            )
            pages.append(page_text)

        return "\n\n".join(pages)

    except Exception as exc:
        log.warning(f"   OCR error: {exc}")
        return ""


# ── helper ────────────────────────────────────────────────────────────────────

def _meaningful(text: str) -> bool:
    """Return True if *text* looks like real content rather than OCR noise or empty."""
    stripped = text.strip()
    word_count = len(stripped.split())
    return len(stripped) >= _MIN_CHARS and word_count >= _MIN_WORDS