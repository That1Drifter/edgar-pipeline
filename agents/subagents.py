"""
Specialized Subagents — Researcher and Analyzer

Each subagent:
- Has isolated context (no automatic inheritance from coordinator)
- Gets only the tools relevant to its role (scoped tool sets)
- Receives explicit context in its prompt (not raw user queries)
- Returns structured output for the coordinator to route

This implements cert exam concepts:
- D1 Task 1.3: Subagent invocation, context passing, scoped tools
- D2 Task 2.3: Tool distribution across agents (4-5 tools max per agent)
- D5 Task 5.3: Error propagation with structured context
"""

import json
import sys
import io
from anthropic import Anthropic
from tools.definitions import TOOLS, handle_tool_call

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

MODEL = "claude-sonnet-4-20250514"

# ─── Researcher Subagent ──────────────────────────────────────────────
# Tools: lookup_company, get_filings, fetch_filing, extract_financials
# This is the same tool set as Phase 1, but now scoped to a subagent role.

RESEARCHER_TOOLS = TOOLS  # All 4 EDGAR tools

RESEARCHER_SYSTEM = """You are a financial data researcher. Your job is to look up a
specific company's SEC filing and extract structured financial data from it.

You have been delegated this task by a coordinator agent. Complete it fully and return
the extracted data. Do not ask questions — work with what you're given.

WORKFLOW:
1. Use lookup_company to find the CIK
2. Use get_filings to find the most recent filing of the requested type
3. Use fetch_filing to read the document
4. Use extract_financials to submit your structured extraction

EXTRACTION RULES:
- Extract values EXACTLY as stated — do not calculate or estimate
- Use null for fields where the value isn't clearly present
- Preserve original labels from the filing
- Note discrepancies in the notes field
- Confidence: 0.9+ for clear table values, 0.7-0.8 for interpreted values, below 0.5 for inferred"""

MAX_RESEARCHER_ITERATIONS = 15


def run_researcher(company: str, form_type: str = "10-K", verbose: bool = True) -> dict:
    """
    Run a researcher subagent for a single company.

    The coordinator calls this — the researcher gets a task-specific prompt
    (not the raw user query) with explicit scope.
    """
    client = Anthropic()

    # Task-specific prompt — crafted by the coordinator, not the raw user query
    # This is the explicit context passing pattern (D1 Task 1.3)
    messages = [
        {
            "role": "user",
            "content": (
                f"Research {company} and extract financial data from their most recent "
                f"{form_type} filing. Return the complete structured extraction."
            )
        }
    ]

    iteration = 0
    extraction_result = None
    retry_count = 0

    while iteration < MAX_RESEARCHER_ITERATIONS:
        iteration += 1

        if verbose:
            print(f"    [researcher:{company}] iteration {iteration}")

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=RESEARCHER_SYSTEM,
            tools=RESEARCHER_TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"    [researcher:{company}] stop_reason: {response.stop_reason} | "
                  f"tokens: {response.usage.input_tokens}+{response.usage.output_tokens}")

        if response.stop_reason == "end_turn":
            break

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    if verbose:
                        extra = ""
                        if block.name == "extract_financials":
                            rev = block.input.get("revenue", {})
                            extra = f" | revenue: {rev.get('value')} {rev.get('unit','')}"
                        print(f"    [researcher:{company}] tool: {block.name}{extra}")

                    result_str = handle_tool_call(block.name, block.input)

                    # Track extraction success/failure for retry logic
                    if block.name == "extract_financials":
                        result_data = json.loads(result_str)
                        if result_data.get("status") == "success":
                            extraction_result = result_data["extraction"]
                            if verbose:
                                print(f"    [researcher:{company}] extraction validated")
                        elif result_data.get("status") == "validation_failed":
                            retry_count += 1
                            if verbose:
                                errs = result_data.get("errors", [])
                                print(f"    [researcher:{company}] validation failed ({retry_count}): {errs[0] if errs else '?'}")
                            if retry_count > 2:
                                extraction_result = block.input

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    # ── Return structured result to coordinator ───────────────────
    # Include everything the coordinator needs to route to the analyzer
    if extraction_result:
        return {
            "status": "success",
            "company": company,
            "form_type": form_type,
            "data": extraction_result,
            "iterations": iteration,
        }
    else:
        # Structured error context (D5 Task 5.3)
        return {
            "status": "failed",
            "company": company,
            "form_type": form_type,
            "error": "Researcher completed without producing a validated extraction",
            "error_category": "extraction_failure",
            "is_retryable": True,
            "iterations": iteration,
            "partial_results": None,
        }


# ─── Analyzer Subagent ────────────────────────────────────────────────
# Tools: save_report (single tool — the analyzer's job is synthesis, not research)
# The analyzer should NOT have access to EDGAR tools.

