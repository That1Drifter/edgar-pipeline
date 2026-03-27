"""
Cost Tracking — Token Counting and Usage Monitoring

Tracks API token usage across the entire pipeline run and estimates costs.
Uses client.messages.count_tokens() for pre-request estimation.

This implements cert exam concepts:
- D4: Token counting API for cost optimization
- D5: Observable system with usage transparency
"""

import json
from anthropic import Anthropic

# ─── Pricing (per million tokens) ────────────────────────────────────
# Source: Anthropic pricing page. Update as needed.

PRICING = {
    "claude-sonnet-4-20250514": {
        "input": 3.00,        # $3/M input tokens
        "output": 15.00,      # $15/M output tokens
        "cache_write": 3.75,  # $3.75/M (1.25x input)
        "cache_read": 0.30,   # $0.30/M (0.1x input)
        "batch_discount": 0.5,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.00,
        "cache_write": 1.00,
        "cache_read": 0.08,
        "batch_discount": 0.5,
    },
}

# Fallback for unknown models
DEFAULT_PRICING = {
    "input": 3.00,
    "output": 15.00,
    "cache_write": 3.75,
    "cache_read": 0.30,
    "batch_discount": 0.5,
}


class CostTracker:
    """Accumulates token usage across multiple API calls and computes cost."""

    def __init__(self, model: str = "claude-sonnet-4-20250514", is_batch: bool = False):
        self.model = model
        self.is_batch = is_batch
        self.prices = PRICING.get(model, DEFAULT_PRICING)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_tokens = 0
        self.cache_read_tokens = 0

    def track(self, usage) -> None:
        """Record usage from a response.usage object."""
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0)
        self.output_tokens += getattr(usage, "output_tokens", 0)
        self.cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    def merge(self, other: "CostTracker") -> None:
        """Merge another tracker's totals into this one."""
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens

    @property
    def total_cost(self) -> float:
        """Calculate total cost in dollars."""
        p = self.prices
        discount = p["batch_discount"] if self.is_batch else 1.0

        cost = (
            (self.input_tokens * p["input"] / 1_000_000) +
            (self.output_tokens * p["output"] / 1_000_000) +
            (self.cache_creation_tokens * p["cache_write"] / 1_000_000) +
            (self.cache_read_tokens * p["cache_read"] / 1_000_000)
        )
        return cost * discount

    @property
    def cache_savings(self) -> float:
        """Estimate how much caching saved vs. full-price input tokens."""
        if self.cache_read_tokens == 0:
            return 0.0
        p = self.prices
        full_price = self.cache_read_tokens * p["input"] / 1_000_000
        cached_price = self.cache_read_tokens * p["cache_read"] / 1_000_000
        return full_price - cached_price

    def summary(self) -> str:
        """Human-readable cost summary."""
        lines = [
            f"  API calls:      {self.calls}",
            f"  Input tokens:   {self.input_tokens:,}",
            f"  Output tokens:  {self.output_tokens:,}",
        ]
        if self.cache_creation_tokens > 0 or self.cache_read_tokens > 0:
            lines.append(f"  Cache write:    {self.cache_creation_tokens:,}")
            lines.append(f"  Cache read:     {self.cache_read_tokens:,}")
            if self.cache_savings > 0:
                lines.append(f"  Cache savings:  ${self.cache_savings:.4f}")
        if self.is_batch:
            lines.append(f"  Batch discount: 50%")
        lines.append(f"  Total cost:     ${self.total_cost:.4f}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serializable dict for JSON output."""
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_cost_usd": round(self.total_cost, 6),
            "cache_savings_usd": round(self.cache_savings, 6),
            "is_batch": self.is_batch,
        }


def estimate_request_cost(model: str, system: str, tools: list,
                          messages: list) -> dict:
    """
    Pre-request cost estimation using the token counting API.

    Returns a dict with estimated input tokens and cost.
    """
    client = Anthropic()
    try:
        result = client.messages.count_tokens(
            model=model,
            system=system,
            tools=tools,
            messages=messages,
        )
        input_tokens = result.input_tokens
        prices = PRICING.get(model, DEFAULT_PRICING)
        estimated_cost = input_tokens * prices["input"] / 1_000_000
        return {
            "input_tokens": input_tokens,
            "estimated_input_cost": round(estimated_cost, 6),
        }
    except Exception as e:
        return {"error": str(e), "input_tokens": 0, "estimated_input_cost": 0}
