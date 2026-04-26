# Product Overview

MarketDesk for AI Agents (`marketdesk-for-ai-agents`) is a local CLI wrapper around OpenBB Platform, shared by the AI investment research agents. It exposes a thin, uniform interface that takes a ticker or series name and emits JSON to stdout — positioned as a **general-purpose market-data desk that AI agents can sit at**.

## Core Capabilities

Capability buckets, not a file list. The live README §1-1 matrix is the catalog and is machine-verified against `scripts/*.py`.

- **Per-ticker disclosure**: quotes, historicals, fundamentals, 3-statement financials, ratios, analyst estimates, news, options chain / IV, SEC filings, insider trading (Form 4), institutional 13F holdings, short interest / fails-to-deliver
- **Composite signals**: sector ETF composite scoring (momentum / Clenow / risk-adjusted z-score blend), cross-sectional momentum & technicals (Clenow, RSI, MACD, vol cones, ADX), Fama-French factor returns
- **Macro & energy**: structured FRED-backed survey snapshots (SLOOS, NY / Texas Fed, Michigan, OECD CLI, FOMC documents) plus commodity prices and EIA weekly petroleum / STEO
- **ETF and calendars**: ETF info / holdings / sector breakdown, plus earnings, dividend, and economic calendars

## Target Use Cases

- **Scheduled collection**: the collector agent runs `uv run scripts/*.py ... > shared/deliverables/.../x.json` asynchronously to produce artifacts
- **Judgment input**: the analyst agent invokes the CLI directly to pull supporting data during macro regime calls and name-level evaluation
- **Exploration / debugging**: a human uses `uv run openbb` (interactive REPL) or a Python one-liner to probe endpoints that do not yet have a wrapper

## Value Proposition

- **Deliberately narrow scope**: this tool owns "equities, fundamentals, structured macro surveys." Raw FRED series belong to `shared/fred-api-ts`; multi-bagger screening belongs to `shared/multibagger-alchemy`. The boundary is a feature, not an oversight.
- **Agent-friendly JSON contract**: every wrapper wraps its OpenBB call in `silence_stdout`, so stdout is always parseable JSON. Errors are also returned as JSON with a non-zero exit.
- **Low friction to extend**: adding a wrapper means dropping one file into `scripts/` — auth wiring and common helpers are already there. Adding a provider means two touches: `.env.example` plus `_CREDENTIAL_MAP` in `_env.py`.
- **Agent-facing skill manuals**: every ✅ wrapper has a host-agnostic `skills/<name>/SKILL.md` that documents how an AI agent should invoke it. `skills/INDEX.md` is the catalog and is kept in lock-step with `scripts/*.py` — see `structure.md` for the conventions.

---

_See `README.md` for the live feature matrix, TODOs, and provider pricing — those change frequently and do not belong in steering._
