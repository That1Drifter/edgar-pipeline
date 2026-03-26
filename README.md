# SEC EDGAR Extraction Pipeline

Multi-agent system for extracting structured financial data from SEC EDGAR filings using Claude's tool_use API.

## What it does

Give it a company name, it returns structured financial data:

```
$ python run.py "Apple Inc"

  EXTRACTION COMPLETE (6 iterations)
  Company:      Apple Inc.
  Ticker:       AAPL
  Period:       2025-09-27
  Revenue:      $416,161.00 (millions)  ●●●●○  [Total net sales]
  Net Income:   $112,010.00 (millions)  ●●●●○  [Net income]
  Total Assets: $359,241.00 (millions)  ●●●●○
  EPS:          7.46 (diluted)
  Risk Factors: 5 categorized
```

## Architecture

**Phase 1** (current) — Single-agent extraction with validation-retry loop:

```
User → Agent → lookup_company → get_filings → fetch_filing → extract_financials → JSON
                                                                    ↑          |
                                                                    └─ retry ←─┘
                                                                  (validation errors)
```

The agent uses Claude's `tool_use` with JSON schemas for guaranteed schema compliance. A validation layer checks cross-field consistency (assets vs liabilities, confidence calibration, date formats) and feeds specific errors back for self-correction.

**Phase 2** (planned) — Multi-agent coordinator with specialized subagents
**Phase 3** (planned) — Production reliability: hooks, batch processing, human review routing
**Phase 4** (planned) — Observability: audit logging, accuracy tracking, quality dashboard

## Key concepts demonstrated

- **Agentic loop** — `stop_reason` control flow (`tool_use` → continue, `end_turn` → stop)
- **Tool design** — Detailed descriptions for reliable selection, structured error responses with `error_category` and `is_retryable`
- **Structured output** — `tool_use` with JSON schemas, nullable fields to prevent hallucination, extensible enums with "other" + detail
- **Validation-retry** — Specific error feedback drives self-correction (not blind retries)
- **Confidence calibration** — Enforced scoring scale with concrete examples

## Setup

```bash
# Clone
git clone https://github.com/That1Drifter/edgar-pipeline.git
cd edgar-pipeline

# Python environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install anthropic

# API key
cp .env.example .env
# Edit .env with your Anthropic API key

# Run
python run.py "Apple Inc"
python run.py "Tesla Inc" --form 10-Q
python run.py "Microsoft Corp" --quiet
```

## Cost

~$0.08-0.12 per extraction on Sonnet. Switch to Haiku (~$0.01) for development testing.

## License

MIT
