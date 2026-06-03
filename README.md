# retail_trades

**Cross-Asset Signal Options Scalper** — a research-first trading system around one idea:

> When a basket of liquid tech proxies is broadly **red**, buy short-dated **puts**
> on a target name, expecting a fast downside continuation; cut quickly (a ~10-minute
> time-stop) if the move doesn't materialize.

Broker: **Charles Schwab** retail Trader API via the
[`schwab-py`](https://schwab-py.readthedocs.io/) wrapper (OAuth, REST, streaming).

> ⚠️ This repository implements **Phase 0 (setup & data gate)** and
> **Phase 1 (signal validation)** only. Phase 1 is a **GO/NO-GO gate**. Live
> monitoring and order execution are intentionally **not** built yet — do not
> automate execution until the signal is validated and the gate is passed.
> See [`PLAN.md`](PLAN.md) for the full roadmap.

## Why Python

Hold time is ~10 minutes, so the bottleneck is the Schwab API round-trip, not
language speed. The heavy lifting (backtest, statistics, expectancy) is
pandas/numpy/scipy work, and a mature Schwab wrapper (`schwab-py`) handles OAuth,
REST, and streaming. C++ would only pay off for sub-millisecond execution, which
this strategy does not require.

## Repository layout

```
scalper/
  config.py            # symbols, signal params, risk limits (NO secrets)
  .env.example         # template for credentials (copy to scalper/.env)
  run_validation.py    # Phase 0+1 runner: auth -> data -> validation report
  auth/
    client.py          # authenticated schwab-py client w/ token refresh
  data/
    history.py         # intraday bars + quote-freshness (real-time) check
    store.py           # optional local sqlite cache of bars
  signal/
    definition.py      # the signal as a PURE function of aligned bars
  backtest/
    engine.py          # forward returns at every signal fire
    stats.py           # hit rate, mean/median, baseline, significance
    report.py          # printable per-target validation report
  tests/               # pytest suite (synthetic data, no network/auth)
```

## Setup (Phase 0)

1. Create a developer app at <https://developer.schwab.com>, link your
   brokerage account, and note your **app key**, **app secret**, and **callback
   URL**. (App approval can take several days.)
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure credentials (never committed):
   ```bash
   cp scalper/.env.example scalper/.env
   # edit scalper/.env and fill in SCHWAB_APP_KEY / SCHWAB_APP_SECRET / callback
   ```
4. Complete OAuth once (opens a browser, persists a refresh token to
   `scalper/token.json`):
   ```bash
   python -m scalper.run_validation        # builds the client, runs Phase 0+1
   ```

**Phase 0 acceptance:** the runner prints the age of a live quote and confirms
it is **real-time** (not the 15-minute delayed feed). A delayed feed makes the
10-minute strategy impossible — resolve data entitlement before relying on live
results.

## Running the validation (Phase 1)

```bash
# Live (requires a completed OAuth token):
python -m scalper.run_validation

# From a previously-saved local sqlite store of bars:
python -m scalper.run_validation --store data.sqlite

# Offline smoke test of the full pipeline on synthetic data (no credentials):
python -m scalper.run_validation --demo
```

The report shows, **per target**: signal fire count, downside hit rate vs.
baseline, mean/median forward move vs. baseline, a significance test (Welch
t-test + bootstrap), and a spread-haircut check.

### The Phase 1 GO/NO-GO gate

- All forward moves are measured on the **underlying**, used as a proxy because
  intraday **options** history is unavailable. A positive underlying edge is
  **necessary but not sufficient**.
- Option bid/ask spreads (often 8-12% of premium on thin names) and slippage can
  erase the edge. The report's spread-haircut line flags this explicitly.
- **Decision:** if the signal's forward move is not meaningfully better than
  baseline *after accounting for spread*, **stop and rethink the signal** before
  building live infrastructure.

## Configuration

Edit `scalper/config.py` (no secrets there):

- `BASKET` / `TARGETS` — proxy signal inputs and traded names
- `SignalParams` — `lookback_minutes`, `red_threshold`, `min_red_count`, `hold_minutes`
- `MAX_SPREAD_PCT`, `RiskLimits` — liquidity & risk (used in later phases)
- `DataParams` — bar size, history length, hit-rate threshold, assumed spread

## Tests

```bash
pip install -r requirements.txt
pytest
```

The suite uses synthetic data and a fake client, so it runs with no network
access, credentials, or `schwab-py` installed.

## Safety notes

- **Secrets** live in `scalper/.env` (gitignored); tokens are never logged.
- **Read-only first.** No orders are placed anywhere in this codebase.
- **PDT rule:** under $25k on margin caps day trades at 3 per 5 business days.
