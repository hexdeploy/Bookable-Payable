"""
gemini_extractor.py
Sends PDF text to Gemini and gets back structured JSON.
Returns: { "payables": [...], "declined": [...] }
"""
import json
import logging
import os
import re
import time

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

_API_KEY = os.environ.get("GEMINI_API_KEY")
if not _API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env")

_CLIENT = genai.Client(api_key=_API_KEY)
_MODEL_NAME = "gemini-3.6-flash"

_SYSTEM = """
You are an accounts-payable extraction engine for an ERP system.

Given the raw text of a supplier document, you must:
  1. CLASSIFY the document — is it a payable (INVOICE or CREDIT_MEMO) or not?
  2. EXTRACT all required fields into structured JSON.

NON-PAYABLES include: purchase orders, delivery notes, account statements,
quotations/RFQs, reminders, receipt confirmations, shipping notices.
Put these in "declined", not in "payables".

A single PDF may have MULTIPLE payables. Return one entry per payable.

Return ONLY this JSON, no markdown, no backticks:

{
  "payables": [
    {
      "invoice_number":  "",
      "invoice_date":    "",
      "due_date":        "",
      "invoice_type":    "",
      "currency":        "",
      "supplier": {
        "name":        "",
        "supplier_id": "",
        "address":     "",
        "vat_id":      ""
      },
      "buyer": {
        "company_code":       "",
        "business_unit_code": "",
        "location_code":      ""
      },
      "_buyer_name":        "",
      "_buyer_address":     "",
      "_payment_term_text": "",
      "payment_term_id": "",
      "po_number":       "",
      "po_id":           "",
      "gross_total":      "",
      "subtotal":         "",
      "total_tax_amount": "",
      "discount_amount":   "",
      "freight_charges":   "",
      "insurance_charges": "",
      "extra_charges":     "",
      "excise_duties":     "",
      "taxes": [
        {
          "tax_type":      "",
          "tax_name":      "",
          "tax_rate":      "",
          "tax_amount":    "",
          "tax_type_code": ""
        }
      ],
      "line_items": [
        {
          "description":         "",
          "item_type":           "",
          "uom":                 "",
          "quantity":            "",
          "unit_price":          "",
          "total":               "",
          "discount":            "",
          "discount_percentage": "",
          "tax_rate":            "",
          "tax_amount":          "",
          "taxes":               []
        }
      ]
    }
  ],
  "declined": [
    { "doc_type": "", "reason": "" }
  ]
}

CRITICAL RULES:

1. Numbers: dot-decimal ONLY. "1234.56" not "1.234,56".
   Convert any comma-decimal European format.

2. unit_price is ALWAYS NET (tax-exclusive). This is non-negotiable.
   The ERP adds taxes on top of unit_price. If you put a tax-inclusive price
   in unit_price AND also list the tax separately, the ERP will double-count it.
   If the document shows a gross (tax-inclusive) unit price, back-calculate:
   net = gross_unit_price / (1 + tax_rate/100)
   Example: printed price 84.03 EUR with 19% VAT → unit_price = "70.61"

3. Tax placement is graded separately from the gross total:
   - Tax stated ONCE at document header → put in taxes[] only
   - Tax charged per line → put in that line's line_items[].taxes[]
   - NEVER move a tax to make the arithmetic easier

4. Credit memos: invoice_type = "CREDIT_MEMO", all amounts POSITIVE.

5. Leave supplier_id, payment_term_id, po_id, tax_type_code, buyer codes
   all as "". The resolver fills them. Do NOT guess.

6. _buyer_name / _buyer_address: copy buyer name+address exactly as printed.

7. _payment_term_text: copy the payment term text exactly as printed.

8. Leave unknown fields as "". Never invent a value.

9. item_type: GOODS / SERVICE / FREIGHT / TAX

10. Withholding tax (reduces what is owed): negative tax_amount e.g. "-15.00"

11. invoice_date and due_date MUST be ISO format: YYYY-MM-DD.
    Convert any date format: "02.02.2026" → "2026-02-02"
"""


def extract_payables(filename: str, text: str) -> dict:
    user_msg = (
        f"Document filename: {filename}\n\n"
        f"Raw document text:\n---\n{text[:10000]}\n---\n\n"
        f"Extract all payables. Return ONLY valid JSON."
    )

    for attempt in range(3):
        try:
            response = _CLIENT.models.generate_content(
                model=_MODEL_NAME,
                contents=user_msg,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM,
                    temperature=0.1,
                    max_output_tokens=16384,
                ),
            )
            raw = response.text.strip()

            # Remove markdown fences if Gemini added them
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)

            result = json.loads(raw)
            result.setdefault("payables", [])
            result.setdefault("declined", [])
            return result

        except json.JSONDecodeError as e:
            log.warning(f"   JSON parse error attempt {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(3)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower() or "exhausted" in err_str.lower():
                log.warning(f"   Quota hit — waiting 60s...")
                time.sleep(60)
            else:
                log.warning(f"   Gemini error attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(6)

    log.error(f"   All attempts failed for {filename}")
    return {
        "payables": [],
        "declined": [{"doc_type": "UNKNOWN", "reason": "LLM extraction failed"}]
    }