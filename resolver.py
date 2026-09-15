"""
resolver.py
Fills in master-data codes that Gemini left blank:
  - supplier_id     → match against suppliers.json
  - payment_term_id → match against payment_terms.json
  - po_id           → match against po_master.json
  - tax_type_code   → match against tax_master.json
  - buyer codes     → match against chart_of_books.json

Uses fuzzy string matching (thefuzz) for names.
Exact match for VAT IDs and PO numbers.
"""
import json
import logging
import pathlib
import re
from datetime import date

from thefuzz import fuzz

log = logging.getLogger(__name__)

MASTER_DIR = pathlib.Path("master_data")


def _load(filename):
    with open(MASTER_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


# Load all master data once when the module is imported
_SUPPLIERS    = _load("suppliers.json")["suppliers"]
_TAXES        = _load("tax_master.json")["taxes"]
_TERMS        = _load("payment_terms.json")["payment_terms"]
_PO_MASTER    = _load("po_master.json")["purchase_orders"]
_COB          = _load("chart_of_books.json")


# ── 1. Supplier matching ──────────────────────────────────────────────────────

def match_supplier(name: str, vat_id: str = "") -> str:
    """
    Try exact VAT ID first (most reliable).
    Fall back to fuzzy name match (score >= 75 out of 100).
    Returns supplier_id string, or "" if no match.
    """
    vat_clean = (vat_id or "").strip().upper()

    # Exact VAT ID match
    if vat_clean:
        for s in _SUPPLIERS:
            if s.get("vat_id", "").upper() == vat_clean:
                return s["supplier_id"]

    # Fuzzy name match
    name_clean = (name or "").strip().lower()
    if name_clean:
        best_id, best_score = "", 0
        for s in _SUPPLIERS:
            score = fuzz.token_sort_ratio(name_clean, s["name"].lower())
            if score > best_score:
                best_score, best_id = score, s["supplier_id"]
        if best_score >= 75:
            return best_id

    return ""


# ── 2. Payment term matching ──────────────────────────────────────────────────

def match_payment_term(text: str, invoice_date: str = "", due_date: str = "") -> str:
    """
    1. Check if any alias from payment_terms.json is inside the text.
    2. Look for "N days" pattern in the text.
    3. Compute day gap between invoice and due date.
    Returns payment_term_id string, or "".
    """
    text_lower = (text or "").strip().lower()

    # Text alias match
    if text_lower:
        for pt in _TERMS:
            for alias in pt.get("text_aliases", []):
                if alias.lower() in text_lower or text_lower in alias.lower():
                    return pt["payment_term_id"]

        # "N days" pattern
        m = re.search(r"(?:net\s*)?(\d+)\s*days?", text_lower)
        if m:
            days = int(m.group(1))
            for pt in _TERMS:
                if pt.get("days") == days:
                    return pt["payment_term_id"]

    # Date delta fallback
    if invoice_date and due_date:
        try:
            inv = date.fromisoformat(invoice_date)
            due = date.fromisoformat(due_date)
            delta = (due - inv).days
            best_pt, best_diff = "", 999
            for pt in _TERMS:
                diff = abs(pt.get("days", 9999) - delta)
                if diff < best_diff:
                    best_diff, best_pt = diff, pt["payment_term_id"]
            if best_diff <= 2:
                return best_pt
        except ValueError:
            pass

    return ""


# ── 3. PO matching ────────────────────────────────────────────────────────────

def match_po(po_number: str) -> str:
    """Exact match on po_number. Returns po_id or ""."""
    po_clean = (po_number or "").strip()
    if not po_clean:
        return ""
    for po in _PO_MASTER:
        if po["po_number"] == po_clean:
            return po["po_id"]
    return ""


# ── 4. Tax code matching ──────────────────────────────────────────────────────

def match_tax_code(tax_name: str, tax_rate: str, country: str = "") -> str:
    """
    Filter taxes by country first, then match by rate.
    Tie-break using keyword overlap with tax_name.
    Returns tax_type_code or "".
    """
    try:
        rate = float(str(tax_rate).replace("%", "").strip())
    except (ValueError, TypeError):
        rate = None

    # Filter by country if known
    candidates = _TAXES
    if country:
        by_country = [t for t in _TAXES if t.get("country", "").upper() == country.upper()]
        if by_country:
            candidates = by_country

    # Match by rate
    if rate is not None:
        by_rate = [t for t in candidates if abs(t["rate"] - rate) < 0.01]
        if by_rate:
            name_lower = (tax_name or "").lower()
            for t in by_rate:
                if t["tax_type"].lower() in name_lower or t["name"].lower()[:4] in name_lower:
                    return t["code"]
            return by_rate[0]["code"]   # fallback: first rate match

    return ""


# ── 5. Buyer org matching ─────────────────────────────────────────────────────

def match_buyer(buyer_name: str, buyer_address: str = "") -> dict:
    """
    Match buyer company from chart_of_books.json.
    Returns { company_code, business_unit_code, location_code }.
    All empty strings if no match.
    """
    empty = {"company_code": "", "business_unit_code": "", "location_code": ""}
    search = f"{buyer_name} {buyer_address}".lower()

    for company in _COB.get("companies", []):
        if fuzz.partial_ratio(company["company_name"].lower(), search) < 65:
            continue

        result = {"company_code": company["company_code"],
                  "business_unit_code": "", "location_code": ""}

        for bu in company.get("business_units", []):
            for loc in bu.get("locations", []):
                addr_words = [
                    w for w in loc.get("invoice_to_address", "").lower().split()
                    if len(w) > 4
                ]
                if any(w in search for w in addr_words):
                    result["business_unit_code"] = bu["business_unit_code"]
                    result["location_code"] = loc["location_code"]
                    return result

        return result   # company matched, no BU/location → return company code only

    return empty


# ── Helper: infer supplier country ────────────────────────────────────────────

def _infer_country(payable: dict) -> str:
    """
    Try to get 2-letter country code from:
    1. Leading letters of VAT ID (e.g. "DE209..." → "DE")
    2. Trailing 2-letter code in supplier address (e.g. "..., EE")
    """
    vat = (payable.get("supplier") or {}).get("vat_id") or ""
    m = re.match(r"^([A-Z]{2})\d", vat.strip().upper())
    if m:
        return m.group(1)

    addr = (payable.get("supplier") or {}).get("address") or ""
    m = re.search(r",\s*([A-Z]{2})\s*$", addr.strip())
    if m:
        return m.group(1)

    return ""


# ── Main function: resolve everything ─────────────────────────────────────────

def resolve_all(payable: dict) -> dict:
    """
    Fill all master-data codes in one payable dict.
    Also removes the helper _ fields that Gemini added (_buyer_name etc.)
    since those aren't part of the final schema.
    """
    p = {**payable}   # make a copy, don't edit the original

    # Supplier ID
    sup = dict(p.get("supplier") or {})
    if not sup.get("supplier_id"):
        sup["supplier_id"] = match_supplier(
            sup.get("name", ""),
            sup.get("vat_id", "")
        )
    p["supplier"] = sup

    # Payment term
    pt_text = p.pop("_payment_term_text", "") or ""
    if not p.get("payment_term_id"):
        p["payment_term_id"] = match_payment_term(
            pt_text,
            p.get("invoice_date", ""),
            p.get("due_date", "")
        )

    # PO ID
    if not p.get("po_id") and p.get("po_number"):
        p["po_id"] = match_po(p["po_number"])

    # Buyer codes
    buyer_name = p.pop("_buyer_name", "") or ""
    buyer_addr = p.pop("_buyer_address", "") or ""
    buyer = dict(p.get("buyer") or {})
    if not buyer.get("company_code"):
        matched = match_buyer(buyer_name, buyer_addr)
        for k, v in matched.items():
            if not buyer.get(k):
                buyer[k] = v
    p["buyer"] = buyer

    # Tax codes (header level)
    country = _infer_country(p)
    for tax in p.get("taxes") or []:
        if not tax.get("tax_type_code"):
            tax["tax_type_code"] = match_tax_code(
                tax.get("tax_name", ""),
                tax.get("tax_rate", ""),
                country
            )

    # Tax codes (line level)
    for line in p.get("line_items") or []:
        for tax in line.get("taxes") or []:
            if not tax.get("tax_type_code"):
                tax["tax_type_code"] = match_tax_code(
                    tax.get("tax_name", ""),
                    tax.get("tax_rate", ""),
                    country
                )

    return p