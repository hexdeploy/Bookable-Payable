"""
validator.py
Reads output/*.json and runs each payable through erp_book().
Prints pass ✅ or fail ❌ for every payable + a summary.

Usage:
    python validator.py                      # check all output/*.json
    python validator.py output/INV-01.json   # check one file
"""
import json
import pathlib
import sys

from erp import erp_book

OUTPUT_DIR = pathlib.Path("output")
TOLERANCE  = 0.02   # allow 2-cent rounding difference


def validate_file(path: pathlib.Path) -> tuple[int, int]:
    with open(path, encoding="utf-8") as f:
        result = json.load(f)

    payables = result.get("payables") or []
    declined = result.get("declined") or []

    print(f"\n{'─'*60}")
    print(f"📄  {path.name}")

    passed = 0
    for i, p in enumerate(payables):
        try:
            out      = erp_book(p)
            will     = out["will_book_gross"]
            doc      = float(p.get("gross_total") or 0)
            diff     = abs(will - doc)
            ok       = diff < TOLERANCE
            sym      = "✅" if ok else "❌"
            inv      = p.get("invoice_number") or f"payable[{i}]"
            curr     = out["currency"]
            if ok:
                passed += 1
            print(f"  {sym}  {inv:<20} doc={doc:>10.2f}  erp={will:>10.2f}  Δ={diff:.2f}  {curr}")
        except Exception as e:
            print(f"  ⚠   payable[{i}] error: {e}")

    for d in declined:
        print(f"  ⛔  DECLINED — {d.get('doc_type','?')}: {d.get('reason','')}")

    if not payables and not declined:
        print("  (empty)")

    return passed, len(payables)


def main():
    files = [pathlib.Path(sys.argv[1])] if len(sys.argv) > 1 else sorted(OUTPUT_DIR.glob("*.json"))

    if not files:
        print(f"No JSON files in {OUTPUT_DIR}/")
        sys.exit(1)

    total_p, total_t = 0, 0
    for path in files:
        p, t = validate_file(path)
        total_p += p
        total_t += t

    print(f"\n{'='*60}")
    print(f"TOTAL  {total_t} payables | ✅ {total_p} passed | ❌ {total_t - total_p} failed")
    if total_t:
        print(f"Pass rate: {total_p / total_t * 100:.1f}%")


if __name__ == "__main__":
    main()