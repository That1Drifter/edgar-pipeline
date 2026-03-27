"""
Human Review Routing — Flag Low-Confidence Extractions

Examines extraction results and routes items to a review queue when:
- Any financial field has confidence < 0.5
- Validation errors were present (partial extractions)
- Conflicts were detected in the filing
- Extraction completely failed

The review queue is a JSON file that a human reviewer can process.
Items include the extraction data, the reason for flagging, and
the source filing URL for manual verification.

This implements cert exam concepts:
- D3 Task 3.3: Human-in-the-loop routing based on confidence thresholds
- D5 Task 5.1: Graceful degradation with review escalation
"""

import json
import os
from datetime import datetime, timezone


# Confidence threshold — below this, flag for human review
REVIEW_THRESHOLD = 0.5

# Fields to check for low confidence
CONFIDENCE_FIELDS = ("revenue", "net_income", "total_assets", "total_liabilities", "eps")


def check_needs_review(result: dict) -> list[str]:
    """
    Check if an extraction result needs human review.

    Returns a list of reasons (empty = no review needed).
    """
    reasons = []

    status = result.get("status", "")

    # Failed extractions always need review
    if status == "failed":
        reasons.append(f"Extraction failed: {result.get('error', 'unknown')}")
        return reasons

    # Partial extractions (validation errors)
    if status == "partial":
        errors = result.get("validation_errors", [])
        for err in errors:
            reasons.append(f"Validation error: {err}")

    data = result.get("data", {})
    if not data:
        reasons.append("No extraction data present")
        return reasons

    # Check each financial field's confidence
    for field_name in CONFIDENCE_FIELDS:
        field = data.get(field_name, {})
        confidence = field.get("confidence")
        value = field.get("value")

        if confidence is not None and confidence < REVIEW_THRESHOLD:
            reasons.append(
                f"Low confidence on {field_name}: {confidence:.2f} "
                f"(value: {value}, threshold: {REVIEW_THRESHOLD})"
            )

        # Null value with no confidence = missing data
        if value is None and confidence is None:
            reasons.append(f"Missing data: {field_name} has no value or confidence")

    # Conflict detection
    if data.get("conflict_detected"):
        reasons.append(f"Conflict detected: {data.get('notes', 'see extraction')}")

    return reasons


def build_review_queue(results: list[dict]) -> dict:
    """
    Process a list of extraction results and build a review queue.

    Returns a dict with:
    - items: list of items needing review (with reasons)
    - summary: counts and statistics
    - passed: list of items that passed review checks
    """
    review_items = []
    passed_items = []

    for result in results:
        company = result.get("company", "?")
        reasons = check_needs_review(result)

        if reasons:
            review_items.append({
                "company": company,
                "status": result.get("status", "?"),
                "reasons": reasons,
                "priority": _calculate_priority(reasons),
                "data": result.get("data"),
                "flagged_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            passed_items.append({
                "company": company,
                "status": result.get("status", "?"),
            })

    return {
        "review_queue": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_processed": len(results),
            "needs_review": len(review_items),
            "passed": len(passed_items),
            "items": sorted(review_items, key=lambda x: x["priority"], reverse=True),
        },
        "passed": passed_items,
    }


def _calculate_priority(reasons: list[str]) -> int:
    """
    Calculate review priority (higher = more urgent).

    - Failed extraction: 10
    - Conflict detected: 8
    - Validation error: 6
    - Low confidence: 3 per field
    - Missing data: 2 per field
    """
    priority = 0
    for reason in reasons:
        if "failed" in reason.lower():
            priority += 10
        elif "conflict" in reason.lower():
            priority += 8
        elif "validation" in reason.lower():
            priority += 6
        elif "low confidence" in reason.lower():
            priority += 3
        elif "missing" in reason.lower():
            priority += 2
    return priority


def save_review_queue(queue: dict, output_dir: str = "output") -> str:
    """Save the review queue to a JSON file. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "review_queue.json")
    with open(path, "w") as f:
        json.dump(queue, f, indent=2)
    return path


def print_review_summary(queue: dict):
    """Print a human-readable review summary."""
    rq = queue["review_queue"]
    print(f"\n  Review Queue Summary")
    print(f"  {'─' * 40}")
    print(f"  Total processed:  {rq['total_processed']}")
    print(f"  Passed:           {rq['passed']}")
    print(f"  Needs review:     {rq['needs_review']}")

    if rq["items"]:
        print(f"\n  Items requiring review:")
        for item in rq["items"]:
            company = item["company"]
            priority = item["priority"]
            reason_count = len(item["reasons"])
            top_reason = item["reasons"][0][:60] if item["reasons"] else "?"
            print(f"    [{priority:>2}] {company}: {top_reason}")
            if reason_count > 1:
                print(f"         +{reason_count - 1} more reason(s)")
