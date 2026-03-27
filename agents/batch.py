"""
Batch Processing — Message Batches API for Bulk Extraction

For processing many companies (10+), this is cheaper and faster than the
coordinator approach. Trade-off: no multi-turn retry loop per company.

How it works:
1. Pre-fetch: Look up CIKs, get filings, fetch text (SEC API — sequential, rate-limited)
2. Batch: Create one Claude Messages Batches API request per company
   Each request gets the filing text + extraction prompt in a single turn
3. Poll: Wait for the batch to complete (typically < 1 hour)
4. Collect: Parse results, run post-hooks, flag for review

Cost: ~50% cheaper than standard API calls (Batches API pricing).
Latency: Higher per-request (batch processing), but total throughput is much higher.

This implements cert exam concepts:
- D4 Task 4.3: Message Batches API for bulk processing
- D3 Task 3.1: PostToolUse hooks applied to batch results
"""

import json
import time
import os
import sys
import io
from anthropic import Anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from edgar.fetcher import lookup_cik, get_company_filings, fetch_filing_text
from hooks import run_post_hooks
from tools.definitions import TOOLS, TOOLS_STRICT

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

MODEL = "claude-sonnet-4-20250514"

BATCH_EXTRACTION_SYSTEM = """You are a financial data extraction agent. You will receive
the text of an SEC filing. Extract structured financial data by calling extract_financials.

EXTRACTION RULES:
- Extract values EXACTLY as stated in the filing — do not calculate or estimate
- Use null for any field where the value is not clearly stated
- Preserve the original label used in the filing
- Confidence calibration:
  * 0.9-1.0: Clearly printed in a labeled financial table
  * 0.7-0.8: Requires some interpretation
  * 0.4-0.6: Inferred from context
  * Below 0.4: Educated guess

You MUST call the extract_financials tool with your extraction."""


def prefetch_filings(companies: list[str], form_type: str = "10-K",
                     verbose: bool = True) -> list[dict]:
    """
    Phase 1: Fetch all filings via SEC API (sequential, rate-limited).

    Returns a list of dicts with company info and filing text, or error info.
    """
    results = []

    for company in companies:
        if verbose:
            print(f"  Prefetching: {company}...")

        entry = {"company": company, "form_type": form_type}

        # Look up CIK
        cik = lookup_cik(company)
        if not cik:
            entry["status"] = "failed"
            entry["error"] = f"No SEC filings found for '{company}'"
            results.append(entry)
            if verbose:
                print(f"    CIK not found")
            continue

        entry["cik"] = cik

        # Get filings list
        filings = get_company_filings(cik, form_type)
        if not filings:
            entry["status"] = "failed"
            entry["error"] = f"No {form_type} filings found for CIK {cik}"
            results.append(entry)
            if verbose:
                print(f"    No filings found")
            continue

        entry["filing_date"] = filings[0]["filed"]
        entry["filing_url"] = filings[0]["url"]

        # Fetch filing text
        text = fetch_filing_text(filings[0]["url"], max_chars=50000)
        if text.startswith("ERROR"):
            entry["status"] = "failed"
            entry["error"] = text
            results.append(entry)
            if verbose:
                print(f"    Fetch failed: {text[:80]}")
            continue

        entry["status"] = "ready"
        entry["filing_text"] = text
        entry["text_chars"] = len(text)
        results.append(entry)

        if verbose:
            print(f"    OK — {len(text):,} chars from {filings[0]['filed']}")

    return results


def create_batch(prefetched: list[dict], verbose: bool = True,
                 strict: bool = False) -> str | None:
    """
    Phase 2: Submit a Message Batches API request for all ready filings.

    Returns the batch ID, or None if no requests to submit.
    """
    client = Anthropic()
    tools = TOOLS_STRICT if strict else TOOLS
    requests = []

    for entry in prefetched:
        if entry["status"] != "ready":
            continue

        company = entry["company"]
        text = entry["filing_text"]
        form_type = entry["form_type"]

        # Single-turn extraction prompt with the filing text included
        messages = [
            {
                "role": "user",
                "content": (
                    f"Extract financial data from this {form_type} filing for {company}.\n\n"
                    f"FILING TEXT:\n{text}"
                ),
            }
        ]

        requests.append(
            Request(
                custom_id=f"extract_{company.replace(' ', '_')}",
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=4096,
                    system=BATCH_EXTRACTION_SYSTEM,
                    tools=tools,
                    tool_choice={"type": "any"},  # Force extraction tool call
                    messages=messages,
                ),
            )
        )

    if not requests:
        if verbose:
            print("  No filings ready for batch processing.")
        return None

    if verbose:
        print(f"\n  Submitting batch: {len(requests)} extraction requests...")

    batch = client.messages.batches.create(requests=requests)

    if verbose:
        print(f"  Batch ID: {batch.id}")
        print(f"  Status: {batch.processing_status}")

    return batch.id


