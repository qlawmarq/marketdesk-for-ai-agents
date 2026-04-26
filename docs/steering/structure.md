# Project Structure

## Organization Philosophy

**A flat directory of wrappers.** One file per OpenBB capability under `scripts/`, with shared concerns extracted into underscore-prefixed helper modules. No subdirectory hierarchy, no class tree, no plugin system — the thinner the wrapper, the more valuable it is.

## Directory Patterns

### Wrapper scripts

**Location**: `scripts/*.py` (excluding files that start with `_`)
**Purpose**: one OpenBB capability per CLI entry. Parses arguments with `argparse`, prints JSON to stdout.
**Example**: `scripts/quote.py`, `scripts/historical.py`, `scripts/fundamentals.py`, `scripts/sector_score.py`

### Shared helpers

**Location**: `scripts/_*.py`
**Purpose**: side-effect-free utilities shared across wrappers. Three files today: `_env.py` (credential wiring), `_common.py` (JSON / stdout hygiene, safe OpenBB calls, the shared envelope, the `ErrorCategory` enum, and the `aggregate_emit` / `single_emit` entry points every wrapper passes through), and `_schema.py` (single source of truth for ratio-output unit tagging — pure data + pure functions, stdlib only). Anything wider than one wrapper belongs in a helper, not duplicated across scripts.
**Example**: `from _env import apply_to_openbb`, `from _common import safe_call, aggregate_emit, single_emit, silence_stdout`, `from _schema import DECIMAL_RATIO_FIELDS`

The same underscore-prefix convention is mirrored under `tests/integration/`: `_sanity.py` holds bounded-range / ordered-date / multi-symbol assertion helpers shared across the per-wrapper integration suites, and `_verification_gate.py` exposes the repo-state introspection functions that the verification gate exercises (kept stdlib-only and `repo_root`-parameterized so the offline self-test under `tests/unit/` can drive each failure mode against a temp-dir fake repo).

### Agent Skills

**Location**: `skills/`
**Purpose**: host-agnostic Markdown manuals that tell an AI agent how to invoke wrappers correctly. One folder per ✅ wrapper in `README.md` §1-1, plus a small set of underscore-prefixed cross-cutting policy skills (`_envelope`, `_errors`, `_providers`) and pure-pipeline composite skills (`single-stock-snapshot`, `sector-rotation-snapshot`, `macro-snapshot`). `INDEX.md` is the catalog and is kept in lock-step with the per-wrapper folders by the ChangeBundleRule — adding `scripts/<name>.py` requires a matching `skills/<name>/SKILL.md` in the same change. `AUTHORING.md` and `SMOKE_CHECKLIST.md` capture authoring rules and the manual smoke procedure.

**Conventions** (these are stricter than the rest of the repo and are deliberate):

- **English only.** Every file under `skills/` is English-fixed, overriding the project-wide Japanese-output default.
- **Minimal-sufficient.** Each `SKILL.md` targets ~30–80 lines with at most one short output sample; do not pad with extra examples or repeat envelope / error prose that lives in the `_envelope` / `_errors` / `_providers` policy skills.
- **Documentation, not tests.** `SKILL.md` content is written from observed live-run output, not from test fixtures, and there is no test harness for the `skills/` tree.

### SDD settings

**Location**: `docs/steering/`, `docs/tasks/`, `docs/settings/`
**Purpose**: SDD workflow memory and specs. `docs/settings/` contains AI-DLC templates and rules — it is metadata, do not document it inside steering files.

## File & Wrapper Conventions

- **File names**: snake_case noun or noun phrase. Use a plural form when the script bundles multiple capabilities (`calendars.py`, `fundamentals.py`).
- **Avoid stdlib names**: `calendar.py` / `json.py` / `typing.py` and similar are forbidden. Because `scripts/` is on `sys.path`, such a name will shadow the stdlib and break OpenBB's internal imports.
- **Entry-point boilerplate**: start every wrapper with
  ```python
  from _common import silence_stdout   # when needed
  from _env import apply_to_openbb
  from openbb import obb
  apply_to_openbb()
  ```
  then build the CLI with `argparse` and launch via `raise SystemExit(main())`, where `main() -> int`.
- **Multi-symbol input**: accept `nargs="+"` whenever it is natural, fetch sequentially, and return a list (see `quote.py` and `estimates.py`).
- **Provider argument**: expose `--provider` with a closed `choices=[...]`. Default to a key-free provider (yfinance for most equity sub-modes, finviz for the sector-ranking path, nasdaq for calendars, federal_reserve / oecd for the FOMC / CLI macro surveys). Paid providers (fmp, intrinio-alike) may appear in `choices` only when they serve as an opt-in fall-back — never as the silent default. When no free alternative exists (e.g. `fundamentals --type ratios`), document the FMP-gated status in `--help` and in `README.md` §1-1 rather than smuggling fmp in as a hidden default.

## Import Organization

```python
from __future__ import annotations       # put this at the top of every file

import argparse                           # stdlib
import json
import sys
from typing import Any

from _common import silence_stdout        # local helpers (underscore-prefixed)
from _env import apply_to_openbb
from openbb import obb                    # third-party
```

- `scripts/` is its own `sys.path` root, so helper imports are **flat, not relative** (`from _env import ...`).
- Import `obb` freely, but only **call** it after `apply_to_openbb()` has run — that is when `obb.user.credentials` is populated.

## Code Organization Principles

- **Keep wrappers thin**: no business logic. Aggregation such as composite scoring is confined to a dedicated script (e.g. `sector_score.py`) rather than leaking into general-purpose wrappers.
- **The caller owns persistence**: wrappers only emit JSON to stdout. The destination path and filename are the caller's decision.
- **Respect the scope boundary**: this tool covers "equities, fundamentals, structured macro surveys." Raw FRED series belong to `shared/fred-api-ts`; multi-bagger screening belongs to `shared/multibagger-alchemy`. If a new feature feels like it might cross that line, re-read the scope declaration in `README.md` §1 first.

---

_The live feature list, TODOs, and provider details live in `README.md` — they change too often to duplicate here._
