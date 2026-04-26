# Technology Stack

## Architecture

A collection of thin wrappers that live in a single directory. Each script is an independent entry point: it calls the OpenBB Platform Python SDK (`obb`) and writes the result as JSON to stdout. No persistent state; where the output is stored is the caller's decision.

## Core Technologies

- **Language**: Python 3.12 (pinned to `>=3.12,<3.13`)
- **Package manager / runner**: `uv` (`uv sync`, `uv run`). `pyproject.toml` sets `[tool.uv] package = false`.
- **Data SDK**: `openbb>=4.4.0` (Platform) plus `openbb-cli>=1.1.0` (interactive REPL)
- **Env loader**: `python-dotenv>=1.0.0`

## Key Libraries / External Services

- **OpenBB providers** (bundled): yfinance (default, no key), finviz, nasdaq, sec (EDGAR), fred, oecd, federal_reserve, cboe, finra, famafrench, eia, fmp
- **Provider selection principle**: default to a key-free provider whenever one exists, and gate paid-only sub-modes behind an explicit `--provider` (or document the gate in `--help` and `README.md` §1-1). The free defaults span yfinance (most equity sub-modes), finviz (price-target revisions and sector_score), nasdaq (calendars), fred / federal_reserve / oecd (macro_survey), finra (short interest), famafrench (factor returns), and eia (weekly petroleum, keyless in practice). FMP is the **default only when no key-free upstream covers the data** — currently `institutional` (13F), `news --scope world`, and `fundamentals --type ratios`; `etf --type holdings|sectors` accept FMP only on paid Starter+ tiers and surface `error_category: plan_insufficient` on the free key. SEC sub-modes (`insider`, `filings`, `shorts --type fails_to_deliver`) require `SEC_USER_AGENT`; FRED-backed surveys and `commodity --type price` require `FRED_API_KEY`; only `commodity --type steo` consults `EIA_API_KEY`.
- **Out of scope**: raw FRED single series → `shared/fred-api-ts` (structured FRED surveys stay here). Multi-bagger style screening → `shared/multibagger-alchemy`. Do not pull those responsibilities into this tool.

## Development Standards

### JSON output contract

- Wrappers **must keep stdout pure JSON**. Always guard OpenBB calls with `scripts/_common.py::silence_stdout()` to absorb printf-style warnings from OpenBB and yfinance (`safe_call` already does this).
- **Every wrapper emits the shared envelope** — root keys are exactly `{source, collected_at, tool, data}` plus optional `{warnings, error, error_category, details}`. Multi-symbol wrappers route through `_common.aggregate_emit`; single-query wrappers through `_common.single_emit`. Downstream parsers must traverse `payload["data"]["results"]` (or equivalent `data.*` keys) rather than treating the stdout as a bare list; per-wrapper meta (provider, series, universe, …) lives under `data/`, never at the envelope root.
- **Per-row failure shape is closed**: `{ok: false, error, error_type, error_category}` with `error_category ∈ {credential, plan_insufficient, transient, validation, other}` (drawn from `_common.ErrorCategory`). Partial failures aggregate to top-level `warnings[]`; never inline them into `data` and never silently drop them.
- **Exit-code contract**: exit 0 covers full success and partial failure; exit 2 is reserved for (a) every input failed on `credential` or `plan_insufficient`, and (b) argparse / input-validation rejection. On the fatal path the envelope omits `data` and carries top-level `error` / `error_category`. Tracebacks to stderr are fine; stdout must stay clean.
- The cross-wrapper envelope contract is mechanically enforced by `tests/integration/test_json_contract.py`, which auto-discovers wrappers from `scripts/*.py` — adding a wrapper means it is held to the contract on the next run.

### Credential wiring

- The `.env` file lives at `.env` (git-ignored). **Do not `export` credentials directly.**
- When adding a provider, update **both** `.env.example` and `scripts/_env.py::_CREDENTIAL_MAP` in the same change. Confirm the attribute name with `obb.user.credentials.__dict__`.
- `SEC_USER_AGENT` is read as an environment variable by OpenBB itself, not as a credentials attribute. Do **not** add it to `_CREDENTIAL_MAP`.

### Testing / Lint

Tests live under `tests/` and are split by pytest markers into `unit` (pure helpers, aggregation logic, and sanity-assertion helpers — offline, no credentials) and `integration` (each wrapper as a CLI subprocess against its default free-tier provider — yfinance / finviz / nasdaq / oecd / federal*reserve, plus FRED when keyed — asserting the shared `wrap()` envelope, exit codes, and per-payload sanity via `tests/integration/_sanity.py`). Sub-modes that require `FMP_API_KEY` are auto-skipped when the key is unset so the `-m integration` run stays green on the free default. Pytest is declared in an opt-in dependency group, so install it with `uv sync --group dev` and invoke via `uv run pytest -m unit` or `uv run pytest -m integration`. The OpenBB-call layer of each wrapper is deliberately integration-only — mocked OpenBB-provider tests add little signal, so unit coverage stays on deterministic, side-effect-free code. The README's §1-1 `Verified` column points back to the integration test that supplies the evidence for each wrapper × sub-mode, and `tests/integration/test_verification_gate.py` mechanically enforces (a) every `tests/...::test*_`marker in the README resolves to a function that exists on disk, and (b) the set of scripts documented in §1-1 equals the set of`scripts/_.py` files on disk.

- A `.ruff_cache/` directory exists, but `pyproject.toml` carries no ruff configuration — default settings only. Existing code uniformly uses `from __future__ import annotations` plus PEP 604 type syntax under Python 3.12.

## Development Environment

### Required tools

- `uv` is required (it also manages the Python venv).
- `ANTHROPIC_API_KEY` and similar agent secrets are **not** needed here — they live with the caller.

### Common commands

```bash
# Sync dependencies
uv sync

# Run a wrapper
uv run scripts/quote.py AAPL MSFT --provider yfinance
uv run scripts/sector_score.py --universe sector-spdr

# Explore un-wrapped endpoints
uv run openbb                           # interactive CLI
uv run python -c "from openbb import obb; print(dir(obb.equity))"
```

## Key Technical Decisions

- **No stdlib shadowing**: never name a file in `scripts/` the same as a Python stdlib module (e.g. `calendar.py`). Because `scripts/` sits on `sys.path`, such a name will break OpenBB's own imports. Use a qualifier or plural form instead (e.g. `calendars.py`).
- **OpenBB is AGPL-3.0**: fine for local, personal-use agents. Exposing this tool as an external SaaS requires either open-sourcing the caller or buying a commercial license.

---

_For live provider pricing and rate limits, see `README.md` §5._
