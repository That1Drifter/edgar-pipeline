# CLAUDE.md

## Project

SEC EDGAR financial data extraction pipeline. Multi-agent system that extracts structured data from 10-K/10-Q filings using Claude's tool_use API.

## Architecture

```
run.py                      CLI entry point (single or multi-company)
├── agents/
│   ├── extractor.py        Phase 1 single-agent extraction (still used for single company)
│   ├── coordinator.py      Phase 2 hub-and-spoke coordinator (multi-company)
│   └── subagents.py        Researcher (4 EDGAR tools) + Analyzer (1 save_report tool)
├── edgar/
│   └── fetcher.py          SEC EDGAR API client (rate-limited, gzip, XBRL stripping)
├── tools/
│   └── definitions.py      Tool schemas (JSON), handlers, validation logic
└── output/                 Extraction results (gitignored)
```

## Key design decisions

- Coordinator gets delegate_research + delegate_analysis only — cannot call EDGAR tools directly
- Researcher subagent gets 4 EDGAR tools (lookup, get_filings, fetch, extract)
- Analyzer subagent gets 1 tool (save_report) with tool_choice: any to force structured output
- All inter-agent communication routes through coordinator (hub-and-spoke)
- Subagents receive task-specific prompts, not raw user queries
- Validation-retry passes specific errors back (not blind retries)
- Confidence scores are calibrated: 0.9+ clear, 0.7-0.8 interpreted, <0.5 inferred

## Running

Must run on WSL Ubuntu (Windows cp1252 encoding breaks Unicode output):
```bash
wsl -d Ubuntu -- bash -c "cd /home/that1drifter/edgar-pipeline && /home/that1drifter/edgar-venv/bin/python run.py 'Apple Inc'"
wsl -d Ubuntu -- bash -c "cd /home/that1drifter/edgar-pipeline && /home/that1drifter/edgar-venv/bin/python run.py 'Apple Inc' 'Tesla Inc'"
```

Sync files from Windows before running:
```bash
wsl -d Ubuntu -- bash -c "cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/agents/*.py /home/that1drifter/edgar-pipeline/agents/ && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/run.py /home/that1drifter/edgar-pipeline/run.py && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/tools/*.py /home/that1drifter/edgar-pipeline/tools/ && cp /mnt/c/Users/Drifter/Desktop/edgar-pipeline/edgar/*.py /home/that1drifter/edgar-pipeline/edgar/"
```

## Cost

- ~$0.12/run Sonnet, ~$0.01/run Haiku
- Multi-company: ~$0.12 per company + ~$0.05 for coordinator + analyzer
- MODEL constant in agents/extractor.py, agents/subagents.py, agents/coordinator.py

## Git workflow

- main is protected — PRs only
- Feature branches: feature/<name>, fixes: fix/<name>
- Commits are atomic, descriptive
- PR #1 (Phase 2) is ready to merge

## Conventions

- Python 3.12+
- Tool schemas follow Claude API tool_use format
- Structured errors always include: error_category, is_retryable, message
- SEC requests rate-limited (0.15s between calls)
- Nullable fields for missing data — never fabricate
