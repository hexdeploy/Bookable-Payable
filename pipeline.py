"""
pipeline.py
Main entry point. Processes every PDF in a folder.

How it works for each PDF:
  Step 1: pdf_reader.py    → extract text (with OCR fallback)
  Step 2: gemini_extractor.py → send text to Gemini → get raw JSON
  Step 3: resolver.py      → fill master-data codes
  Step 4: write output/X.json

Usage:
    python pipeline.py documents/            # all PDFs
    python pipeline.py documents/INV-01.pdf  # single PDF
"""
import time 
import json
import logging
import pathlib
import sys

from dotenv import load_dotenv
load_dotenv()   # reads GEMINI_API_KEY from .env

from pdf_reader import extract_text
from gemini_extractor import extract_payables
from resolver import resolve_all
from erp import erp_book

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

OUTPUT_DIR = pathlib.Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


def process_one_pdf(pdf_path: pathlib.Path) -> dict:
    """Process a single PDF. Returns the output dict."""
    log.info(f"▶  {pdf_path.name}")

    # Step 1: Get text
    text = extract_text(pdf_path)

    # Step 2: LLM extraction
    raw = extract_payables(pdf_path.name, text)

    # Step 3: Resolve master data codes
    payables = [resolve_all(p) for p in raw.get("payables", [])]

    return {
        "file": pdf_path.name,
        "payables": payables,
        "declined": raw.get("declined", [])
    }


def erp_check(result: dict):
    """Print ERP pass/fail for each payable (for visibility while running)."""
    for p in result["payables"]:
        try:
            out = erp_book(p)
            will = out["will_book_gross"]
            doc  = float(p.get("gross_total") or 0)
            diff = abs(will - doc)
            ok   = diff < 0.02
            sym  = "✅" if ok else "❌"
            inv  = p.get("invoice_number", "?")
            log.info(f"   {sym}  inv={inv}  doc={doc:.2f}  erp={will:.2f}  Δ={diff:.2f}  {out['currency']}")
        except Exception as e:
            log.warning(f"   ⚠  ERP error: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <documents_folder_or_pdf>")
        sys.exit(1)

    target = pathlib.Path(sys.argv[1])

    if target.is_file() and target.suffix.lower() == ".pdf":
        pdfs = [target]
    elif target.is_dir():
        pdfs = sorted(target.glob("*.pdf"))
    else:
        print(f"Not a PDF or directory: {target}")
        sys.exit(1)

    log.info(f"Found {len(pdfs)} PDF(s)")

    for i, pdf_path in enumerate(pdfs):
        try:
            result = process_one_pdf(pdf_path)
            erp_check(result)

            out_path = OUTPUT_DIR / (pdf_path.stem + ".json")
            out_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            log.info(f"   → {out_path}")
        except Exception as e:
            log.error(f"   FAILED {pdf_path.name}: {e}", exc_info=True)

        if i < len(pdfs) - 1:
            log.info("   ⏳ 5s pause (quota protection)...")
            time.sleep(5)

    log.info("Done.")


if __name__ == "__main__":
    main()