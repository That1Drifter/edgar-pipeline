# CLAUDE.md

## Project

SEC EDGAR financial data extraction pipeline. Multi-agent system that extracts structured data from 10-K/10-Q filings using Claude's tool_use API.

## Architecture

```
run.py                      CLI entry point (single, multi, batch, streaming)
├── agents/
│   ├── extractor.py        Phase 1 single-agent extraction (single company)
│   ├── extractor_stream.py Phase 4 streaming variant (real-time output)
│   ├── coordinator.py      Phase 2 hub-and-spoke coordinator (multi-company)
│   ├── subagents.py        Researcher (4 EDGAR tools) + Analyzer (save_report + thinking)
│   └── batch.py            Phase 3 Message Batches API (bulk processing, 50% cheaper)
├── edgar/
│   └── fetcher.py          SEC EDGAR API client (rate-limited, gzip, XBRL stripping)
├── tools/
│   └── definitions.py      Tool schemas (TOOLS + TOOLS_STRICT), handlers, hooks, caching helpers
├── hooks.py                PreToolCall + PostToolUse hook system
├── review.py               Human review routing (confidence-based flagging)
├── costs.py                Token counting, CostTracker, price constants
└── output/                 Extraction results, audit log, review queue (gitignored)
```

## Key design decisions

- Coordinator gets delegate_research + delegate_analysis only — cannot call EDGAR tools directly
- Researcher subagent gets 4 EDGAR tools (lookup, get_filings, fetch, extract)
- Analyzer subagent gets 1 tool (save_report) with tool_choice: any (or auto when thinking enabled)
- All inter-agent communication routes through coordinator (hub-and-spoke)
- Subagents receive task-specific prompts, not raw user queries
- Validation-retry passes specific errors back (not blind retries)
- Confidence scores are calibrated: 0.9+ clear, 0.7-0.8 interpreted, <0.5 inferred

### Phase 3: Hooks, Batches, Review

- **PreToolCall hooks**: audit logging (output/audit.log), PII blocking (SSN/email/phone)
- **PostToolUse hooks**: financial data normalization (convert to millions, clamp confidence)
- **Batch mode** (`--batch`): Message Batches API, 50% cheaper, single-shot extraction
- **Human review routing**: flags low-confidence extractions → review_queue.json

### Phase 4: Advanced API Features

- **Structured outputs** (`--strict`): `strict: true` on extract_financials with `TOOLS_STRICT` schema (anyOf for nullables, additionalProperties: false)
- **Token counting**: `CostTracker` in costs.py, tracks usage per API call, prints cost summary
- **Prompt caching**: `cache_control: {type: "ephemeral"}` on system prompts and last tool — saves ~90% on repeated input tokens
- **Streaming** (`--stream`): `client.messages.stream()` with real-time text and tool event display
- **Citations**: Filing text passed as document blocks with `citations: {enabled: true}`, `source_section` field on financial metrics
- **Extended thinking** (`--think`): `thinking: {type: "enabled", budget_tokens: 10000}` on analyzer for complex multi-company comparisons (requires tool_choice: auto)

## Running

Must run on WSL Ubuntu (Windows cp1252 encoding breaks Unicode output):
```bash
# Single company
wsl -d Ubuntu -- bash -c "cd /home/that1drifter/edgar-pipeline && /home/that1drifter/edgar-venv/bin/python run.py 'Apple Inc'"

# With strict schemas
wsl -d Ubuntu -- bash -c "cd /home/that1drifter/edgar-pipeline && /home/that1drifter/edgar-venv/bin/python run.py --strict 'Apple Inc'"

# Streaming mode
wsl -d Ubuntu -- bash -c "cd /home/that1drifter/edgar-pipeline && /home/that1drifter/edgar-venv/bin/python run.py --stream 'Apple Inc'"

# Multi-company with extended thinking
wsl -d Ubuntu -- bash -c "cd /home/that1drifter/edgar-pipeline && /home/that1drifter/edgar-venv/bin/python run.py --think 'Apple Inc' 'Tesla Inc'"

# Batch mode (Message Batches API)
wsl -d Ubuntu -- bash -c "cd /home/that1drifter/edgar-pipeline && /home/that1drifter/edgar-venv/bin/python run.py --batch 'Apple Inc' 'Tesla Inc' 'Microsoft Corp'"
```

Sync files from Windows before running:
```bash
wsl -d Ubuntu -- bash -c "cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/agents/*.py /home/that1drifter/edgar-pipeline/agents/ && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/run.py /home/that1drifter/edgar-pipeline/run.py && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/tools/*.py /home/that1drifter/edgar-pipeline/tools/ && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/edgar/*.py /home/that1drifter/edgar-pipeline/edgar/ && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/hooks.py /home/that1drifter/edgar-pipeline/hooks.py && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/review.py /home/that1drifter/edgar-pipeline/review.py && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/costs.py /home/that1drifter/edgar-pipeline/costs.py"
```

## Cost

- ~$0.29/run Sonnet with caching, ~$0.01/run Haiku
- Multi-company: ~$0.12 per company + ~$0.05 for coordinator + analyzer
- Batch mode: ~$0.06 per company (50% Batches API discount, single-turn)
- Caching saves ~$0.05/run on repeated system+tool tokens
- MODEL constant in agents/extractor.py, agents/subagents.py, agents/coordinator.py, agents/batch.py

## Git workflow

- main is protected — PRs only
- Feature branches: feature/<name>, fixes: fix/<name>
- Commits are atomic, descriptive
- PR #1 (Phase 2) merged, PR #2 (Phase 3) open

## Conventions

- Python 3.12+
- Tool schemas follow Claude API tool_use format
- Structured errors always include: error_category, is_retryable, message
- SEC requests rate-limited (0.15s between calls)
- Nullable fields for missing data — never fabricate
- All financial values normalized to millions after extraction (PostToolUse hook)
- Cost summary printed at the end of every run

## Cert Exam Coverage

| Feature | Phase | File |
|---------|-------|------|
| Core agentic loop (stop_reason) | 1 | agents/extractor.py |
| Multi-agent hub-and-spoke | 2 | agents/coordinator.py |
| Subagent context passing, scoped tools | 2 | agents/subagents.py |
| Tool schemas, handlers, validation | 1 | tools/definitions.py |
| tool_choice variants (auto, any) | 2 | agents/subagents.py |
| PreToolCall / PostToolUse hooks | 3 | hooks.py |
| Message Batches API | 3 | agents/batch.py |
| Human review routing | 3 | review.py |
| Structured error propagation | 2 | agents/subagents.py |
| Structured outputs (strict: true) | 4 | tools/definitions.py |
| Token counting / cost tracking | 4 | costs.py |
| Prompt caching (cache_control) | 4 | all agents |
| Streaming (messages.stream) | 4 | agents/extractor_stream.py |
| Citations (document blocks) | 4 | agents/extractor.py, subagents.py |
| Extended thinking | 4 | agents/subagents.py |
