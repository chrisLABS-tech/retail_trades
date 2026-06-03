# PLAN.md — Cross-Asset Signal Options Scalper

## 1. Goal

Build a system around one trading idea: **when a basket of liquid tech proxies is broadly red, buy short-dated puts on a target name, expecting a fast downside continuation; cut quickly if the move doesn't materialize.**

Build it in this order, because each stage gates the next:

1. **Validate** the signal has real predictive edge (this is the only thing that justifies the rest).
2. **Journal** real fills and compute true expectancy net of spread/fees.
3. **Monitor** the signal live, read-only.
4. **Filter** for liquidity.
5. **Execute** (paper first, tiny size second).

Broker: **Charles Schwab** retail Trader API (free with any standard individual brokerage account; register a developer app at developer.schwab.com, link the brokerage account, authenticate via OAuth).

## 2. Language decision — Python, not C++

Use **Python 3.11+**. Rationale, so this isn't relitigated:
- Hold time is ~10 minutes. The latency bottleneck is the Schwab API round-trip and the human/strategy logic, not language execution speed.
- The heavy lifting (backtest, statistics, expectancy) is pandas/numpy/scipy work where Python is far more productive.
- A mature Schwab wrapper exists (`schwab-py`), handling OAuth, REST, and streaming.
- C++ only pays off for sub-millisecond execution, which this strategy does not require. Choosing it would mean hand-rolling OAuth/HTTP/JSON and losing the data-analysis ecosystem for zero practical benefit.

## 3. Stack

- Python 3.11+
- `schwab-py` — Schwab Trader API wrapper (OAuth, REST quotes/orders/option-chains, streaming). Verified against PyPI (latest 1.5.1 at time of writing); imported as `from schwab import auth, client`.
- `pandas`, `numpy`, `scipy` — data alignment, returns, statistics
- `python-dotenv` — secrets
- `pytest` — tests
- `matplotlib` — distribution/diagnostic plots
- `duckdb` or `sqlite3` — local store for bars and fills (this repo uses `sqlite3`)

## 4. Repo structure

```
scalper/
  config.py            # symbols, signal params, risk limits (no secrets)
  .env                 # SCHWAB_APP_KEY, SCHWAB_APP_SECRET, callback URL  (gitignored)
  auth/
    client.py          # build authenticated schwab-py client, token refresh
  data/
    history.py         # fetch intraday bars for basket + targets
    stream.py          # real-time quote stream (Phase 3+)
    store.py           # persist/load bars + fills (duckdb/sqlite)
  signal/
    definition.py      # the signal as a pure function of aligned bar data
  backtest/
    engine.py          # generate signals over history, measure forward returns
    stats.py           # hit rate, mean/median move, baseline comparison, significance
    report.py          # printable/plottable summary
  journal/
    fills.py           # pull executed orders from API
    expectancy.py      # win/scratch/loss buckets, avg win vs loss, net of costs
  screener/
    liquidity.py       # spread-as-%-of-mid filter; candidate ranking
  live/
    monitor.py         # evaluate signal on the live stream, log/alert (read-only)
    execute.py         # bracket: entry + take-profit + time-stop (Phase 5)
    risk.py            # max size, daily loss limit, trade count, kill switch
  tests/
```

## 5. Configuration (config.py)

- `BASKET = ["VT", "VGT", "MRVL", "MU", "SNDSK"]` (the proxy signal inputs)
- `TARGETS = ["INFQ", "P", ...]` (names actually traded)
- `LOOKBACK_MINUTES` — window over which "red" is measured
- `RED_THRESHOLD` — how negative a proxy must be to count as red (e.g., return < 0, or < -0.X%)
- `MIN_RED_COUNT` — how many of the basket must be red to fire (default: all)
- `HOLD_MINUTES` — time-stop (default 10)
- `MAX_SPREAD_PCT` — skip if option bid/ask spread exceeds this % of mid (e.g., 8)
- Risk: `MAX_CONTRACTS`, `MAX_TRADES_PER_DAY`, `DAILY_LOSS_LIMIT`

## 6. Phases

### Phase 0 — Setup & data gate
- [x] Build authenticated client in `auth/client.py` with automatic token refresh.
- [x] Fetch one live quote and one intraday history pull for a basket symbol.
- **Acceptance:** a real-time quote is confirmed **not** delayed (Schwab feeds can be 15-min delayed unless entitled), and intraday bars load into a DataFrame.

### Phase 1 — Signal validation (GO/NO-GO GATE — most important phase)
- [x] Pull aligned intraday bars for the basket + each target.
- [x] Implement `signal/definition.py` as a pure function.
- [x] In `backtest/engine.py`, measure each target's **forward return over `HOLD_MINUTES`** at every fire.
- [x] In `backtest/stats.py`, compare signal-conditioned forward returns to the **unconditional baseline**: hit rate, mean/median forward move, distribution, and a significance test (bootstrap + t-test).
- [x] Output a report per target.
- **Realism note:** backtest the **underlying's** forward move as a proxy. A positive underlying edge is **necessary but not sufficient** — option spreads and slippage can erase it. The report flags this explicitly via a spread-haircut check.
- **Acceptance / decision gate:** if the signal's forward move is not meaningfully better than baseline after accounting for spread, **stop and rethink the signal** before building live infrastructure.

### Phase 2 — Trade journal & expectancy  *(not yet built)*
### Phase 3 — Live signal monitor (read-only, no orders)  *(built)*
- [x] `data/stream.py`: wrap `schwab-py`'s `StreamClient`, subscribe to
  LEVELONE_EQUITIES (basket + targets) and LEVELONE_OPTIONS (near-ATM greeks),
  route every message into a pure, thread-safe `TickCache`.
- [x] `live/monitor.py`: a 15-second sampler (`MonitorParams.sample_seconds`,
  default 15) that, per comparison stock, logs trailing return, realized vol,
  red/not-red state, the basket-wide `red_count` (via the **unchanged** signal
  definition), and the option greeks **plus their change since the previous
  sample** (Δgreek). Samples persist to the sqlite `samples` table.
- **Read-only:** never places orders.

### Phase 4 — Liquidity screener  *(folded into selection)*
- [x] `live/execute.select_atm_put` screens candidates by the existing
  `MAX_SPREAD_PCT` spread-as-%-of-mid filter.

### Phase 5 — Execution (paper first, then tiny size)  *(built, default dry-run)*
- [x] `live/execute.py`: select a nearest-expiry, near-ATM **put** on a target,
  build a marketable-limit **BUY_TO_OPEN**, and **SELL_TO_CLOSE** on whichever
  fires first — the 10-minute time-stop (`HOLD_MINUTES`) or an optional
  take-profit / stop-loss on the option mark.
- [x] `live/risk.py`: pre-trade gate enforcing `MAX_CONTRACTS`,
  `MAX_TRADES_PER_DAY` (mind PDT), `DAILY_LOSS_LIMIT`, and a kill switch.
- [x] **Safety ramp:** `ExecutionMode` = `dry-run` (default, logs only) →
  `paper` → `live` (tiny size). Nothing is ever sent unless explicitly armed.

## 7. Critical caveats

- **Real-time data is mandatory** for a 10-minute strategy; confirm entitlement (not the 15-min delayed feed) in Phase 0.
- **Backtest is on the underlying, not the option** — treat any edge as a ceiling, not net P&L.
- **PDT rule**: < $25k on margin caps day trades at 3 per 5 business days.
- **Build read-only first; automate execution last.** Never debug live with real money.
- **Secrets** live in `.env`, never committed; tokens never logged.
