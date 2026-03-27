"""
Streaming Extractor — Real-Time Extraction Progress

Same agentic loop as extractor.py, but uses client.messages.stream()
for real-time output. Shows text as it generates and tool names as
they're invoked.

This implements cert exam concepts:
- D4: Streaming API with event handling
- stream.text_stream for text deltas
- stream.get_final_message() for the full response to continue the loop
"""

import json
import os
import sys
import io
from anthropic import Anthropic

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from tools.definitions import (handle_tool_call, get_cached_tools, make_cached_system,
                               make_citation_tool_result)
from costs import CostTracker

# ─── Configuration ────────────────────────────────────────────────────

MODEL = os.environ.get("EDGAR_MODEL", "claude-sonnet-4-20250514")
MAX_ITERATIONS = 25
MAX_RETRIES = 2

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


def run_extraction_stream(company: str, form_type: str = "10-K",
                          strict: bool = False) -> dict:
    """
    Streaming extraction — real-time progress display.

    Uses client.messages.stream() context manager. The agentic loop is
    identical to the non-streaming version, but inner API calls stream
    text and tool invocations in real time.
    """
    client = Anthropic()
    tools = get_cached_tools(strict=strict)
    system = make_cached_system(SYSTEM_PROMPT)
    tracker = CostTracker(MODEL)

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
        print(f"\n{'─' * 60}")
        print(f"  Iteration {iteration}/{MAX_ITERATIONS}")

        # ── Streaming API call ────────────────────────────────────
        with client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages,
        ) as stream:
            # Print text as it arrives
            print(f"  ", end="")
            for event in stream:
                # content_block_start tells us when a tool call begins
                if hasattr(event, 'type'):
                    if event.type == "content_block_start":
                        block = event.content_block
                        if hasattr(block, 'type') and block.type == "tool_use":
                            tool_name = block.name
                            print(f"\n  Tool: {tool_name}", end="")
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, 'type') and delta.type == "text_delta":
                            print(delta.text, end="", flush=True)

            # Get the complete response for the agentic loop
            response = stream.get_final_message()

        tracker.track(response.usage)
        print()  # newline after streaming output
        print(f"  stop_reason: {response.stop_reason} | "
              f"tokens: {response.usage.input_tokens}+{response.usage.output_tokens}")

        # ── Check stop_reason (same as non-streaming) ─────────────
        if response.stop_reason == "end_turn":
            print(f"\n  Agent finished.")
            break

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input

                    if tool_name == "extract_financials":
                        rev = tool_input.get('revenue', {})
                        print(f"    company: {tool_input.get('company_name', '?')}")
                        print(f"    revenue: {rev.get('value')} {rev.get('unit', '')} "
                              f"(conf: {rev.get('confidence', '?')})")

                    # Execute the tool (with hooks)
                    result_str = handle_tool_call(tool_name, tool_input)

                    # Track extraction validation
                    if tool_name == "extract_financials":
                        result_data = json.loads(result_str)
                        if result_data.get("status") == "success":
                            extraction_result = result_data["extraction"]
                            print(f"  Extraction validated!")
                        elif result_data.get("status") == "validation_failed":
                            retry_count += 1
                            print(f"  Validation failed (attempt {retry_count}/{MAX_RETRIES}):")
                            for err in result_data.get("errors", []):
                                print(f"    - {err}")
                            if retry_count > MAX_RETRIES:
                                print(f"  Max retries exceeded. Using last extraction.")
                                extraction_result = tool_input

                    # Citation support: wrap fetch_filing in document block
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
                                continue
                        except json.JSONDecodeError:
                            pass

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            print(f"  Unexpected stop_reason: {response.stop_reason}")
            break

    if iteration >= MAX_ITERATIONS:
        print(f"\n  Hit iteration cap ({MAX_ITERATIONS}).")

    if extraction_result:
        return {"status": "success", "data": extraction_result, "iterations": iteration,
                "cost": tracker}
    else:
        return {
            "status": "no_extraction",
            "message": "Agent completed without producing a validated extraction.",
            "iterations": iteration, "cost": tracker
        }
