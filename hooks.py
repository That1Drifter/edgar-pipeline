"""
Hook System — Pre-Tool and Post-Tool Processing

Hooks intercept tool calls at two points:
1. PreToolCall: runs BEFORE the tool executes (audit logging, PII blocking)
2. PostToolUse: runs AFTER the tool returns (data normalization, review flagging)

Each hook is a callable that receives context and can:
- Modify the input (PreToolCall) or output (PostToolUse)
- Block execution (PreToolCall returning a block signal)
- Flag results for review (PostToolUse adding review flags)

This implements cert exam concepts:
- D3 Task 3.1: PostToolUse hooks for data normalization
- D3 Task 3.2: PreToolCall hooks for guardrails (PII blocking)
- D5 Task 5.2: Audit trail via hook logging
"""

import json
import re
import os
import logging
from datetime import datetime, timezone
from typing import Any

# ─── Audit Logger ────────────────────────────────────────────────────

_audit_logger = None


def _get_audit_logger() -> logging.Logger:
    """Lazy-init file logger for the audit trail."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = logging.getLogger("edgar.audit")
        _audit_logger.setLevel(logging.INFO)
        _audit_logger.propagate = False

        os.makedirs("output", exist_ok=True)
        handler = logging.FileHandler("output/audit.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        _audit_logger.addHandler(handler)
    return _audit_logger


# ─── PreToolCall Hooks ───────────────────────────────────────────────

def pre_audit_log(tool_name: str, tool_input: dict) -> dict | None:
    """
    Log every tool call before execution.

    Returns None (allow) or a dict with "blocked" key to halt execution.
    Writes structured JSON lines to output/audit.log.
    """
    logger = _get_audit_logger()
    entry = {
        "event": "tool_call",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "input_keys": list(tool_input.keys()),
    }

    # For fetch_filing, log the URL (useful for debugging)
    if tool_name == "fetch_filing":
        entry["url"] = tool_input.get("url", "?")

    # For extract_financials, log the company
    if tool_name == "extract_financials":
        entry["company"] = tool_input.get("company_name", "?")

    logger.info(json.dumps(entry))
    return None  # Allow execution


def pre_pii_blocker(tool_name: str, tool_input: dict) -> dict | None:
    """
    Block tool calls that contain PII patterns.

    Scans all string values in tool_input for SSN, email, phone patterns.
    If found, blocks the call and returns a structured error.
    """
    # Only scan tools that send data out or could leak PII
    if tool_name not in ("extract_financials", "save_report"):
        return None

    pii_patterns = {
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    }

    def scan_value(val: Any, path: str = "") -> list[str]:
        """Recursively scan for PII in nested structures."""
        findings = []
        if isinstance(val, str):
            for pii_type, pattern in pii_patterns.items():
                if re.search(pattern, val):
                    findings.append(f"{pii_type} detected in {path or 'value'}")
        elif isinstance(val, dict):
            for k, v in val.items():
                findings.extend(scan_value(v, f"{path}.{k}" if path else k))
        elif isinstance(val, list):
            for i, v in enumerate(val):
                findings.extend(scan_value(v, f"{path}[{i}]"))
        return findings

    findings = scan_value(tool_input)
    if findings:
        # Log the blocked call
        logger = _get_audit_logger()
        logger.info(json.dumps({
            "event": "pii_blocked",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "findings": findings,
        }))
        return {
            "blocked": True,
            "error": "PII detected in tool input — call blocked",
            "error_category": "pii_violation",
            "is_retryable": True,
            "details": findings,
            "message": "Remove personally identifiable information and retry.",
        }
    return None


# ─── PostToolUse Hooks ───────────────────────────────────────────────

def post_normalize_financials(tool_name: str, tool_input: dict, result_str: str) -> str:
    """
    Normalize financial data after extract_financials returns success.

    Standardizations:
    - Convert all monetary values to millions for consistent comparison
    - Clean up whitespace in labels
    - Ensure confidence scores are clamped to [0.0, 1.0]
    """
    if tool_name != "extract_financials":
        return result_str

    try:
        result = json.loads(result_str)
    except json.JSONDecodeError:
        return result_str

    if result.get("status") != "success":
        return result_str

    extraction = result.get("extraction", {})

    # Normalize each financial field
    for field_name in ("revenue", "net_income", "total_assets", "total_liabilities"):
        field = extraction.get(field_name, {})
        if not field or field.get("value") is None:
            continue

        # Clamp confidence
        if "confidence" in field:
            field["confidence"] = max(0.0, min(1.0, field["confidence"]))

        # Normalize to millions
        value = field["value"]
        unit = field.get("unit", "dollars")
        conversion = {
            "dollars": 1 / 1_000_000,
            "thousands": 1 / 1_000,
            "millions": 1,
            "billions": 1_000,
        }
        if unit in conversion and unit != "millions":
            field["original_value"] = value
            field["original_unit"] = unit
            field["value"] = round(value * conversion[unit], 2)
            field["unit"] = "millions"

        # Clean label whitespace
        if field.get("label"):
            field["label"] = " ".join(field["label"].split())

    # Clamp EPS confidence
    eps = extraction.get("eps", {})
    if "confidence" in eps:
        eps["confidence"] = max(0.0, min(1.0, eps["confidence"]))

    result["extraction"] = extraction
    result["normalized"] = True
    return json.dumps(result)


def post_audit_log(tool_name: str, tool_input: dict, result_str: str) -> str:
    """Log tool results after execution (completes the audit trail)."""
    logger = _get_audit_logger()

    entry = {
        "event": "tool_result",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "result_size": len(result_str),
    }

    # Log extraction status specifically
    if tool_name == "extract_financials":
        try:
            result = json.loads(result_str)
            entry["extraction_status"] = result.get("status", "?")
            if result.get("status") == "validation_failed":
                entry["errors"] = result.get("errors", [])
        except json.JSONDecodeError:
            entry["extraction_status"] = "parse_error"

    logger.info(json.dumps(entry))
    return result_str  # Pass through unchanged


# ─── Hook Registry ───────────────────────────────────────────────────

# Pre-tool hooks run in order. If any returns a "blocked" dict, execution stops.
PRE_HOOKS = [
    pre_audit_log,
    pre_pii_blocker,
]

# Post-tool hooks run in order, each receiving the (possibly modified) result from the previous.
POST_HOOKS = [
    post_normalize_financials,
    post_audit_log,
]


def run_pre_hooks(tool_name: str, tool_input: dict) -> dict | None:
    """
    Run all pre-tool hooks. Returns None to allow, or a block dict to halt.
    """
    for hook in PRE_HOOKS:
        result = hook(tool_name, tool_input)
        if result and result.get("blocked"):
            return result
    return None


def run_post_hooks(tool_name: str, tool_input: dict, result_str: str) -> str:
    """
    Run all post-tool hooks. Each hook can transform the result string.
    """
    for hook in POST_HOOKS:
        result_str = hook(tool_name, tool_input, result_str)
    return result_str
