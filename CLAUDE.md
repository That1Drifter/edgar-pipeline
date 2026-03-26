# CLAUDE.md

## Project

SEC EDGAR financial data extraction pipeline. Multi-agent system that extracts structured data from 10-K/10-Q filings using Claude's tool_use API.

## Architecture

- `run.py` — CLI entry point
- `edgar/fetcher.py` — SEC EDGAR API client (rate-limited, gzip, XBRL stripping)
- `tools/definitions.py` — Tool schemas (JSON), handlers, validation logic
- `agents/extractor.py` — Core agentic loop (stop_reason control, retry-with-feedback)
- `output/` — Extraction results (gitignored)

## Conventions

- Python 3.12+, no type: ignore comments
- Tool schemas follow Claude API tool_use format exactly
- Structured error responses always include: error_category, is_retryable, human-readable message
- All SEC requests use rate limiting (0.15s minimum between calls)
- Financial values use nullable fields — never fabricate missing data

## Git Workflow

- `main` branch is protected — all changes via PR
- Feature branches: `feature/<name>`, bug fixes: `fix/<name>`
- Commits are atomic and descriptive
- PRs get Claude Code review before merge

## Testing

- Run against known filings: `python run.py "Apple Inc"` (FY2025 10-K, ~$0.08)
- Validate extraction accuracy against known values (Apple FY2025: revenue $416.2B, net income $112.0B)
- Use Haiku model for testing plumbing: change MODEL in agents/extractor.py
