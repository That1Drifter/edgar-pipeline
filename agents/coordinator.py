"""
Coordinator Agent — Hub-and-Spoke Orchestration

The coordinator:
1. Receives a user query (single or multi-company)
2. Decomposes it into subagent tasks
3. Spawns subagents (parallel when independent)
4. Routes results between agents (researcher output → analyzer input)
5. Handles errors gracefully (partial results, retries)

This implements the patterns from Domain 1, Tasks 1.2-1.4 of the cert exam:
- Hub-and-spoke: all inter-subagent communication flows through coordinator
- Subagents have isolated context — no automatic inheritance
- Parallel spawning via multiple tool calls in a single response
- Explicit context passing with structured data
"""

import json
import os
import sys
import io
from anthropic import Anthropic
from agents.subagents import run_researcher, run_analyzer
from tools.definitions import TOOLS

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

MODEL = "claude-sonnet-4-20250514"

# ─── Coordinator Tools ────────────────────────────────────────────────
# The coordinator gets ONE tool: delegate_task.
# It cannot call EDGAR tools directly — only subagents can.
# This enforces the hub-and-spoke pattern.

COORDINATOR_TOOLS = [
    {
        "name": "delegate_research",
        "description": (
            "Spawn a researcher subagent to look up a company and extract financial "
            "data from their SEC filing. The subagent handles the full pipeline: "
            "company lookup, filing retrieval, text extraction, and structured data "
            "extraction with validation. Returns structured financial data or an error. "
            "You can call this multiple times in a SINGLE response to research "
            "multiple companies in parallel."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {
                    "type": "string",
                    "description": "Company name to research (e.g. 'Apple Inc', 'Tesla Inc')"
                },
                "form_type": {
                    "type": "string",
                    "enum": ["10-K", "10-Q"],
                    "description": "Filing type: '10-K' for annual, '10-Q' for quarterly"
                },
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of specific areas to focus on (e.g. ['revenue growth', 'debt levels', 'risk factors'])"
                }
            },
            "required": ["company", "form_type"]
        }
    },
    {
        "name": "delegate_analysis",
        "description": (
            "Spawn an analyzer subagent to compare and synthesize financial data "
            "from multiple companies. Pass the extracted data from researcher "
            "subagents as the 'findings' parameter. The analyzer produces a "
            "structured comparison report. Only call this AFTER all researcher "
            "subagents have returned their results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "description": "Array of extraction results from researcher subagents",
                    "items": {
                        "type": "object",
                        "properties": {
                            "company": {"type": "string"},
                            "data": {"type": "object", "description": "The extracted financial data"},
                            "status": {"type": "string", "enum": ["success", "partial", "failed"]},
                            "notes": {"type": ["string", "null"]}
                        },
                        "required": ["company", "status"]
                    }
                },
                "analysis_type": {
                    "type": "string",
                    "enum": ["comparison", "single_summary", "trend"],
                    "description": "Type of analysis: 'comparison' for multi-company, 'single_summary' for one company deep dive, 'trend' for multi-period"
                },
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific areas to emphasize in the analysis"
                }
            },
            "required": ["findings", "analysis_type"]
        }
    },
]

COORDINATOR_SYSTEM = """You are a financial research coordinator. You manage a team of
specialist subagents to research SEC filings and produce analysis.

YOUR ROLE: Decompose the user's request into subagent tasks, delegate work,
and synthesize results. You NEVER access SEC data directly — only your subagents do.

WORKFLOW:
1. Analyze the user's request to determine which companies and filing types are needed
2. Spawn researcher subagents using delegate_research — one per company
   IMPORTANT: For multiple companies, call delegate_research MULTIPLE TIMES IN A
   SINGLE RESPONSE to run them in parallel
3. Review the results from all researchers
4. If analyzing multiple companies OR the user wants a detailed summary, spawn an
   analyzer subagent using delegate_analysis, passing ALL researcher findings
5. Present the final results to the user

RULES:
- For a single company with no special analysis requested, just present the researcher's
  extraction directly — don't spawn an analyzer unnecessarily
- For multi-company requests, ALWAYS use parallel delegate_research calls
- When passing findings to the analyzer, include ALL data — don't summarize or filter
- If a researcher fails, note the failure and proceed with available data
- Never fabricate data — if a researcher returns partial results, flag the gaps"""


def run_coordinator(query: str, verbose: bool = True) -> dict:
    """
    Run the coordinator to handle a user query.

    The coordinator spawns subagents and routes data between them.
    """
    client = Anthropic()
    messages = [{"role": "user", "content": query}]

    iteration = 0
    max_iterations = 15
    final_result = None

    while iteration < max_iterations:
        iteration += 1

        if verbose:
            print(f"\n{'─' * 60}")
            print(f"  Coordinator — Iteration {iteration}/{max_iterations}")

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=COORDINATOR_SYSTEM,
            tools=COORDINATOR_TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"  stop_reason: {response.stop_reason}")
            print(f"  tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out")

        if response.stop_reason == "end_turn":
            # Coordinator is done — extract final text
            for block in response.content:
                if block.type == "text":
                    final_result = {"status": "success", "summary": block.text, "iterations": iteration}
                    if verbose:
                        print(f"\n  Coordinator finished.")
            break

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            # Collect all tool calls from this response
            # Multiple delegate_research calls = parallel subagent spawning
            tool_calls = [b for b in response.content if b.type == "tool_use"]

            if verbose:
                research_calls = [t for t in tool_calls if t.name == "delegate_research"]
                if len(research_calls) > 1:
                    companies = [t.input.get("company", "?") for t in research_calls]
                    print(f"  Parallel research: {', '.join(companies)}")

            tool_results = []
            for block in tool_calls:
                if block.name == "delegate_research":
                    company = block.input.get("company", "?")
                    form_type = block.input.get("form_type", "10-K")

                    if verbose:
                        print(f"\n  {'=' * 50}")
                        print(f"  Spawning researcher: {company} ({form_type})")
                        print(f"  {'=' * 50}")

                    # ── Spawn researcher subagent ──
                    result = run_researcher(company, form_type, verbose=verbose)

                    if verbose:
                        status = result.get("status", "?")
                        if status == "success":
                            rev = result.get("data", {}).get("revenue", {})
                            print(f"  Researcher returned: {company} — revenue {rev.get('value')} {rev.get('unit', '')}")
                        else:
                            print(f"  Researcher returned: {company} — {status}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

                elif block.name == "delegate_analysis":
                    findings = block.input.get("findings", [])
                    analysis_type = block.input.get("analysis_type", "comparison")
                    focus_areas = block.input.get("focus_areas", [])

                    if verbose:
                        print(f"\n  {'=' * 50}")
                        print(f"  Spawning analyzer: {analysis_type}")
                        print(f"  Companies: {[f.get('company','?') for f in findings]}")
                        print(f"  {'=' * 50}")

                    # ── Spawn analyzer subagent ──
                    result = run_analyzer(findings, analysis_type, focus_areas, verbose=verbose)

                    if verbose:
                        print(f"  Analyzer returned: {result.get('status', '?')}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            if verbose:
                print(f"  Unexpected stop_reason: {response.stop_reason}")
            break

    if iteration >= max_iterations:
        if verbose:
            print(f"\n  Coordinator hit iteration cap.")

    return final_result or {"status": "no_result", "iterations": iteration}
