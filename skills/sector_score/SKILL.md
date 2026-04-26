---
name: sector_score
description: >-
  Score and rank a sector / theme / factor ETF universe (or an arbitrary
  ticker list) on a composite momentum + risk-adjusted signal. Use when an
  agent needs ranked sector or factor candidates for rotation analysis.
covers_scripts:
  - scripts/sector_score.py
---

## When to Use

- Ranking the eleven SPDR sector ETFs, the global factor sleeve, or a JP TOPIX sector basket by composite momentum.
- Producing input ETF tickers for a downstream `../momentum/SKILL.md` deep-dive.

Not for: single-symbol momentum (use `../momentum/SKILL.md`), raw price history (use `../historical/SKILL.md`).

## Inputs

| Argument | Default | Required keys | Notes |
|---|---|---|---|
| `--universe sector-spdr` | — | none | XLK / XLF / XLE / XLV / XLI / XLP / XLY / XLU / XLB / XLRE / XLC. |
| `--universe theme-ark` | — | none | ARKK / ARKW / ARKG / SOXX / SMH / KWEB etc. |
| `--universe global-factor` | — | none | QUAL / MTUM / USMV / VLUE / SIZE / HDV / DGRO / SPHD. |
| `--universe jp-sector` | — | none | TOPIX-17 sector ETFs (`1615.T` … `1629.T`). |
| `--tickers <CSV>` | — | none | Mutually exclusive with `--universe`. Free-form ticker list. |
| `--benchmark <symbol>` | `SPY` | none | Informational; recorded in `data.benchmark`. |
| `--weight-*` | see script | none | Composite weights (clenow_90/180, return_3m/6m/12m, risk_adj). Do not restate; see `scripts/sector_score.py`. |

Provider mix is finviz (price-performance) + yfinance (clenow), both keyless. See `../_providers/SKILL.md`.

## Command

```bash
uv run scripts/sector_score.py --universe sector-spdr
uv run scripts/sector_score.py --tickers XLK,XLF,XLE
```

## Output

[shared envelope](../_envelope/SKILL.md). `tool = "sector_score"`. Single emit — `data.results[]` is one row per scored ticker, sorted ascending by `rank`.

`data` namespace:

- `data.universe` — universe key passed in (or `null` for `--tickers`).
- `data.tickers` — resolved ticker list.
- `data.benchmark` — benchmark symbol used for relative strength.
- `data.weights` — weight map applied to the composite.
- `data.missing_tickers` — tickers dropped before scoring (no price history etc.).
- `data.notes` — short free-form notes from the wrapper (e.g. provider quirks).
- `data.results[]` — `{ticker, symbol, rank, composite_score_0_100, composite_z, signals{return_3m, return_6m, return_12m, clenow_90, clenow_180, risk_adj_1m}, z_scores{<same fields>}, ok}`.

```json
{
  "tool": "sector_score",
  "data": {
    "universe": "sector-spdr",
    "results": [
      {"ticker": "XLE", "rank": 1, "composite_score_0_100": 100.0, "composite_z": 2.14,
       "signals": {"return_3m": 0.156, "clenow_90": 1.244, "risk_adj_1m": -2.6},
       "z_scores": {"return_3m": 1.78, "clenow_90": 2.88}, "symbol": "XLE", "ok": true}
    ]
  }
}
```

## Failure Handling

See [error categories](../_errors/SKILL.md). Wrapper-specific paths:

- A ticker with insufficient price history is dropped into `data.missing_tickers` with no per-row error — the run still exits 0.
- Finviz rate limit on the price-performance fetch → `error_category: transient`; retry once with backoff.

## References

- `scripts/sector_score.py`
- `README.md` § 1 row 7 (Sector ETF composite score).
- `../_envelope/SKILL.md`, `../_errors/SKILL.md`, `../_providers/SKILL.md`.
