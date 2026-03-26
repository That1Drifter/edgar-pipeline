"""
Tool definitions for the extraction pipeline.

Each tool has:
- A JSON schema (what the model calls)
- A handler function (what actually executes)
- A detailed description (how the model decides when to use it)

These map directly to the Claude API tool_use format.
"""

from edgar.fetcher import lookup_cik, get_company_filings, fetch_filing_text

# ─── Tool Schemas ─────────────────────────────────────────────────────
# These are sent to Claude via the `tools` parameter.
# The model reads these descriptions to decide which tool to call.

TOOLS = [
    {
        "name": "lookup_company",
        "description": (
            "Look up a company's SEC CIK identifier by name. Use this first when "
            "you have a company name but need to find their filings. Returns the "
            "CIK number or null if not found. Example: 'Apple Inc' -> '0000320193'. "
            "Do NOT use this if you already have a CIK number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Company name to search for, e.g. 'Tesla Inc', 'Apple Inc'"
                }
            },
            "required": ["company_name"]
        }
    },
    {
        "name": "get_filings",
        "description": (
            "Retrieve a list of SEC filings for a company by CIK number. "
            "Returns filing dates, accession numbers, and URLs. Use this after "
            "lookup_company to find available filings. Supports 10-K (annual) "
            "and 10-Q (quarterly) form types. Returns up to 5 most recent filings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cik": {
                    "type": "string",
                    "description": "The company's CIK number (e.g. '0000320193')"
                },
                "form_type": {
                    "type": "string",
                    "enum": ["10-K", "10-Q"],
                    "description": "Filing type. Use '10-K' for annual reports, '10-Q' for quarterly."
                }
            },
            "required": ["cik", "form_type"]
        }
    },
    {
        "name": "fetch_filing",
        "description": (
            "Fetch and return the text content of a specific SEC filing by URL. "
            "Returns the filing as cleaned plain text (HTML tags stripped). "
            "The text is truncated to ~50,000 characters. Use this to read the "
            "actual content of a filing before extracting data from it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to the filing document"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "extract_financials",
        "description": (
            "Submit extracted financial data from a 10-K or 10-Q filing. "
            "Call this tool AFTER reading a filing with fetch_filing. "
            "Extract the values directly from the filing text — do not estimate "
            "or calculate values that aren't explicitly stated. Use null for any "
            "field where the value is not clearly present in the document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Official company name as stated in the filing"
                },
                "ticker": {
                    "type": ["string", "null"],
                    "description": "Stock ticker symbol, or null if not found"
                },
                "form_type": {
                    "type": "string",
                    "enum": ["10-K", "10-Q"]
                },
                "period_end": {
                    "type": ["string", "null"],
                    "description": "Fiscal period end date in YYYY-MM-DD format, or null if unclear"
                },
                "fiscal_year": {
                    "type": ["integer", "null"],
                    "description": "Fiscal year (e.g. 2024), or null if not stated"
                },
                "revenue": {
                    "type": "object",
                    "description": "Total revenue / net sales",
                    "properties": {
                        "value": { "type": ["number", "null"], "description": "Dollar amount, or null if not found" },
                        "unit": { "type": "string", "enum": ["dollars", "thousands", "millions", "billions"], "description": "Unit scale as stated in the filing" },
                        "label": { "type": ["string", "null"], "description": "Exact label used in the filing (e.g. 'Net Sales', 'Total Revenue')" },
                        "confidence": { "type": "number", "description": "0.0-1.0. Use 0.9+ for clearly printed values, 0.6-0.8 for values requiring interpretation, below 0.5 for inferred" }
                    },
                    "required": ["value", "unit", "confidence"]
                },
                "net_income": {
                    "type": "object",
                    "description": "Net income / net earnings",
                    "properties": {
                        "value": { "type": ["number", "null"], "description": "Dollar amount, or null if not found. Use negative for net loss." },
                        "unit": { "type": "string", "enum": ["dollars", "thousands", "millions", "billions"] },
                        "label": { "type": ["string", "null"], "description": "Exact label used in the filing" },
                        "confidence": { "type": "number", "description": "0.0-1.0" }
                    },
                    "required": ["value", "unit", "confidence"]
                },
                "total_assets": {
                    "type": "object",
                    "description": "Total assets from the balance sheet",
                    "properties": {
                        "value": { "type": ["number", "null"] },
                        "unit": { "type": "string", "enum": ["dollars", "thousands", "millions", "billions"] },
                        "confidence": { "type": "number" }
                    },
                    "required": ["value", "unit", "confidence"]
                },
                "total_liabilities": {
                    "type": "object",
                    "description": "Total liabilities from the balance sheet",
                    "properties": {
                        "value": { "type": ["number", "null"] },
                        "unit": { "type": "string", "enum": ["dollars", "thousands", "millions", "billions"] },
                        "confidence": { "type": "number" }
                    },
                    "required": ["value", "unit", "confidence"]
                },
                "eps": {
                    "type": "object",
                    "description": "Earnings per share (diluted preferred)",
                    "properties": {
                        "value": { "type": ["number", "null"] },
                        "diluted": { "type": ["boolean", "null"], "description": "True if diluted EPS, false if basic, null if unclear" },
                        "confidence": { "type": "number" }
                    },
                    "required": ["value", "confidence"]
                },
                "risk_factors": {
                    "type": "array",
                    "description": "Top 3-5 risk factors mentioned in the filing. Empty array if section not found.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": { "type": "string", "description": "Risk factor heading or summary" },
                            "category": {
                                "type": "string",
                                "enum": ["market", "regulatory", "operational", "financial", "legal", "technology", "competitive", "other"],
                                "description": "Category. Use 'other' if none of the standard categories fit."
                            },
                            "category_detail": {
                                "type": ["string", "null"],
                                "description": "If category is 'other', describe the actual category here"
                            }
                        },
                        "required": ["title", "category"]
                    }
                },
                "notes": {
                    "type": ["string", "null"],
                    "description": "Any issues, ambiguities, or conflicts found during extraction. E.g. 'Revenue restated in footnote 3 from $4.2B to $4.1B'"
                },
                "conflict_detected": {
                    "type": "boolean",
                    "description": "True if conflicting values were found in the document (e.g. table vs footnote disagree)"
                }
            },
            "required": [
                "company_name", "form_type", "revenue", "net_income",
                "total_assets", "total_liabilities", "eps",
                "risk_factors", "conflict_detected"
            ]
        }
    },
]