ANALYZER_TOOLS = [
    {
        "name": "save_report",
        "description": (
            "Save the final analysis report. Call this once with the complete "
            "structured analysis. The report should include all findings with "
            "source attribution preserved from the researcher data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Report title"
                },
                "companies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Companies covered in this report"
                },
                "highlights": {
                    "type": "array",
                    "description": "Key findings — 3-5 bullet points",
                    "items": {
                        "type": "object",
                        "properties": {
                            "finding": {"type": "string"},
                            "companies_involved": {"type": "array", "items": {"type": "string"}},
                            "data_quality": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "Based on source extraction confidence"
                            }
                        },
                        "required": ["finding", "data_quality"]
                    }
                },
                "comparison": {
                    "type": ["object", "null"],
                    "description": "Side-by-side metrics comparison (null for single company)",
                    "properties": {
                        "metrics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "metric": {"type": "string"},
                                    "values": {
                                        "type": "object",
                                        "description": "Company name → value mapping",
                                        "additionalProperties": {"type": ["string", "null"]}
                                    },
                                    "winner": {"type": ["string", "null"], "description": "Company name or null if not applicable"},
                                    "notes": {"type": ["string", "null"]}
                                },
                                "required": ["metric", "values"]
                            }
                        }
                    }
                },
                "risk_analysis": {
                    "type": ["object", "null"],
                    "description": "Comparative risk analysis",
                    "properties": {
                        "common_risks": {"type": "array", "items": {"type": "string"}},
                        "unique_risks": {
                            "type": "object",
                            "description": "Company name → unique risk factors",
                            "additionalProperties": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                },
                "data_gaps": {
                    "type": "array",
                    "description": "List any missing data, failed extractions, or low-confidence values",
                    "items": {"type": "string"}
                },
                "conflicts": {
                    "type": "array",
                    "description": "Any data conflicts detected across sources",
                    "items": {"type": "string"}
                }
            },
            "required": ["title", "companies", "highlights", "data_gaps"]
        }
    }
]

ANALYZER_SYSTEM = """You are a financial data analyzer. You receive extracted financial
data from researcher agents and produce structured analysis reports.

You do NOT have access to SEC filings or search tools. Work only with the data provided.

RULES:
- Preserve source attribution — every claim must trace to a specific company's filing
- Flag data quality issues: note which values had low confidence scores
- If data is missing for a company (failed extraction), note the gap explicitly
- For comparisons, present both values side-by-side — don't just pick the "better" one
- If values conflict or seem inconsistent, flag them in the conflicts array
- data_quality ratings: "high" for confidence >= 0.8, "medium" for 0.5-0.8, "low" for < 0.5"""

MAX_ANALYZER_ITERATIONS = 8


def run_analyzer(findings: list, analysis_type: str, focus_areas: list = None, verbose: bool = True) -> dict:
    """
    Run the analyzer subagent to synthesize researcher findings.

    The coordinator passes ALL researcher data explicitly — the analyzer
    has no access to prior context or EDGAR tools.
    """
    client = Anthropic()

    # Build the task-specific prompt with ALL context included explicitly
    # This is the structured data passing pattern (D1 Task 1.3)
    companies = [f.get("company", "?") for f in findings]
    focus_str = f"\nFocus areas: {', '.join(focus_areas)}" if focus_areas else ""

    prompt = (
        f"Analyze the following financial data extracted from SEC filings.\n"
        f"Analysis type: {analysis_type}\n"
        f"Companies: {', '.join(companies)}{focus_str}\n\n"
        f"EXTRACTED DATA:\n{json.dumps(findings, indent=2, default=str)}\n\n"
        f"Produce a structured report using the save_report tool."
    )

    messages = [{"role": "user", "content": prompt}]
    iteration = 0
    report = None

    while iteration < MAX_ANALYZER_ITERATIONS:
        iteration += 1

        if verbose:
            print(f"    [analyzer] iteration {iteration}")

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=ANALYZER_SYSTEM,
            tools=ANALYZER_TOOLS,
            # Force tool call — analyzer MUST produce structured output
            tool_choice={"type": "any"},
            messages=messages,
        )

        if verbose:
            print(f"    [analyzer] stop_reason: {response.stop_reason} | "
                  f"tokens: {response.usage.input_tokens}+{response.usage.output_tokens}")

        if response.stop_reason == "end_turn":
            break

        elif response.stop_reason == "tool_use":
            for block in response.content:
                if block.type == "tool_use" and block.name == "save_report":
                    report = block.input
                    if verbose:
                        print(f"    [analyzer] report generated: {report.get('title', '?')}")
                        print(f"    [analyzer] highlights: {len(report.get('highlights', []))}")
                        print(f"    [analyzer] data gaps: {len(report.get('data_gaps', []))}")
            break  # Report produced, we're done
        else:
            break

    if report:
        return {"status": "success", "report": report, "iterations": iteration}
    else:
        return {
            "status": "failed",
            "error": "Analyzer did not produce a report",
            "error_category": "analysis_failure",
            "is_retryable": True,
            "iterations": iteration,
        }
