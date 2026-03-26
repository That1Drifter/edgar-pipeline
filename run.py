#!/usr/bin/env python3
"""
SEC EDGAR Extraction Pipeline — Phase 1

Usage:
    python run.py "Apple Inc"
    python run.py "Tesla Inc" --form 10-Q
    python run.py "Microsoft Corp" --quiet
"""

import sys
import io
import json
import os

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8', errors='replace')

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

from agents.extractor import run_extraction, save_result


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    company = sys.argv[1]
    form_type = "10-K"
    verbose = True

    # Parse args
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--form" and i + 1 < len(sys.argv):
            form_type = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--quiet":
            verbose = False
            i += 1
        else:
            i += 1

    print(f"═══════════════════════════════════════════════════════════")
    print(f"  SEC EDGAR Extraction Pipeline")
    print(f"  Company: {company}")
    print(f"  Filing:  {form_type}")
    print(f"═══════════════════════════════════════════════════════════")

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n  ERROR: ANTHROPIC_API_KEY not set.")
        print("  Create a .env file with: ANTHROPIC_API_KEY=sk-ant-...")
        print("  Or export it in your shell.")
        sys.exit(1)

    result = run_extraction(company, form_type, verbose=verbose)

    print(f"\n{'═' * 59}")

    if result["status"] == "success":
        data = result["data"]
        print(f"  EXTRACTION COMPLETE ({result['iterations']} iterations)")
        print(f"{'─' * 59}")
        print(f"  Company:      {data.get('company_name', '?')}")
        print(f"  Ticker:       {data.get('ticker', '?')}")
        print(f"  Period:       {data.get('period_end', '?')}")
        print(f"  Fiscal Year:  {data.get('fiscal_year', '?')}")
        print(f"{'─' * 59}")

        rev = data.get("revenue", {})
        ni = data.get("net_income", {})
        ta = data.get("total_assets", {})
        tl = data.get("total_liabilities", {})
        eps = data.get("eps", {})

        print(f"  Revenue:      {_fmt_financial(rev)}")
        print(f"  Net Income:   {_fmt_financial(ni)}")
        print(f"  Total Assets: {_fmt_financial(ta)}")
        print(f"  Total Liab:   {_fmt_financial(tl)}")
        print(f"  EPS:          {eps.get('value', '?')} {'(diluted)' if eps.get('diluted') else ''}")
        print(f"{'─' * 59}")

        risks = data.get("risk_factors", [])
        if risks:
            print(f"  Risk Factors ({len(risks)}):")
            for r in risks[:5]:
                cat = r.get("category", "?")
                if cat == "other" and r.get("category_detail"):
                    cat = r["category_detail"]
                print(f"    [{cat}] {r.get('title', '?')[:70]}")

        if data.get("conflict_detected"):
            print(f"\n  ⚠ CONFLICTS DETECTED: {data.get('notes', 'See extraction')}")

        if data.get("notes"):
            print(f"\n  Notes: {data['notes'][:200]}")

        # Save
        path = save_result(result)
        print(f"\n  Saved to: {path}")
    else:
        print(f"  EXTRACTION FAILED")
        print(f"  {result.get('message', 'Unknown error')}")

    print(f"{'═' * 59}\n")


def _fmt_financial(field: dict) -> str:
    """Format a financial field for display."""
    val = field.get("value")
    if val is None:
        return "not found"
    unit = field.get("unit", "")
    conf = field.get("confidence", 0)
    label = field.get("label", "")
    conf_bar = "●" * int(conf * 5) + "○" * (5 - int(conf * 5))
    unit_suffix = f" ({unit})" if unit != "dollars" else ""
    label_suffix = f'  [{label}]' if label else ""
    return f"${val:,.2f}{unit_suffix}  {conf_bar}{label_suffix}"


if __name__ == "__main__":
    main()