def poll_batch(batch_id: str, verbose: bool = True, poll_interval: int = 30,
               max_wait: int = 3600) -> bool:
    """
    Phase 3: Poll until the batch completes.

    Returns True if batch ended, False if timed out.
    """
    client = Anthropic()
    elapsed = 0

    while elapsed < max_wait:
        batch = client.messages.batches.retrieve(batch_id)

        if verbose:
            counts = batch.request_counts
            print(f"  [{elapsed}s] {batch.processing_status} — "
                  f"processing: {counts.processing}, "
                  f"succeeded: {counts.succeeded}, "
                  f"errored: {counts.errored}")

        if batch.processing_status == "ended":
            return True

        time.sleep(poll_interval)
        elapsed += poll_interval

    if verbose:
        print(f"  Timed out after {max_wait}s")
    return False


def collect_results(batch_id: str, prefetched: list[dict],
                    verbose: bool = True) -> list[dict]:
    """
    Phase 4: Collect batch results and run post-hooks.

    Returns a list of extraction results (one per company).
    """
    client = Anthropic()
    results = []

    # Start with failed prefetches
    for entry in prefetched:
        if entry["status"] == "failed":
            results.append({
                "company": entry["company"],
                "status": "failed",
                "error": entry.get("error", "Prefetch failed"),
                "phase": "prefetch",
            })

    # Process batch results
    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        company = custom_id.replace("extract_", "").replace("_", " ")

        if result.result.type == "succeeded":
            message = result.result.message

            # Find the tool_use block (extract_financials call)
            extraction = None
            for block in message.content:
                if block.type == "tool_use" and block.name == "extract_financials":
                    extraction = block.input
                    break

            if extraction:
                # Run post-hooks (normalization, audit logging)
                # Simulate what handle_tool_call does post-execution
                from tools.definitions import _validate_extraction
                errors = _validate_extraction(extraction)

                if errors:
                    # Create a validation-failed result, then run post-hooks
                    result_str = json.dumps({
                        "status": "validation_failed",
                        "errors": errors,
                        "extraction": extraction,
                    })
                    result_str = run_post_hooks("extract_financials", extraction, result_str)
                    result_data = json.loads(result_str)
                    results.append({
                        "company": company,
                        "status": "partial",
                        "data": extraction,
                        "validation_errors": errors,
                        "phase": "batch",
                    })
                else:
                    result_str = json.dumps({
                        "status": "success",
                        "extraction": extraction,
                    })
                    result_str = run_post_hooks("extract_financials", extraction, result_str)
                    result_data = json.loads(result_str)
                    results.append({
                        "company": company,
                        "status": "success",
                        "data": result_data.get("extraction", extraction),
                        "normalized": result_data.get("normalized", False),
                        "phase": "batch",
                    })

                if verbose:
                    status = "success" if not errors else "partial"
                    print(f"  {company}: {status}")
            else:
                results.append({
                    "company": company,
                    "status": "failed",
                    "error": "No extract_financials call in response",
                    "phase": "batch",
                })
                if verbose:
                    print(f"  {company}: no extraction tool call")

        elif result.result.type == "errored":
            results.append({
                "company": company,
                "status": "failed",
                "error": str(result.result.error),
                "phase": "batch",
            })
            if verbose:
                print(f"  {company}: API error")

        elif result.result.type == "expired":
            results.append({
                "company": company,
                "status": "failed",
                "error": "Request expired",
                "phase": "batch",
            })

    return results


def run_batch(companies: list[str], form_type: str = "10-K",
              verbose: bool = True, strict: bool = False) -> dict:
    """
    Full batch extraction pipeline.

    Returns a dict with all results and summary statistics.
    """
    print(f"\n{'=' * 60}")
    print(f"  Batch Mode — {len(companies)} companies")
    print(f"{'=' * 60}")

    # Phase 1: Prefetch
    print(f"\n  Phase 1: Prefetching filings...")
    prefetched = prefetch_filings(companies, form_type, verbose)
    ready_count = sum(1 for p in prefetched if p["status"] == "ready")
    print(f"  Ready: {ready_count}/{len(companies)}")

    if ready_count == 0:
        return {"status": "failed", "error": "No filings available", "results": []}

    # Phase 2: Create batch
    print(f"\n  Phase 2: Creating batch...")
    batch_id = create_batch(prefetched, verbose, strict=strict)
    if not batch_id:
        return {"status": "failed", "error": "Batch creation failed", "results": []}

    # Phase 3: Poll
    print(f"\n  Phase 3: Waiting for batch completion...")
    completed = poll_batch(batch_id, verbose)
    if not completed:
        return {
            "status": "timeout",
            "batch_id": batch_id,
            "message": "Batch still processing. Check later with batch ID.",
        }

    # Phase 4: Collect
    print(f"\n  Phase 4: Collecting results...")
    results = collect_results(batch_id, prefetched, verbose)

    # Summary
    succeeded = sum(1 for r in results if r["status"] == "success")
    partial = sum(1 for r in results if r["status"] == "partial")
    failed = sum(1 for r in results if r["status"] == "failed")

    print(f"\n{'=' * 60}")
    print(f"  Batch Complete")
    print(f"  Succeeded: {succeeded}  Partial: {partial}  Failed: {failed}")
    print(f"{'=' * 60}")

    return {
        "status": "success",
        "batch_id": batch_id,
        "results": results,
        "summary": {
            "total": len(companies),
            "succeeded": succeeded,
            "partial": partial,
            "failed": failed,
        },
    }
