#!/usr/bin/env python3
"""
SEC EDGAR Extraction Pipeline

Single company:
    python run.py "Apple Inc"
    python run.py "Tesla Inc" --form 10-Q

Multi-company comparison (uses coordinator + subagents):
    python run.py "Apple Inc" "Tesla Inc"
    python run.py "Apple Inc" "Microsoft Corp" "Google" --form 10-K

Batch mode (10+ companies, 50% cheaper):
    python run.py --batch "Apple Inc" "Tesla Inc" "Microsoft Corp" ...

Options:
    --form 10-K|10-Q    Filing type (default: 10-K)
    --batch             Use Message Batches API (cheaper, higher latency)
    --strict            Enable strict tool schemas (API-enforced output)
    --stream            Real-time streaming output (single company only)
    --think             Enable extended thinking on analyzer (multi-company)
    --model MODEL       Model to use (default: claude-sonnet-4-20250514)
    --quiet             Suppress iteration-level output
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
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Parse args
    companies = []
    form_type = "10-K"
    verbose = True
    batch_mode = False
    strict = False
    stream = False
    think = False
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--form" and i + 1 < len(sys.argv):
            form_type = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--quiet":
            verbose = False
            i += 1
        elif sys.argv[i] == "--batch":
            batch_mode = True
            i += 1
        elif sys.argv[i] == "--strict":
            strict = True
            i += 1
        elif sys.argv[i] == "--stream":
            stream = True
            i += 1
        elif sys.argv[i] == "--think":
            think = True
            i += 1
        elif sys.argv[i] == "--model" and i + 1 < len(sys.argv):
            os.environ["EDGAR_MODEL"] = sys.argv[i + 1]
            i += 2
        elif not sys.argv[i].startswith("--"):
            companies.append(sys.argv[i])
            i += 1
        else:
            i += 1

    if not companies:
        print("Error: provide at least one company name.")
        sys.exit(1)

    # Check API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n  ERROR: ANTHROPIC_API_KEY not set.")
        print("  Create a .env file with: ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"  SEC EDGAR Extraction Pipeline")
    model = os.environ.get("EDGAR_MODEL", "claude-sonnet-4-20250514")
    print(f"  Companies: {', '.join(companies)}")
    print(f"  Filing:    {form_type}")
    print(f"  Model:     {model}")
    if batch_mode:
        mode = "batch (Message Batches API)"
    elif stream and len(companies) == 1:
        mode = "streaming extraction"
    elif len(companies) > 1:
        mode = "multi-agent coordinator"
    else:
        mode = "single extraction"
    print(f"  Mode:      {mode}")
    if strict:
        print(f"  Strict:    enabled (API-enforced schema)")
    if think:
        print(f"  Thinking:  enabled (extended reasoning on analyzer)")
    print(f"{'=' * 60}")

    if batch_mode:
        run_batch_mode(companies, form_type, verbose, strict)
    elif stream and len(companies) == 1:
        run_stream(companies[0], form_type, strict)
    elif len(companies) == 1:
        run_single(companies[0], form_type, verbose, strict)
    else:
        run_multi(companies, form_type, verbose, strict, think)


def run_stream(company: str, form_type: str, strict: bool = False):
    """Streaming extraction — real-time output."""
    from agents.extractor_stream import run_extraction_stream
    from agents.extractor import save_result

    result = run_extraction_stream(company, form_type, strict=strict)
    print(f"\n{'=' * 60}")

    if result["status"] == "success":
        print_extraction(result["data"], result["iterations"])
        path = save_result(result)
        print(f"\n  Saved to: {path}")

        from review import check_needs_review
        reasons = check_needs_review({"status": "success", "data": result["data"]})
        if reasons:
            print(f"\n  Flagged for review ({len(reasons)} reason(s)):")
            for r in reasons:
                print(f"    - {r}")
    else:
        print(f"  EXTRACTION FAILED: {result.get('message', 'Unknown error')}")

    if result.get("cost"):
        print(f"{'─' * 60}")
        print(result["cost"].summary())

    print(f"{'=' * 60}\n")


def run_batch_mode(companies: list, form_type: str, verbose: bool, strict: bool = False):
    """Batch processing — uses Message Batches API for bulk extraction."""
    from agents.batch import run_batch

    result = run_batch(companies, form_type, verbose=verbose, strict=strict)

    if result.get("status") == "success":
        # Save all results
        os.makedirs("output", exist_ok=True)
        path = "output/batch_results.json"
        with open(path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\n  Saved to: {path}")

        # Print per-company summaries
        for r in result.get("results", []):
            company = r.get("company", "?")
            status = r.get("status", "?")
            if status == "success":
                data = r.get("data", {})
                rev = data.get("revenue", {})
                print(f"  {company}: revenue {rev.get('value')} {rev.get('unit', '')}")
            elif status == "partial":
                print(f"  {company}: partial — {r.get('validation_errors', ['?'])[0][:60]}")
            else:
                print(f"  {company}: failed — {r.get('error', '?')[:60]}")

        # Run human review routing
        from review import build_review_queue, save_review_queue, print_review_summary
        queue = build_review_queue(result.get("results", []))
        if queue["review_queue"]["needs_review"] > 0:
            review_path = save_review_queue(queue)
            print_review_summary(queue)
            print(f"  Saved review queue: {review_path}")

    elif result.get("status") == "timeout":
        print(f"\n  Batch still processing. Check later:")
        print(f"  Batch ID: {result.get('batch_id')}")
    else:
        print(f"\n  Batch failed: {result.get('error', 'Unknown')}")

    print(f"{'=' * 60}\n")


def run_single(company: str, form_type: str, verbose: bool, strict: bool = False):
    """Single company extraction — uses the direct extractor agent."""
    from agents.extractor import run_extraction, save_result

    result = run_extraction(company, form_type, verbose=verbose, strict=strict)
    print(f"\n{'=' * 60}")

    if result["status"] == "success":
        print_extraction(result["data"], result["iterations"])
        path = save_result(result)
        print(f"\n  Saved to: {path}")

        # Human review check
        from review import check_needs_review
        reasons = check_needs_review({"status": "success", "data": result["data"]})
        if reasons:
            print(f"\n  Flagged for review ({len(reasons)} reason(s)):")
            for r in reasons:
                print(f"    - {r}")
    else:
        print(f"  EXTRACTION FAILED: {result.get('message', 'Unknown error')}")

    # Cost summary
    if result.get("cost"):
        print(f"{'─' * 60}")
        print(result["cost"].summary())

    print(f"{'=' * 60}\n")


def run_multi(companies: list, form_type: str, verbose: bool, strict: bool = False,
              think: bool = False):
    """Multi-company comparison — uses coordinator + subagents."""
    from agents.coordinator import run_coordinator

    # Build the query for the coordinator
    company_list = ", ".join(companies)
    query = (
        f"Extract and compare financial data from the most recent {form_type} "
        f"filings for these companies: {company_list}. "
        f"Produce a comparison report highlighting key differences in revenue, "
        f"profitability, assets, and risk factors."
    )

    result = run_coordinator(query, verbose=verbose, strict=strict, think=think)
    print(f"\n{'=' * 60}")

    if result.get("status") == "success":
        print(f"  ANALYSIS COMPLETE ({result['iterations']} coordinator iterations)")
        print(f"{'─' * 60}")
        print(f"\n{result.get('summary', 'No summary generated.')}\n")

        # Save the full result
        os.makedirs("output", exist_ok=True)
        safe = "_vs_".join("".join(c if c.isalnum() else "_" for c in co) for co in companies)
        path = f"output/{safe}_comparison.json"
        with open(path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\n  Saved to: {path}")
    else:
        print(f"  ANALYSIS FAILED: {result.get('message', result.get('error', 'Unknown'))}")

    # Cost summary
    if result.get("cost"):
        print(f"{'─' * 60}")
        print(result["cost"].summary())

    print(f"{'=' * 60}\n")


def print_extraction(data: dict, iterations: int):
    """Pretty-print a single extraction result."""
    print(f"  EXTRACTION COMPLETE ({iterations} iterations)")
    print(f"{'─' * 60}")
    print(f"  Company:      {data.get('company_name', '?')}")
    print(f"  Ticker:       {data.get('ticker', '?')}")
    print(f"  Period:       {data.get('period_end', '?')}")
    print(f"  Fiscal Year:  {data.get('fiscal_year', '?')}")
    print(f"{'─' * 60}")

    for field_name, label in [("revenue", "Revenue"), ("net_income", "Net Income"),
                               ("total_assets", "Total Assets"), ("total_liabilities", "Total Liab")]:
        field = data.get(field_name, {})
        print(f"  {label + ':':<14}  {_fmt_financial(field)}")

    eps = data.get("eps", {})
    eps_val = eps.get("value", "?")
    eps_type = " (diluted)" if eps.get("diluted") else ""
    print(f"  {'EPS:':<14}  {eps_val}{eps_type}")
    print(f"{'─' * 60}")

    risks = data.get("risk_factors", [])
    if risks:
        print(f"  Risk Factors ({len(risks)}):")
        for r in risks[:5]:
            cat = r.get("category", "?")
            if cat == "other" and r.get("category_detail"):
                cat = r["category_detail"]
            print(f"    [{cat}] {r.get('title', '?')[:70]}")

    if data.get("conflict_detected"):
        print(f"\n  WARNING: Conflicts detected — {data.get('notes', 'see extraction')}")
    elif data.get("notes"):
        print(f"\n  Notes: {data['notes'][:200]}")


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
