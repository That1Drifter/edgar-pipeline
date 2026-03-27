"""
Core Agentic Loop — SEC Filing Extractor

This implements the fundamental agentic pattern:
1. Send a request to Claude with tools
2. Check stop_reason
3. If "tool_use" → execute the tool, append result, loop
4. If "end_turn" → done, return final response
5. Validation-retry: if extraction fails validation, feed errors back

This is the pattern tested in Domain 1, Task 1.1 of the cert exam.
"""

import json
import os
import sys
import io
from anthropic import Anthropic

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from tools.definitions import (TOOLS, TOOLS_STRICT, handle_tool_call,
                               get_cached_tools, make_cached_system,
                               make_citation_tool_result)
from costs import CostTracker

# ─── Configuration ────────────────────────────────────────────────────

MODEL = os.environ.get("EDGAR_MODEL", "claude-sonnet-4-20250514")
MAX_ITERATIONS = 25  # Safety cap — graceful termination, not primary stopping mechanism
MAX_RETRIES = 2      # For validation-retry loops

SYSTEM_PROMPT = """You are a financial data extraction agent specialized in SEC EDGAR filings.

Your job: Given a company name and filing type, look up the company, fetch the filing,
read it carefully, and extract structured financial data.

WORKFLOW:
1. Use lookup_company to find the company's CIK number
2. Use get_filings to find available filings
3. Use fetch_filing to read the most recent filing of the requested type
4. Read the filing text carefully, then use extract_financials to submit your extraction

EXTRACTION RULES:
- Extract values EXACTLY as stated in the filing — do not calculate or estimate
- Use null for any field where the value is not clearly stated in the document
- Preserve the original label used in the filing (e.g. "Net Sales" not "Revenue")
- Note any discrepancies between tables and footnotes in the notes field
- Set conflict_detected to true if you find contradictory values
- Confidence calibration:
  * 0.9-1.0: Value is clearly printed in a labeled financial table
  * 0.7-0.8: Value requires some interpretation (e.g. derived from segment breakdown)
  * 0.4-0.6: Value is inferred or approximated from surrounding context
  * Below 0.4: Educated guess — flag for human review

If extract_financials returns validation errors, re-read the relevant sections
and correct your extraction based on the specific error feedback."""


def run_extraction(company: str, form_type: str = "10-K", verbose: bool = True,
                   strict: bool = False) -> dict:
    """
    Run the full extraction pipeline for a company filing.

    Returns the extracted financial data or an error dict.
    """
    client = Anthropic()
    tools = get_cached_tools(strict=strict)
    system = make_cached_system(SYSTEM_PROMPT)
    tracker = CostTracker(MODEL)

    # Initial user message
    messages = [
        {
            "role": "user",
            "content": f"Extract financial data from {company}'s most recent {form_type} filing."
        }
    ]

    iteration = 0
    extraction_result = None
    retry_count = 0

    while iteration < MAX_ITERATIONS:
        iteration += 1

        if verbose:
            print(f"\n{'─' * 60}")
            print(f"  Iteration {iteration}/{MAX_ITERATIONS}")

        # ── Step 1: Send request to Claude ────────────────────────
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=tools,
            # First turn: let the model choose freely (auto)
            # It should start with lookup_company
            messages=messages,
        )
        tracker.track(response.usage)

        if verbose:
            print(f"  stop_reason: {response.stop_reason}")
            print(f"  tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out")

        # ── Step 2: Check stop_reason ─────────────────────────────

        if response.stop_reason == "end_turn":
            # Model is done — extract any final text
            if verbose:
                print(f"\n  Agent finished.")
                for block in response.content:
                    if block.type == "text":
                        print(f"  Final message: {block.text[:200]}")
            break

        elif response.stop_reason == "tool_use":
            # Model wants to call a tool — execute it

            # Add the assistant's response (with tool_use blocks) to history
            messages.append({"role": "assistant", "content": response.content})

            # Process each tool call in the response
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input

                    if verbose:
                        print(f"  Tool call: {tool_name}")
                        if tool_name == "extract_financials":
                            print(f"    company: {tool_input.get('company_name', '?')}")
                            rev = tool_input.get('revenue', {})
                            print(f"    revenue: {rev.get('value')} {rev.get('unit', '')} (conf: {rev.get('confidence', '?')})")
                        elif tool_name != "fetch_filing":
                            print(f"    input: {json.dumps(tool_input)[:120]}")

                    # ── Step 3: Execute the tool ──────────────────
                    result_str = handle_tool_call(tool_name, tool_input)

                    # Check if this was a successful extraction
                    if tool_name == "extract_financials":
                        result_data = json.loads(result_str)
                        if result_data.get("status") == "success":
                            extraction_result = result_data["extraction"]
                            if verbose:
                                print(f"  ✓ Extraction validated successfully!")
                        elif result_data.get("status") == "validation_failed":
                            retry_count += 1
                            if verbose:
                                print(f"  ✗ Validation failed (attempt {retry_count}/{MAX_RETRIES}):")
                                for err in result_data.get("errors", []):
                                    print(f"    - {err}")
                            if retry_count > MAX_RETRIES:
                                if verbose:
                                    print(f"  Max retries exceeded. Using last extraction.")
                                extraction_result = tool_input  # Use what we have
                                # Don't break — let the model finish naturally

                    # ── Citation support: wrap fetch_filing in document block ──
                    if tool_name == "fetch_filing":
                        try:
                            filing_data = json.loads(result_str)
                            if "text" in filing_data:
                                tool_results.append(
                                    make_citation_tool_result(
                                        block.id,
                                        filing_data["text"],
                                        filing_data.get("source_url", ""),
                                        filing_data.get("truncated", False),
                                    )
                                )
                                continue  # Skip the default append below
                        except json.JSONDecodeError:
                            pass  # Fall through to default

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            # ── Step 4: Append tool results to conversation ───────
            # This is critical — the model needs to see what the tools returned
            messages.append({"role": "user", "content": tool_results})

        else:
            # Unexpected stop reason
            if verbose:
                print(f"  Unexpected stop_reason: {response.stop_reason}")
            break

    # ── Graceful termination on iteration cap ─────────────────────
    if iteration >= MAX_ITERATIONS:
        if verbose:
            print(f"\n  Hit iteration cap ({MAX_ITERATIONS}). Returning best result.")

    if extraction_result:
        return {"status": "success", "data": extraction_result, "iterations": iteration,
                "cost": tracker}
    else:
        return {
            "status": "no_extraction",
            "message": "Agent completed without producing a validated extraction.",
            "iterations": iteration, "cost": tracker
        }


def save_result(result: dict, output_dir: str = "output"):
    """Save extraction result to a JSON file."""
    os.makedirs(output_dir, exist_ok=True)
    company = result.get("data", {}).get("company_name", "unknown")
    safe_name = "".join(c if c.isalnum() else "_" for c in company)
    path = os.path.join(output_dir, f"{safe_name}.json")
    with open(path, "w") as f:
        # Serialize CostTracker as dict
        save_data = {k: (v.to_dict() if hasattr(v, 'to_dict') else v)
                     for k, v in result.items()}
        json.dump(save_data, f, indent=2)
    return path
