# SEC EDGAR Extraction Pipeline

Multi-agent system for extracting structured financial data from SEC EDGAR 10-K/10-Q filings using the Claude API. Built as a portfolio project demonstrating agentic patterns, tool use, streaming, prompt caching, batch processing, and more.

## What it does

Give it a company name, get back structured financial data with confidence scores, source citations, and validation:

```
$ python run.py "Apple Inc"

  EXTRACTION COMPLETE (7 iterations)
  Company:      Apple Inc.
  Ticker:       AAPL
  Period:       2025-09-27
  Fiscal Year:  2025
  ────────────────────────────────────────────────────────
  Revenue:        $416,161.00 (millions)  ●●●●○  [Total net sales]
  Net Income:     $112,010.00 (millions)  ●●●●○  [Net income]
  Total Assets:   $359,241.00 (millions)  ●●●●○
  Total Liab:     $285,508.00 (millions)  ●●●●○
  EPS:            7.46 (diluted)
  ────────────────────────────────────────────────────────
  Risk Factors (5): competitive, market, operational, financial, legal
  ────────────────────────────────────────────────────────
  API calls:      7
  Input tokens:   81,156
  Cache read:     17,784
  Cache savings:  $0.0480
  Total cost:     $0.2887
```

Compare multiple companies side-by-side, or batch-process 10+ companies at 50% cost savings:

```
$ python run.py "Apple Inc" "Tesla Inc"              # coordinator + subagents
$ python run.py --batch "Apple Inc" "Tesla Inc" ...  # Message Batches API
```

## Architecture

```
run.py                      CLI entry point
├── agents/
│   ├── extractor.py        Single-agent extraction with validation-retry
│   ├── extractor_stream.py Streaming variant (real-time output)
│   ├── coordinator.py      Hub-and-spoke multi-agent coordinator
│   ├── subagents.py        Researcher + Analyzer subagents
│   └── batch.py            Message Batches API bulk processing
├── edgar/
│   └── fetcher.py          SEC EDGAR API client (rate-limited, gzip)
├── tools/
│   └── definitions.py      Tool schemas (standard + strict), handlers, caching
├── hooks.py                Pre/post tool call hooks (audit, PII, normalization)
├── costs.py                Token counting and cost tracking
├── review.py               Human review routing for low-confidence extractions
└── output/                 Results, audit log, review queue
```

### How extraction works

```
User ──→ Agent ──→ lookup_company ──→ get_filings ──→ fetch_filing ──→ extract_financials ──→ JSON
                                                           │                  ↑          │
                                                     [document block    validation    retry
                                                      with citations]    errors ←─────┘
```

1. The agent resolves a company name to a CIK via the SEC tickers index
2. Fetches the most recent filing of the requested type (10-K or 10-Q)
3. The filing text is passed as a **document block with citations enabled** so Claude can ground extracted values to specific sections
4. `extract_financials` captures structured data with confidence scores; a validation layer checks cross-field consistency and feeds specific errors back for self-correction

### Multi-company mode

The **coordinator** decomposes multi-company requests into parallel researcher subagent tasks, then routes all results to an **analyzer** subagent for comparison:

```
Coordinator
├── delegate_research("Apple Inc")  ──→ Researcher (4 EDGAR tools) ──→ extraction
├── delegate_research("Tesla Inc")  ──→ Researcher (4 EDGAR tools) ──→ extraction
└── delegate_analysis(all findings) ──→ Analyzer (save_report tool) ──→ comparison report
```

The coordinator cannot call EDGAR tools directly (hub-and-spoke pattern). Each subagent gets an isolated context with scoped tools and a task-specific prompt.

## Features

| Feature | Description | Flag |
|---------|-------------|------|
| **Agentic loop** | `stop_reason` control flow with validation-retry | default |
| **Multi-agent** | Hub-and-spoke coordinator with researcher + analyzer subagents | 2+ companies |
| **Batch processing** | Message Batches API for bulk extraction at 50% cost | `--batch` |
| **Streaming** | Real-time text and tool event display | `--stream` |
| **Strict schemas** | API-enforced output via `strict: true` on tool definitions | `--strict` |
| **Extended thinking** | 10K token thinking budget on analyzer for complex comparisons | `--think` |
| **Prompt caching** | Cached system prompts and tools across multi-turn conversations | automatic |
| **Citations** | Filing text as document blocks with source attribution | automatic |
| **Cost tracking** | Per-call token counting with cost summary | automatic |
| **Hooks** | Pre-call audit logging + PII blocking; post-call normalization | automatic |
| **Human review** | Flags extractions with confidence < 0.5 or validation errors | automatic |

## Setup

```bash
git clone https://github.com/That1Drifter/edgar-pipeline.git
cd edgar-pipeline

python -m venv venv
source venv/bin/activate
pip install anthropic

echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

python run.py "Apple Inc"
```

### Usage

```bash
python run.py "Apple Inc"                              # single extraction
python run.py "Apple Inc" --form 10-Q                  # quarterly filing
python run.py "Apple Inc" "Tesla Inc"                   # multi-company comparison
python run.py --batch "Apple Inc" "Tesla Inc" "NVIDIA"  # batch mode (50% cheaper)
python run.py --stream "Apple Inc"                      # streaming output
python run.py --strict "Apple Inc"                      # strict schema enforcement
python run.py --think "Apple Inc" "Tesla Inc"           # extended thinking on analysis
python run.py --quiet "Apple Inc"                       # suppress iteration output
```

Flags can be combined: `--strict --stream`, `--strict --think`, etc.

## Cost

| Mode | Cost per company | Notes |
|------|-----------------|-------|
| Single (Sonnet) | ~$0.29 | With prompt caching |
| Multi-company | ~$0.12/company + $0.05 coordinator | Caching reduces repeat costs |
| Batch | ~$0.06/company | 50% API discount, single-turn |
| Haiku (dev) | ~$0.01 | Change MODEL constant in agent files |

## Output

Extractions are saved to `output/` as JSON with full metadata:

- `output/{company}.json` — Structured financial data with confidence scores
- `output/audit.log` — Structured JSON audit trail of all tool calls
- `output/review_queue.json` — Items flagged for human review (batch mode)
- `output/{company}_comparison.json` — Multi-company analysis reports

## API Patterns Demonstrated

This project demonstrates the following Claude API capabilities:

- **Core agentic loop** — `stop_reason` branching, multi-turn tool use conversations
- **Tool use** — JSON schemas, `tool_choice` variants (auto/any), scoped tool sets per agent
- **Structured outputs** — `strict: true` with `additionalProperties: false` and `anyOf` nullables
- **Multi-agent orchestration** — Hub-and-spoke coordinator, parallel subagent spawning, explicit context passing
- **Message Batches API** — Async bulk processing with polling and result collection
- **Streaming** — `client.messages.stream()` with event-based text and tool display
- **Prompt caching** — `cache_control: {type: "ephemeral"}` on system prompts and tool definitions
- **Citations** — Document content blocks with `citations: {enabled: true}`
- **Extended thinking** — `thinking: {type: "enabled", budget_tokens: 10000}` for complex analysis
- **Token counting** — `client.messages.count_tokens()` for pre-request cost estimation
- **PreToolCall hooks** — Audit logging, PII detection and blocking
- **PostToolUse hooks** — Data normalization, confidence clamping
- **Human-in-the-loop** — Confidence-based review routing with priority scoring
- **Structured errors** — `error_category`, `is_retryable`, actionable messages for self-correction
- **Validation-retry** — Specific error feedback (not blind retries) drives model self-correction

## License

MIT
