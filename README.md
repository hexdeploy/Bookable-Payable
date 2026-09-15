# Bookable Payable — Zycus AI/ML Graduate Assignment

An end-to-end accounts-payable extraction pipeline that reads supplier PDFs,
classifies them, extracts structured invoice data using an LLM, resolves
master-data codes, and validates every payable against the ERP oracle (`erp.py`).

---

## How It Works

```
documents/*.pdf
      │
      ▼
┌─────────────────────┐
│   pdf_reader.py     │  pdfplumber (text layer) → PyMuPDF + Tesseract OCR (scanned)
└────────┬────────────┘
         │ raw text
         ▼
┌─────────────────────┐
│ gemini_extractor.py │  Gemini 3.6 Flash — classify + extract → autodraft JSON
└────────┬────────────┘
         │ raw extracted fields
         ▼
┌─────────────────────┐
│   resolver.py       │  Fuzzy-match supplier, payment term, PO, tax codes, buyer org
└────────┬────────────┘
         │ resolved autodraft
         ▼
┌─────────────────────┐
│   erp.py (oracle)   │  Recompute gross → compare to doc gross
└────────┬────────────┘
         │
         ▼
   output/X.json
```

---

## Stack

| Layer | Tool |
|---|---|
| PDF text extraction | pdfplumber |
| OCR fallback (scanned PDFs) | PyMuPDF + Tesseract |
| LLM extraction + classification | Google Gemini 3.6 Flash |
| Master-data fuzzy matching | thefuzz (Levenshtein) |
| ERP validation oracle | erp.py (unchanged from kit) |
| Runtime | Python 3.11 / Docker |

---

## Setup

### Prerequisites

- Python 3.10+
- Tesseract OCR installed on your system
- A Google Gemini API key (free tier works): https://aistudio.google.com/app/apikey

**Install Tesseract:**

```bash
# Windows: download installer from
# https://github.com/UB-Mannheim/tesseract/wiki
# Install to C:\Program Files\Tesseract-OCR\ and add to PATH

# macOS
brew install tesseract tesseract-lang

# Ubuntu / Debian
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu \
    tesseract-ocr-est tesseract-ocr-fra tesseract-ocr-por
```

### Option A — Local Python (recommended for development)

```bash
# 1. Clone the repo
git clone https://github.com/hexdeploy/bookable-payable.git
cd bookable-payable

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set API key
cp .env.example .env
# Open .env and paste your GEMINI_API_KEY

# 5. Place PDFs in the documents/ folder
# (copy all candidate kit PDFs into documents/)

# 6. Run
python pipeline.py documents/
```

### Option B — Docker

```bash
# 1. Build
docker build -t bookable-payable .

# 2. Run
docker run --env-file .env \
  -v "$(pwd)/documents:/app/documents" \
  -v "$(pwd)/output:/app/output" \
  bookable-payable
```

---

## Running the Pipeline

```bash
# Process all PDFs in the documents/ folder
python pipeline.py documents/

# Process a single PDF
python pipeline.py documents/INV-01.pdf
```

The pipeline prints live progress and ERP pass/fail for each PDF as it runs.

---

## Validating Results

```bash
# Validate all output files
python validator.py

# Validate one file
python validator.py output/INV-01.json
```

Output:
```
────────────────────────────────────────────────────────────
📄  INV-01.json
  ✅  852566               doc=    438.00  erp=    438.00  Δ=0.00  EUR

============================================================
TOTAL  1 payables | ✅ 1 passed | ❌ 0 failed
Pass rate: 100.0%
```

---

## Output Format

Each `documents/X.pdf` produces `output/X.json`:

```json
{
  "file": "X.pdf",
  "payables": [
    {
      "invoice_number": "852566",
      "invoice_date": "2026-02-02",
      "due_date": "2026-02-12",
      "invoice_type": "INVOICE",
      "currency": "EUR",
      "supplier": { "name": "...", "supplier_id": "2845695", ... },
      "buyer": { "company_code": "BOLTGROUP", ... },
      "gross_total": "438.00",
      "line_items": [ ... ],
      "taxes": [ ... ]
    }
  ],
  "declined": []
}
```

Non-payable documents (delivery notes, reminders, POs, etc.) go into `declined[]`:

```json
{
  "file": "DU-08.pdf",
  "payables": [],
  "declined": [
    {
      "doc_type": "REMINDER",
      "reason": "Payment reminder listing past transactions, not a payable invoice"
    }
  ]
}
```

---

## Project Structure

```
bookable-payable/
├── pipeline.py           ← main entry point
├── pdf_reader.py         ← PDF text extraction + OCR fallback
├── gemini_extractor.py   ← Gemini LLM extraction + classification
├── resolver.py           ← master-data fuzzy matching
├── validator.py          ← ERP pass/fail report
├── erp.py                ← ERP oracle (unchanged from kit)
├── example_check.py      ← kit usage example (unchanged)
├── sample_autodraft.json ← kit sample (unchanged)
├── master_data/
│   ├── suppliers.json        (14 suppliers)
│   ├── tax_master.json       (34 tax codes)
│   ├── payment_terms.json    (10 payment terms)
│   ├── po_master.json        (active POs)
│   └── chart_of_books.json   (org hierarchy)
├── documents/            ← input PDFs go here
├── output/               ← generated JSON (one per PDF)
├── DESIGN.md             ← approach and learnings
├── requirements.txt
├── Dockerfile
└── .env.example
```

---

## Master Data Matching Logic

| Field | Strategy |
|---|---|
| `supplier_id` | Exact VAT ID match first → fuzzy name match (≥75% score) |
| `payment_term_id` | Text alias match → "N days" regex → invoice-to-due-date delta |
| `po_id` | Exact `po_number` string match |
| `tax_type_code` | Filter by country prefix from VAT ID → match by rate → keyword tie-break |
| `company_code` | Partial ratio fuzzy match on buyer name from document |

Unmatched fields return `""` — honest blank rather than fabricated code.

---

## Key Design Decisions

**1. OCR fallback is automatic.**
pdfplumber runs first on every PDF. If it returns fewer than 80 characters or 10 words, the file is treated as scanned and PyMuPDF renders it to 300 DPI images for Tesseract. No manual flag needed.

**2. `unit_price` is always NET.**
The Gemini prompt explicitly instructs back-calculation when the document shows a tax-inclusive unit price: `net = gross / (1 + rate/100)`. Submitting a gross unit price alongside a separate tax entry would cause the ERP to double-count.

**3. Tax placement is preserved, not corrected.**
A header-level tax stays in `taxes[]`. A line-level tax stays in `line_items[].taxes[]`. Moving a tax to make arithmetic easier changes the ERP's computation base and produces a wrong booking even when the gross total looks correct.

**4. Honest blanks over fabricated codes.**
Any master-data code that cannot be confidently matched is left as `""`. The ERP books correctly on amounts alone — an empty `supplier_id` or `payment_term_id` does not break the booking.

**5. Rate limiting built in.**
A 5-second pause between PDFs prevents Gemini free-tier quota exhaustion. A 60-second auto-retry fires on HTTP 429 responses.

---

## Author

**Anup B**
Computer Science & AI/ML Graduate — R L Jalappa Institute of Technology, Bengaluru (2026)
GitHub: github.com/hexdeploy
LinkedIn: linkedin.com/in/anup-bileyali