# ─── Tool Handlers ────────────────────────────────────────────────────
# These execute when the model calls a tool.

def handle_tool_call(name: str, input: dict) -> str:
    """Route a tool call to the right handler and return the result as a string."""
    handlers = {
        "lookup_company": _handle_lookup,
        "get_filings": _handle_get_filings,
        "fetch_filing": _handle_fetch_filing,
        "extract_financials": _handle_extract_financials,
    }

    handler = handlers.get(name)
    if not handler:
        return f'{{"error": "Unknown tool: {name}", "is_retryable": false}}'

    try:
        return handler(input)
    except Exception as e:
        return (
            f'{{"error": "{str(e)}", '
            f'"error_category": "transient", '
            f'"is_retryable": true, '
            f'"attempted": "{name}"}}'
        )


def _handle_lookup(input: dict) -> str:
    """Look up company CIK."""
    import json
    name = input["company_name"]
    cik = lookup_cik(name)
    if cik:
        return json.dumps({"cik": cik, "company_name": name})
    return json.dumps({
        "error": f"No SEC filings found for '{name}'",
        "error_category": "validation",
        "is_retryable": False,
        "suggestion": "Try the official company name (e.g. 'Apple Inc' not 'Apple')"
    })


def _handle_get_filings(input: dict) -> str:
    """Get list of filings for a CIK."""
    import json
    cik = input["cik"]
    form_type = input.get("form_type", "10-K")
    filings = get_company_filings(cik, form_type)
    if filings and "error" not in filings[0]:
        return json.dumps({"filings": filings, "count": len(filings)})
    return json.dumps({
        "error": f"Could not retrieve filings for CIK {cik}",
        "error_category": "transient",
        "is_retryable": True,
        "partial_results": filings
    })


def _handle_fetch_filing(input: dict) -> str:
    """Fetch filing text content."""
    import json
    url = input["url"]
    text = fetch_filing_text(url, max_chars=50000)
    if text.startswith("ERROR"):
        return json.dumps({
            "error": text,
            "error_category": "transient",
            "is_retryable": True,
            "url": url
        })
    return json.dumps({
        "text": text,
        "chars": len(text),
        "truncated": len(text) >= 49999,
        "source_url": url
    })


def _handle_extract_financials(input: dict) -> str:
    """
    The extract_financials tool is special — the model IS the extractor.
    When it calls this tool, the input IS the extracted data.
    We validate it and return the validation result.
    """
    import json
    errors = _validate_extraction(input)
    if errors:
        return json.dumps({
            "status": "validation_failed",
            "errors": errors,
            "message": "Re-examine the filing and correct these issues. The original extraction is returned for reference.",
            "extraction": input
        })
    return json.dumps({
        "status": "success",
        "message": "Extraction validated successfully.",
        "extraction": input
    })


def _validate_extraction(data: dict) -> list[str]:
    """Validate extracted financial data. Returns list of error strings."""
    errors = []

    # Check required fields aren't all null
    revenue = data.get("revenue", {})
    if revenue.get("value") is None and revenue.get("confidence", 0) > 0.5:
        errors.append("Revenue value is null but confidence is high — re-check the income statement.")

    net_income = data.get("net_income", {})
    if net_income.get("value") is None and net_income.get("confidence", 0) > 0.5:
        errors.append("Net income value is null but confidence is high — re-check the income statement.")

    # Cross-validate: assets should be > liabilities (usually)
    assets = data.get("total_assets", {})
    liab = data.get("total_liabilities", {})
    if (assets.get("value") is not None and liab.get("value") is not None
            and assets.get("unit") == liab.get("unit")):
        if liab["value"] > assets["value"]:
            errors.append(
                f"Total liabilities ({liab['value']} {liab['unit']}) exceed total assets "
                f"({assets['value']} {assets['unit']}). Verify both values."
            )

    # Check that confidence scores are calibrated (not all 0.95+)
    confidences = []
    for field in ["revenue", "net_income", "total_assets", "total_liabilities", "eps"]:
        c = data.get(field, {}).get("confidence")
        if c is not None:
            confidences.append(c)
    if confidences and all(c >= 0.95 for c in confidences):
        errors.append(
            "All confidence scores are 0.95+. Re-calibrate: use 0.9+ only for clearly "
            "printed values, 0.6-0.8 for values requiring interpretation."
        )

    # Period end date format
    period = data.get("period_end")
    if period and not _is_valid_date(period):
        errors.append(f"period_end '{period}' is not in YYYY-MM-DD format.")

    return errors


def _is_valid_date(s: str) -> bool:
    """Check if string is YYYY-MM-DD format."""
    import re
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}$', s))
