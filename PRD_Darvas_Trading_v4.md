# PRD: Serverless Automated Darvas Box Trading & Backtesting Platform

> **Implementation:** use [`REQUIREMENTS_v1.md`](REQUIREMENTS_v1.md) (v1.2) as the **single source of truth**. PRD v5/v6 divergences are reconciled in REQUIREMENTS Section 0. This file is retained as decision history and commentary.

**Revision 4.3 — Deep-Research Gap Closure (Formulas, Enforcement Rules, Data Source)**

---

## 0. Revision Summary

This revision keeps the architecture and intent of v3.0 (decoupled core engine, live/backtest dual context, shared persistence model) but closes gaps that would otherwise produce broken or non-compliant code from an AI coding agent. The major additions are: a regulatory compliance section (SEBI's retail algo framework, now mandatory), a broker authentication/session lifecycle section (Kite Connect tokens expire daily and exchange policy requires a human login), a data sourcing strategy (the original spec implicitly relied on `yfinance` for data it does not reliably provide), a precise Darvas Box state-machine definition, worked position-sizing formulas, a human-in-the-loop notification layer, a revised persistence model (SQLite-on-S3 is replaced for the *live* path), and a testing/rollout plan. Section 2 contains the strategic critique the product owner asked for.

**What changed in 4.1:** three ambiguities flagged in 4.0 have now been resolved through direct consultation with the product owner. (1) `discretionary` mode now auto-places GTTs and notifies afterward, rather than gating on approval — both modes execute identically (Section 5). (2) The Darvas Box bound calculation is now a defined hybrid of the classic 3-day Darvas reversal and ATR-based bands, using whichever side is tighter (Section 8). (3) Upstox was evaluated as a way to remove the daily-auth friction entirely and was found to carry an equivalent constraint, so the human-in-the-loop refresh design in Section 5 holds regardless of final broker choice. Section 16 is now a consolidated decision log — resolved items, open items with a recommendation attached to each, and defaults carried over unless you override them.

**What changed in 4.2:** three execution-architecture ambiguities flagged in 4.1 are now resolved. (1) `TRAIL_OCO` now has explicit math and emit rules: the stop ratchets to the current Darvas `box_bottom` whenever that bound rises on a re-formed or extended box — never by ATR chandelier, fixed %, or a separate higher-low rule (Section 9). (2) The kill switch is explicitly **end-of-day only** for v1: evaluated once at the 16:30 post-close run, compares today's close equity to yesterday's close equity, and can only block **next session's** new entries — it cannot prevent same-day losses; a separate intraday-polling job is deferred (Sections 3.2, 9, 16). (3) `fully_automated` and `discretionary` now differ at the **auth layer**, not execution: `fully_automated` requires `totp_automated_login` for unattended token refresh; `discretionary` uses `manual_daily_login` (morning Telegram link). Both modes still auto-place GTTs once a valid token exists (Section 5).

**What changed in 4.3:** gaps flagged in `Requirements-deep-research-report.md` are closed where the recommendation was unambiguous. (1) `max_portfolio_loss_per_trade_pct` is now enforced in the sizing formula (Section 9). (2) Breakout requires **both** daily close > `box_top` **and** volume ≥ threshold; backtester uses **daily close only** (Section 8). (3) Sector exposure, concurrency, cash, and target-price rules are explicit (Sections 8–9). (4) Fundamentals default source is **NSE official XBRL PIT** (Section 10); `avoid_days_before_earnings` scoped to **entry filter only** (Section 6). (5) IST timezone, holiday-calendar refresh, broker error-handling, partial-fill v1 assumption, and expanded unit-test list added (Sections 3.3, 10, 14). (6) Product-owner decisions: `enforce_long_term_growth_group` = 3-year positive YoY EPS (PIT); trend filter blocks advance only; cash = all-or-nothing (Section 16). (7) Default `config.yaml` backtest block aligned with `BACKTEST_PLAN_Darvas_Trading_v1.md`: **2018-01-01** start, **₹5L** capital, **2016-09** price warm-up.

---

## 1. Document Control
- **Title:** Serverless Automated Darvas Box Swing Trading Platform for Indian Markets
- **Version:** 4.3 (Deep-Research Gap Closure)
- **Date:** 2026-06-17
- **Author:** Product Owner / Lead System Architect (revised by Claude, Anthropic)
- **Target Engine:** AI Code Generation (Claude Code or equivalent)

---

## 2. Strategic Critique & Framing

A few framing issues are worth resolving before any code is written, because they change what gets built.

**This is not a pure Darvas Box system — name it honestly.** The fundamental filters (revenue/EPS growth, ROE/ROCE, debt-to-equity, promoter holding) are Minervini/SEPA-style trend-template and quality screens layered on top of a Darvas Box entry/exit trigger. That's a reasonable and fairly common hybrid (classic Darvas worked on momentum and volume alone, with no fundamental screen), but the PRD should say "Darvas Box entries with a fundamental quality pre-filter" rather than implying classic Darvas, so an AI agent — and you, six months from now — doesn't mis-implement the box logic by trying to also factor fundamentals into it.

**"ALL_NSE" as the default backtest/live universe is not realistic for an MVP.** Running fundamental screens (especially ones needing promoter holding % and ROCE, which aren't reliably available from free sources — see Section 10) across ~2,000 NSE-listed equities daily, inside a Lambda with a hard ceiling, is a significant data-engineering project on its own, independent of the trading logic. Recommend building and validating against `NIFTY_500` first, and only widening to `ALL_NSE` once the data pipeline is proven and a paid fundamentals/price vendor is in place.

**The self-improvement advisor is a parameter-search tool, not an autonomous learner — treat it that way.** "Produces algorithmic tuning recommendations" is dangerously close to "re-runs backtests until it finds parameters that worked in the past," which is curve-fitting on a single market regime (the default 2018–2026 backtest window has mostly NIFTY uptrend with a few corrections — limited regime diversity). Section 13 scopes this down to an advisory-only tool that never writes back to `config.yaml` without a human approving the change, and recommends walk-forward / out-of-sample validation rather than full-history optimization.

**This handles real capital and a live broker connection — it needs a rollout plan, not just a build plan.** Section 14 adds a paper-trading phase and a live/backtest parity test before any real order is allowed to reach the broker.

---

## 3. System Architecture

### 3.1 Structural Topology (unchanged intent)

```
   +-------------------------------------------------------------------+
   |                   CORE DETACHED STRATEGY ENGINE                   |
   |  Inputs: Target Date (T), Market Data Matrix, Account Context     |
   |  Outputs: Active State Machine Registers, Expected GTT Targets    |
   +-------------------------------------------------------------------+
                                |
        +-----------------------+-----------------------+
        |                                               |
        v                                               v
+-------------------------------+               +-------------------------------+
|  LIVE LAMBDA EXECUTION CONTEXT |               |  ON-DEMAND BACKTEST ENGINE     |
|  - Injected Date: Today        |               |  - Injected Date: T = Start    |
|  - Storage: DynamoDB/Postgres  |               |    Looping to End Date         |
|  - Broker: Live GTT Orders     |               |  - Storage: Isolated SQLite    |
|  - Trigger: Daily Event Bridge |               |  - Broker: Virtual Match Leg   |
+-------------------------------+               +-------------------------------+
                |                                               |
                +-----------------------+----------------------+
                                        v
                       +-------------------------------+
                       |     EXPORTS & ANALYTICS        |
                       |  - Performance Summary Report  |
                       |  - Replay Ledger CSV            |
                       +-------------------------------+
                                        |
                                        v
       +-------------------------------------------------------------+
       |              ON-DEMAND ADVISORY MODULE (MANUAL RUN)          |
       |  [Manual Run] -> Reads Live/Backtest Store -> Evaluates ->   |
       |                   Returns Advisory JSON (human-reviewed)    |
       +-------------------------------------------------------------+
```

Note the one structural change from v3.0: **live storage is no longer SQLite-on-S3** (see Section 11 for why), but the repository interface keeps `engine.py` and `advisor.py` storage-agnostic, preserving the original "runs interchangeably on live or backtest data" goal.

### 3.2 Decoupled Components (unchanged)
1. **Core Strategy Pipeline (`engine.py`)** — pure functional block: screening, sector momentum sorting, Darvas Box state evaluation, risk constraints, ideal order calculation. No knowledge of live vs. historical context.
2. **Daily Core Execution Module (`lambda_function.py`)** — invoked post-market close (16:30 IST). Injects today's date, updates live state store, syncs outstanding GTTs with the broker. For `fully_automated` mode, a pre-flight step (or a separate EventBridge trigger ~08:45 IST) runs `totp_automated_login` so the access token is valid before the close run. Kill-switch and `TRAIL_OCO` ratchets are evaluated here only — there is no intraday execution path in v1 (Section 9).
3. **On-Demand Backtesting Engine (`backtester.py`)** — run manually via CLI **on a local machine, EC2 instance, or Fargate task — not in Lambda** (the 30-minute / multi-year run target would risk Lambda's 15-minute hard execution ceiling; keep the heavy compute off Lambda entirely).
4. **On-Demand Self-Improvement Advisor (`advisor.py`)** — reads any ledger via the repository interface, produces advisory JSON. Never writes to `config.yaml` automatically.

### 3.3 AWS Implementation Notes (new — required for this to actually run)
- **Networking:** `lambda_function.py` must run inside a VPC with a NAT Gateway bound to a fixed Elastic IP. This is not optional — see Section 4. Pure Lambda-without-VPC has a dynamic, unregisterable egress IP.
- **Packaging:** Given `pandas`/`numpy`/TA libraries, deploy as a Lambda **container image** (via ECR) rather than a zip + layers, to avoid the 250MB unzipped layer ceiling.
- **Secrets:** Broker API key/secret, the daily access token, and the Telegram bot token must live in AWS Secrets Manager (or SSM Parameter Store), never in `config.yaml` or environment variables in plaintext.
- **Idempotency:** Lambda can retry on transient failure. Every GTT placement action must be keyed by `(symbol, target_date, action_type)` so a retried invocation doesn't double-place an order. The `trade_ledger.trade_id` should be derived deterministically from this tuple, not randomly generated.
- **Alerting:** Wire a CloudWatch Alarm → SNS → (email + Telegram) path for: Lambda failure/timeout, broker API errors, kill-switch trips, and missing/expired access token. A daily job that silently fails is the most dangerous failure mode for this system.
- **Broker API error handling (v1):** `PLACE_BUY_GTT` / `TRAIL_OCO` / `CANCEL_*` calls that fail with a transient error (HTTP 5xx, timeout, rate-limit 429) must be retried with exponential backoff up to **3 attempts** within the same Lambda invocation. Persistent failure after retries: log the `idempotency_key`, emit an SNS/Telegram alert, and **do not** mark the action as placed in `trade_ledger`. `ESTABLISH_OCO` failure after a confirmed buy fill: mark the position row `oco_pending_review = true`, alert immediately — do not emit duplicate buy actions on retry. Missing OHLCV or fundamentals for a symbol: **drop that symbol** for the day and continue (never abort the full run). Database read failure: abort the invocation and alert (no partial state writes without transactional commit).

---

## 4. Regulatory Compliance Requirements (new — must read before building)

SEBI's February 2025 circular on retail algorithmic trading (SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013) became fully mandatory for all brokers on **April 1, 2026**. Two provisions apply directly to this system:

- **Static IP whitelisting is mandatory for all API-based order placement, regardless of order frequency.** Zerodha will reject any order request (this includes GTT placement/modification) from an IP that isn't registered on the developer dashboard. Since this system places at most ~10 orders/day, the *order frequency* part of the framework doesn't apply — but the *static IP* part applies to everyone, with no exemption. This means the Lambda's outbound traffic must route through a fixed IP (the NAT Gateway + Elastic IP from Section 3.3), and that IP must be registered in the Kite Connect developer console under the account's IP Whitelist before going live.
- **Formal algo registration (Algo ID, exchange approval) is only required above 10 orders/second.** This system is trivially under that threshold, so no Performance Validation Agency registration is needed — the static IP requirement is the only piece that actually changes the build.
- **Market orders and SL-M orders now require an explicit `market_protection` value** (orders with `market_protection: 0` are rejected). This shouldn't affect this system since `PlannedGTTAction` always carries explicit `trigger_price`/`stop_loss_price`/`target_price` (LIMIT-type legs), but `engine.py` must never emit a bare MARKET order without setting `market_protection`.

**Action item before any live order is placed:** confirm the NAT Gateway's Elastic IP is registered in the Kite Connect developer dashboard's IP Whitelist section.

---

## 5. Broker Authentication & Session Lifecycle (new)

Kite Connect access tokens expire daily (flushed around 6 AM), and Zerodha's stated policy is that a manual login is required at least once per day per exchange guidance — the broker explicitly does not recommend fully automating this step. This directly conflicts with an unattended 16:30 IST Lambda trigger if nobody has logged in that day.

Two token-refresh strategies exist; **`system.mode` selects which one is required** (see below). Both are documented explicitly rather than left implicit:

1. **`manual_daily_login` (required for `system.mode: discretionary`).** Each morning, the user taps a link (e.g., from the Telegram bot in Section 12) that completes the Kite login redirect and stores a fresh `access_token` in Secrets Manager. `lambda_function.py` checks token validity before running and sends an alert (not a silent failure) if the token is missing or stale, skipping that day's run rather than crashing.
2. **`totp_automated_login` (required for `system.mode: fully_automated`).** A scheduled pre-market job (or the Lambda's own pre-flight step) performs programmatic login using API key/secret + TOTP secret stored in Secrets Manager. Commonly done by retail algo traders in practice, but it works against the spirit of the exchange's "manual login" requirement and is not officially supported — document it as an **accepted risk** for `fully_automated` only, never as the default for discretionary use.

**Resolved — mode behavior (execution vs. auth):**

| Layer | `discretionary` | `fully_automated` |
|---|---|---|
| **GTT execution** | Auto-place immediately after 16:30 computation | Identical — auto-place, no per-trade approval gate |
| **Token refresh** | `manual_daily_login` — human taps link each morning | `totp_automated_login` — unattended programmatic refresh |
| **Notifications** | Digest + auth reminders framed for human oversight (Section 12) | Operational alerts only (failures, kill switch, token errors) |

`config.yaml` validation must enforce: `fully_automated` ↔ `totp_automated_login`, and `discretionary` ↔ `manual_daily_login`. Mixing modes (e.g., `fully_automated` + `manual_daily_login`) is a configuration error that `lambda_function.py` must reject at startup.

**What "fully_automated" means:** no per-trade approval and no daily human auth tap — it is **not** zero human involvement in the abstract (TOTP credentials must be provisioned and rotated by a human), but the 16:30 run does not depend on anyone logging in that morning. **`discretionary` is the documented default** for operators who prefer the compliant, human-triggered refresh path.

**Broker evaluation note — Upstox.** Upstox was evaluated as a way to remove the daily-token problem entirely. Its standard access token actually expires *sooner* in wall-clock terms than Kite's (around 3:30 AM the next day, vs. Kite's ~6 AM same day), and while it offers a dedicated Access Token Request API aimed at algo use, that flow still requires a daily in-app approval tap from the user rather than a true set-and-forget token. Upstox's 1-year Analytics Token exists but is read-only — it can't place orders. **Conclusion: no major Indian retail broker currently offers an order-placing API without some form of daily human touchpoint** unless the operator accepts programmatic login (TOTP) as a risk — keep the mode-specific auth design above regardless of which broker is ultimately chosen.

---

## 6. Configuration Schema (`config.yaml`) — Revised

```yaml
system:
  mode: "discretionary" # Options: [discretionary, fully_automated] -- auth strategy is bound to mode; see Section 5
  execution_segment: "CASH"
  broker:
    provider: "zerodha_kite" # Upstox evaluated as an alternative -- found an equivalent daily-auth constraint, see Section 5
  auth:
    # MUST match system.mode: discretionary -> manual_daily_login; fully_automated -> totp_automated_login
    token_refresh_strategy: "manual_daily_login" # Options: [manual_daily_login, totp_automated_login]
    access_token_secret_arn: "arn:aws:secretsmanager:..."
    totp_secret_arn: "arn:aws:secretsmanager:..." # required only when token_refresh_strategy = totp_automated_login
  storage:
    live_backend: "dynamodb" # Options: [dynamodb, aurora_postgres] -- see Section 11
    backtest_backend: "sqlite"
  networking:
    static_ip_required: true
    nat_gateway_elastic_ip: "" # fill in after provisioning; must match Kite Connect IP Whitelist
  notifications:
    telegram_bot_token_secret_arn: "arn:aws:secretsmanager:..."
    telegram_chat_id: ""

backtest:
  target_segment: "NIFTY_500" # Recommend starting here, not ALL_NSE -- see Section 2
  start_date: "2018-01-01"   # full PRD from session 1; aligned with BACKTEST_PLAN_Darvas_Trading_v1.md
  end_date: "2026-05-31"
  initial_capital_inr: 500000.0
  price_warmup_start_date: "2016-09-01" # ingest only; engine loop begins at start_date (280-session lookback)
  export_directory: "./backtest_outputs"
  simulation_slippage_pct: 0.05
  execution_environment: "local" # Options: [local, ec2, fargate] -- never lambda, see Section 3.2

universe_filters:
  min_daily_volume_shares: 500000
  min_daily_turnover_inr_cr: 10.0
  min_stock_price_inr: 100.0
  lookback_years_for_52wk_high: 1   # see Section 8 -- classic Darvas precondition, renamed for clarity
  require_new_52wk_high: true
  exclude_asm_gsm: true

fundamental_filters:
  source: "nse_official_xbrl_pit" # NSE financial-results XBRL + shareholding pattern; effective_date = submission_date + 1 session -- see Section 10
  point_in_time_required: true # backtests must use fundamentals as they existed on date T, not today's snapshot
  min_revenue_growth_pct: 15.0
  min_eps_growth_pct: 15.0
  min_roe_pct: 15.0
  min_roce_pct: 15.0
  max_debt_to_equity: 0.5
  min_promoter_holding_pct: 40.0
  avoid_days_before_earnings: 5 # entry filter only for v1 -- does not force exit on open positions; see Section 6 note
  enforce_long_term_growth_group: true # positive YoY EPS growth in each of trailing 3 FY; PIT join -- see Section 8 screening note

darvas_box:
  box_bound_rule: "hybrid_darvas_atr" # Resolved -- tighter of classic Darvas reversal and ATR bands wins; see Section 8
  darvas_reversal_days: 3 # classic Nicolas Darvas reversal window
  atr_period: 20
  atr_multiplier: 2.0
  min_box_duration_days: 5
  max_box_duration_days: 30
  min_box_height_pct: 3.0   # now a sanity filter applied to the hybrid result, not the primary box definition -- see Section 8
  max_box_height_pct: 20.0
  breakout_volume_multiplier: 1.5
  required_price_history_days: 280 # >= 252 trading days for 52wk-high check + buffer; see Section 8
  market_trend_filter:
    index: "NIFTY 50"
    moving_averages: [50, 200]
    rule: "index_close_above_both_mas" # explicit -- see Section 8

risk_management:
  account_risk_pct: 1.0
  max_capital_per_trade_pct: 10.0
  max_sector_exposure_pct: 30.0
  max_concurrent_positions: 10
  stop_loss_buffer_fraction_inr: 0.05
  max_portfolio_loss_per_trade_pct: 10.0
  kill_switch_daily_loss_limit_inr: 25000  # 5% of default ₹5L book; scale with initial_capital_inr
  kill_switch_evaluation_timing: "eod_only" # v1: single 16:30 check only -- intraday polling deferred; see Section 9
  kill_switch_action: "halt_new_entries" # Options: [halt_new_entries, flatten_all] -- see Section 9
  sector_classification_source: "nse_official" # not a third-party label set -- see Section 9

trailing_stop:
  method: "box_bottom_ratchet" # Resolved -- see Section 9; do not substitute ATR chandelier or fixed %
  min_ratchet_inr: 0.05 # emit TRAIL_OCO only when new_stop exceeds current_stop by at least this amount

candidate_ranking:
  primary_metric: "structural_rr" # v1: rank by minimum objective R at entry -- see Section 9
  tiebreakers: ["sector_rs_percentile", "breakout_volume_ratio"] # applied in order when structural_rr ties
  sector_rs_lookback_days: 63 # ~3 months; rank sectors vs NIFTY 50 for sector_rs_percentile
```

---

## 7. Unified Interface Signature — Revised

```python
from pydantic import BaseModel
from datetime import date
from typing import List, Dict, Optional

class MarketContext(BaseModel):
    target_date: date
    account_equity: float
    open_positions: List[Dict]
    price_history_window_days: int  # must satisfy darvas_box.required_price_history_days (>=252+buffer)

class PointInTimeFundamentals(BaseModel):
    as_of_date: date  # the date this fundamental snapshot was actually known/published, not "today"
    symbol: str
    metrics: Dict[str, float]

class PlannedGTTAction(BaseModel):
    symbol: str
    action_type: str  # "PLACE_BUY_GTT", "CANCEL_BUY_GTT", "ESTABLISH_OCO", "TRAIL_OCO", "NO_CHANGE"
    # TRAIL_OCO: emitted when box_bottom ratchets up on an open position (Section 9) -- updates stop_loss_price only
    trigger_price: float
    stop_loss_price: float
    target_price: float
    quantity: int
    idempotency_key: str  # deterministic hash of (symbol, target_date, action_type)

def run_daily_strategy_iteration(
    context: MarketContext,
    fundamentals: List[PointInTimeFundamentals],
    repository,  # storage-agnostic repository, not a raw db_connection -- see Section 11
) -> List[PlannedGTTAction]:
    """
    Pure algorithmic computation block.
    1. Runs structural universe filters for context.target_date.
    2. Runs fundamental selection screens using point-in-time fundamentals only
       (never the latest/current snapshot, to avoid lookahead bias in backtests).
    3. Evaluates the Darvas Box state machine per Section 8, requiring at least
       darvas_box.required_price_history_days of price history.
    4. Evaluates risk allocations against context.account_equity per Section 9.
    5. When qualified breakouts exceed available slots (max_concurrent_positions,
       sector exposure, or settled cash), ranks survivors by structural_rr and
       tiebreakers per Section 9, emitting PLACE_BUY_GTT only for the top N.
    6. Returns a structured list of required GTT adjustments, each carrying
       an idempotency_key so retried invocations don't duplicate orders.
    """
    pass
```

---

## 8. Darvas Box State Machine — Precise Definition (Revised: Hybrid Darvas + ATR Rule)

The original PRD named the states (`SCANNING`, `FORMING`, `VALIDATED`, `BREAKOUT`) but never defined the transition rules. **Resolved this session:** the box bound calculation fuses the classic Nicolas Darvas 3-day reversal rule with ATR-based bands, taking whichever side is tighter (more conservative) at every point — this was confirmed as the preferred approach over either rule alone.

- **SCANNING → FORMING:** unchanged precondition — a stock enters `FORMING` when it makes a new high over the trailing `lookback_years_for_52wk_high` window (default: 1 year / ~252 trading days) and `require_new_52wk_high` is true. This is also why `required_price_history_days` must be at least 252 trading days, not the 30 days implied by the old interface docstring.
- **Locating the reversal pivot (Darvas component):** track a rolling `darvas_reversal_days`-day (default 3) high. The reversal point triggers on the first session where, after a run of new 3-day highs, the close fails to extend that high for `darvas_reversal_days` consecutive sessions. The highest high reached just before that failure becomes `darvas_top`; the low of the failure session becomes `darvas_bottom`.
- **Computing the ATR band (ATR component), anchored at the same reversal pivot:** compute the Average True Range over `atr_period` (default 20) trading days as of the reversal point. `atr_top = reversal_high + atr_multiplier * ATR`; `atr_bottom = reversal_high - atr_multiplier * ATR` — centered on the reversal high, since that's the one pivot both components agree on.
- **Effective box bounds (the fusion):** `box_top = min(darvas_top, atr_top)`; `box_bottom = max(darvas_bottom, atr_bottom)` — i.e., the tighter of the two on each side wins.
- **Sanity filter:** if the resulting `(box_top - box_bottom) / box_bottom` falls outside `[min_box_height_pct, max_box_height_pct]`, discard the box and return the stock to `SCANNING` — this applies whenever bounds are recomputed (including mid-`FORMING` / mid-`VALIDATED` if a subsequent daily update pushes height out of range), not only at initial box creation.
- **FORMING → VALIDATED:** the box is confirmed once price has held within `[box_bottom, box_top]` for at least `min_box_duration_days` consecutive sessions. If price closes outside that range before `min_box_duration_days` is reached, or the box persists past `max_box_duration_days` without a qualifying breakout, it resets to `SCANNING`.
- **VALIDATED → BREAKOUT:** requires **both** conditions on the same session `T` (AND logic — neither alone is sufficient):
  1. **Price:** official session **close** > `box_top`.
  2. **Volume:** `volume[T] >= volume_sma_20[T] * breakout_volume_multiplier`.
  An intraday touch above `box_top` that fails to hold into the close **does not** qualify. Partial intraday crosses that revert below `box_top` by close are **not** breakouts.
- **Backtester parity (v1):** use daily Bhavcopy close and volume only — no intraday-bar breakout path. This matches the live 16:30 post-close evaluation model.
- **Trend filter gate (applies at every state transition, not just entry):** the NIFTY 50 index close must be above both its 50-day and 200-day moving averages (`index_close_above_both_mas`) for any state transition that would advance toward breakout or emit `PLACE_BUY_GTT`. If the trend filter fails while a symbol is in `FORMING` or `VALIDATED`, **block advancement only** — preserve current box bounds and state; resume normal transitions when the filter passes again. Do **not** reset to `SCANNING` solely because the index filter failed.

**Fundamental screen — `enforce_long_term_growth_group`:** when `true`, require **positive YoY EPS growth** in each of the **trailing 3 completed fiscal years** relative to the prior year, using PIT fundamentals only (`effective_date <= context.target_date`). A single non-positive year fails the filter. Compute from NSE XBRL EPS line items, not pre-aggregated screen CAGRs.

**Open-position note (feeds `TRAIL_OCO`, Section 9):** for symbols with an active trade in `trade_ledger`, the Darvas state machine continues to update `box_bottom` (and `box_top`) on each daily iteration even after `BREAKOUT`. If price action re-forms or tightens the box and `box_bottom` rises, that new value is the ratchet input for `TRAIL_OCO`. If the box invalidates back to `SCANNING`, the position's stop is **not** lowered — the last ratcheted stop in `trade_ledger` is preserved until exit or a new higher `box_bottom` appears.

---

## 9. Risk & Position Sizing — Worked Formulas (new)

The original PRD specified risk *limits* but not the *formula* connecting them to an actual order quantity — this is the single most important piece of math in the system and shouldn't be left to an AI agent's interpretation.

```
risk_amount_inr = account_equity * (risk_management.account_risk_pct / 100)
per_share_risk_inr = entry_price - stop_loss_price

# Guard: if stop >= entry or per_share_risk rounds to zero, drop the candidate (no division by zero)
if per_share_risk_inr <= 0: candidate is rejected

raw_quantity = floor(risk_amount_inr / per_share_risk_inr)

capital_cap_qty = floor((account_equity * risk_management.max_capital_per_trade_pct / 100) / entry_price)

portfolio_loss_cap_qty = floor(
    (account_equity * risk_management.max_portfolio_loss_per_trade_pct / 100) / per_share_risk_inr
)

final_quantity = min(raw_quantity, capital_cap_qty, portfolio_loss_cap_qty)

# All-or-nothing cash rule: require settled cash to fund the FULL final_quantity
if final_quantity < 1 OR (final_quantity * entry_price > settled_cash_inr):
    candidate is rejected

# Do not downsize below risk-modeled quantity to fit cash — skip the trade instead
```

`portfolio_loss_cap_qty` enforces `max_portfolio_loss_per_trade_pct`: even at the stop, total loss `final_quantity × per_share_risk_inr` must not exceed that % of equity.

**T+1 settled cash:** `settled_cash_inr` excludes same-day sale proceeds (CASH segment). Unsettled funds are not available for new buys.

### Initial stop at entry (`ESTABLISH_OCO`)

When a breakout fill establishes a position, the initial stop-loss is anchored to the box, not a trailing rule:

```
initial_stop_loss = box_bottom - risk_management.stop_loss_buffer_fraction_inr
target_price     = box_top + (box_top - box_bottom)   # = entry_price + box_height when entry at box_top
```

Set `target_price` identically on `PLACE_BUY_GTT` and `ESTABLISH_OCO`. `ESTABLISH_OCO` is emitted once on fill confirmation during the daily reconciliation step.

`target_price` is a **first objective** for the OCO bookkeeping leg — not a forecast of final trade reward. Open-ended upside is captured by `TRAIL_OCO` (box-bottom ratchet), not by revising `target_price` upward. Candidate ranking uses structural R (below), which measures minimum payoff per unit of defined risk at entry only.

### Candidate ranking — structural R (when multiple breakouts compete for slots)

On any day where more symbols pass the Darvas breakout and risk filters than can be taken (`max_concurrent_positions`, `max_sector_exposure_pct`, or settled-cash limits bind), `engine.py` must **rank** survivors and emit `PLACE_BUY_GTT` only for the top candidates. Position sizing already equalizes **dollar risk** per trade (`account_risk_pct`); ranking answers which setups deploy that risk most efficiently.

**Do not rank on projected infinite upside.** Darvas boxes can extend indefinitely; ranking uses the minimum objective reward measurable at signal time — one box height to `target_price` — not a forecast of how far price will run under the trail.

**Per-candidate inputs (all known at 16:30 on `context.target_date`):**

```
box_height     = box_top - box_bottom
entry_price    = box_top                    # GTT buy trigger at breakout level; use actual close if sizing on fill
stop_loss_price = box_bottom - risk_management.stop_loss_buffer_fraction_inr
target_price   = box_top + box_height       # one box-height objective; must match ESTABLISH_OCO plan

risk_per_share    = entry_price - stop_loss_price
reward_to_target  = target_price - entry_price   # equals box_height when entry_price = box_top
structural_rr     = reward_to_target / risk_per_share
```

When `entry_price = box_top`, this simplifies to:

```
structural_rr ≈ box_height / (box_height + stop_loss_buffer_fraction_inr)
```

which is slightly below 1:1 with the default target rule — that is expected and fine; cross-stock comparability matters more than the absolute ratio.

**Selection algorithm (greedy, deterministic):**

1. **Pre-check hard stops:** if `kill_switch_active`, return no new `PLACE_BUY_GTT`. If `count(open_positions where is_active=1) >= max_concurrent_positions`, return no new buys.
2. Size each breakout candidate → `final_quantity` per formulas above; drop if rejected.
3. Sort surviving candidates by `structural_rr` descending.
4. On ties (identical `structural_rr` to 4 decimal places), apply `candidate_ranking.tiebreakers` in order:
   - `sector_rs_percentile` — sector's trailing return vs. NIFTY 50 over `candidate_ranking.sector_rs_lookback_days` (default 63).
   - `breakout_volume_ratio` — `breakout_day_volume / volume_sma_20`.
5. **Greedy fill:** iterate sorted list; for each candidate, accept only if **all** of the following hold:
   - `open_position_count + already_selected_count < max_concurrent_positions`
   - **Sector exposure:** `(sector_market_value_inr + candidate_qty * entry_price) / account_equity <= max_sector_exposure_pct / 100`, where `sector_market_value_inr` = sum of `qty * last_close` for open positions in the same NSE official sector (mark-to-market at `context.target_date` close)
   - `final_quantity * entry_price <= settled_cash_inr` (**all-or-nothing** — if cash insufficient for full sized qty, skip with `INSUFFICIENT_CASH`; no partial downsizing)
6. On reject at step 5, record `skip_reason` (`MAX_POSITIONS`, `SECTOR_CAP`, `INSUFFICIENT_CASH`, `RANKED_OUT`) in `decision_log`; try next candidate.
7. Emit `PLACE_BUY_GTT` for accepted candidates only.

**Persistence:** store `structural_rr` and the tiebreaker values on each `PLACE_BUY_GTT` row in `trade_ledger` (or a companion audit field) so live decisions are reproducible and the advisor module can later compare structural R at entry vs. realized R at exit.

**v2 (deferred):** replace or augment `structural_rr` with `expected_r` from backtest buckets (box-height bucket × sector RS quintile × box-duration bucket) once sufficient simulated trade history exists under the actual `TRAIL_OCO` rules — see Section 13.

### Trailing stop (`TRAIL_OCO`) — box-bottom ratchet (resolved)

`TRAIL_OCO` is **not** an independent trailing mechanism (no ATR chandelier, no fixed %, no Darvas higher-low scan). It reuses the same `box_bottom` the Darvas state machine already maintains in `active_state_registry` (Section 8). As price action re-forms or extends the box and `box_bottom` rises, the open position's stop ratchets up to track that bound.

**Formula (evaluated once per open position, each daily run at 16:30 IST):**

```
candidate_stop = active_state_registry.box_bottom   # for this symbol on context.target_date
new_stop       = max(trade_ledger.current_stop_loss, candidate_stop)
```

**Emit rules — `TRAIL_OCO` is produced only when all of the following hold:**

1. The symbol has an active open row in `trade_ledger` (`is_active = 1`).
2. `active_state_registry.box_bottom` is not `NULL` and the box has not been discarded to `SCANNING` without a replacement box (if the box resets while a position is open, **do not lower** the stop — hold `current_stop_loss` unchanged and emit `NO_CHANGE` for that symbol).
3. `new_stop > trade_ledger.current_stop_loss + trailing_stop.min_ratchet_inr` (default ₹0.05) — avoids broker churn on trivial tick improvements.
4. `new_stop < entry_price` is not required (the stop may eventually sit at or above entry as the box rises; that is expected ratchet behavior).

**What `TRAIL_OCO` does at the broker:** modify the existing GTT OCO's stop-loss leg to `stop_loss_price = new_stop`; `target_price` is unchanged unless separately revised. The stop **only ever ratchets upward**, never loosens, even if `box_bottom` later falls or the box invalidates.

**Backtest parity:** the virtual match leg applies the same ratchet rule on each simulated day using that day's `active_state_registry.box_bottom`.

### Kill switch — end-of-day evaluation only (resolved for v1)

The system architecture runs **once per day** post-close (Section 3.2). The kill switch is therefore an **end-of-day check**, not an intraday circuit breaker. This is an explicit product decision: v1 **cannot prevent losses that occur during the trading session** — it can only block **the next session's** new entries.

**When evaluated:** once per invocation of `lambda_function.py` at 16:30 IST, **after** broker position reconciliation and **before** new `PLACE_BUY_GTT` actions are computed.

**Formula:**

```
daily_loss_inr = max(0, account_equity_at_yesterday_close - account_equity_at_today_close)
```

`account_equity_at_*_close` = cash (settled) + mark-to-market of open positions at that session's official close. Persist both values in the repository (e.g., `portfolio_snapshots` table or a single `system_state` record) so the comparison is auditable.

**Trip condition:** `daily_loss_inr >= risk_management.kill_switch_daily_loss_limit_inr`

**On trip (`kill_switch_action: halt_new_entries`, default):**

- Set `kill_switch_active = true` with `tripped_on_date = context.target_date` in persistent system state.
- Suppress all `PLACE_BUY_GTT` actions on **subsequent** daily runs until a manual reset (Telegram command or config flag) clears the latch.
- Existing positions continue under their own OCO stop/target legs — no forced exit.
- Send an immediate alert (Section 12).

**Explicit non-goals for v1:** no intraday quote polling, no mid-session order cancellation, no same-day entry block after a morning drawdown. A separate intraday-polling Lambda (e.g., every 15–30 minutes during market hours) is **deferred** unless paper-trading shows the EOD check misses material risk; if added later, it would be a new component with its own schedule, not a reinterpretation of this field.

**Kill switch behavior (`risk_management.kill_switch_action`):** `halt_new_entries` (recommended default — stop placing new `PLACE_BUY_GTT` actions until manual reset, leave existing stops to manage themselves) vs. `flatten_all` (also cancel GTTs and queue exit orders — still only triggered at 16:30 in v1, so it exits at next opportunity, not intraday). Pick one as default and make it configurable.

**Sector classification source:** `max_sector_exposure_pct` requires a sector taxonomy. Use NSE's official sector/industry classification (published in NSE's security-wise classification reports) rather than an ad hoc or third-party label set, so sector exposure numbers are auditable against the same source the exchange uses.

**Partial fills and corporate actions on open positions (v1 assumptions):** GTT buy triggers are modeled as **fill-all-or-none** at the trigger price (+ slippage in backtest). Partial exchange fills are **not** simulated in v1 — reconcile live broker reported qty daily; if actual qty ≠ planned qty, update `trade_ledger` and recompute OCO legs before the next action. The daily reconciliation step (Section 3.3) must reconcile live broker positions/holdings against `trade_ledger` before computing new actions — manual user trades, unexpected partial fills, or bonus/split adjustments otherwise desync state.

---

## 10. Data Sourcing Strategy (new — this is the platform's actual hardest problem)

**Timezone and session convention:** all `target_date` values, OHLCV bars, index MAs, and `effective_date` joins use **NSE trading sessions in `Asia/Kolkata` (IST)**. The daily run assumes official NSE end-of-day prices (post-15:30 close, evaluated at the 16:30 invocation). API calls to Kite/Upstox must not convert bar timestamps to UTC for comparison logic.

The original PRD names `yfinance` as the fundamentals source and is silent on the OHLCV price source. Both need a real decision before coding starts:

- **Price/OHLCV data:** For backtesting, prefer NSE's own Bhavcopy archives (free, official, end-of-day) over `yfinance`, which has known rate-limiting and occasional gap issues at the scale of a few thousand symbols. For live trading, use the broker's own historical/quote API (Kite Connect) since it's already authenticated and is the same data the broker will use to fill GTTs against.
- **Fundamentals — default source (resolved v4.3):** build a local **point-in-time warehouse** from **NSE official filings** (not third-party screen snapshots):
  - **Financial results XBRL** ([NSE Corporate Filings](https://www.nseindia.com/companies-listing/corporate-filings-financial-results)) — revenue, PAT/EPS, balance-sheet lines for ROE/ROCE/D/E; mandatory quarterly XBRL from ~Apr 2017.
  - **Shareholding pattern** ([NSE SHP filings](https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern)) — promoter %; quarterly.
  - **Corporate announcements** — board-meeting / results dates for `avoid_days_before_earnings`.
  - **PIT rule:** `effective_date = next_trading_session_after(submission_date)`; on simulation date `T`, join `MAX(effective_date) WHERE effective_date <= T` per `(symbol, metric)`. Never use Screener/Tijori exports as primary ingest (lookahead risk). See `BACKTEST_PLAN_Darvas_Trading_v1.md` for ingest detail.
  - **Tooling:** [`nse-xbrl`](https://pypi.org/project/nse-xbrl/) for Integrated Filing (2025+); legacy XBRL parser for 2017–2025.
- **Point-in-time requirement.** Store `effective_date` (and `source_url` to the filing) per metric row. A backtest that screens today's fundamentals against historical prices is lookahead bias — address at the data-model layer, not only in `engine.py`.
- **ASM/GSM exclusion:** NSE publishes these lists at its official reports endpoint (`nseindia.com/reports/asm` and `/gsm`). Cache the daily list once per session rather than re-fetching inside the per-symbol loop.
- **Trading holiday calendar:** use NSE's official annually-published holiday list; **refresh in Q3 each year** for the following calendar year (do not derive holidays algorithmically).
- **Survivorship-bias-free universe for "ALL_NSE" backtests:** constructing a historically accurate "all stocks listed on NSE as of date T" list (including delisted names) is a known hard data problem — flag as follow-up; MVP uses NIFTY 500 point-in-time membership from Nifty Indices monthly archives.

---

## 11. Persistent Database Model — Revised

SQLite-on-S3 for the *live* path is fragile: S3 has no row-level locking, a Lambda retry mid-write risks a corrupted or partially-written file, and there's no real concurrency story if two invocations ever overlap. Backtests are fine on SQLite (single-writer, single isolated run, no concurrency concerns) — keep that as-is. For live state, use **DynamoDB** (serverless-native, pairs naturally with Lambda's execution model) or **Aurora Serverless Postgres** if relational queries across positions are needed for the advisor module.

To preserve the original goal — `advisor.py` should work identically against live or backtest data — both backends should sit behind a shared repository interface (e.g., `get_open_positions()`, `record_trade()`, `get_state_registry()`) rather than `advisor.py` opening a raw SQLite connection directly. The two logical tables stay the same:

### Table: `active_state_registry`
```sql
CREATE TABLE active_state_registry (
    symbol TEXT PRIMARY KEY,
    box_state TEXT CHECK(box_state IN ('SCANNING', 'FORMING', 'VALIDATED', 'BREAKOUT')),
    box_top REAL,
    box_bottom REAL,
    box_start_date DATE,
    box_end_date DATE,
    volume_sma_20 REAL,
    last_updated_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Table: `trade_ledger`
```sql
CREATE TABLE trade_ledger (
    trade_id TEXT PRIMARY KEY,  -- deterministic hash of (symbol, target_date, action_type), not random
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT NOT NULL,
    direction TEXT CHECK(direction IN ('BUY', 'SELL')),
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    current_stop_loss REAL,
    current_target REAL,
    structural_rr_at_entry REAL,  # candidate_ranking audit field -- see Section 9
    gtt_buy_trigger_id TEXT,
    gtt_position_oco_id TEXT,
    is_active INTEGER CHECK(is_active IN (0, 1)) DEFAULT 1,
    exit_reason TEXT DEFAULT NULL -- Options: ['STOP_LOSS_HIT', 'TARGET_HIT', 'MANUAL_OVERRIDE']
);
```

(For DynamoDB, map `symbol` and `trade_id` to partition/sort keys respectively; the field semantics above are unchanged regardless of backend.)

### Table: `system_state` (portfolio-level flags — new in 4.2)
```sql
CREATE TABLE system_state (
    key TEXT PRIMARY KEY,  -- e.g. 'kill_switch', 'equity_snapshot'
    value_json TEXT NOT NULL,
    last_updated_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Required keys for v1:
- `equity_snapshot`: `{ "date": "YYYY-MM-DD", "equity_at_close_inr": float }` — written each 16:30 run; previous row used for kill-switch `daily_loss_inr` comparison.
- `kill_switch`: `{ "active": bool, "tripped_on_date": "YYYY-MM-DD" | null, "daily_loss_inr_at_trip": float | null }` — latched until manual reset.

---

## 12. Notification & Human-in-the-Loop Interface (new)

Both modes need visibility into daily activity, but the notification framing differs by mode (Section 5):

- **`discretionary`:** morning Telegram message with the Kite login link (`manual_daily_login`); post-16:30 digest summarizing `PlannedGTTAction` list and any `TRAIL_OCO` ratchets; reminders if auth was not completed before the close run.
- **`fully_automated`:** no daily login prompt; post-16:30 operational digest (actions placed, errors); immediate alerts for kill-switch trips, Lambda failures, TOTP/auth failures, and broker API errors.

Push alerts (both modes): kill-switch trips, Lambda failures, missing/expired access token. A simple read-only dashboard for browsing `trade_ledger` history and backtest reports (rather than only raw JSON/CSV files) is a reasonable v2 addition once the core loop is working. Per-trade approve/reject controls remain a future enhancement — neither mode gates execution on them in v1.

---

## 13. Self-Improvement Advisor Module — Scoped (revised)

`advisor.py` reads the ledger via the repository interface and produces a structured JSON report containing: performance breakdowns by sector, by box-duration bucket, and by holding period; and a parameter-sensitivity scan that re-runs the backtest across a small grid of nearby values for two or three config knobs (e.g., `breakout_volume_multiplier`, `min_box_height_pct`) to show how sensitive results are to those choices. It does **not** write back to `config.yaml` automatically — every suggested change requires explicit human approval before being applied, and ideally a walk-forward (train on part of the history, validate on a later untouched slice) rather than whole-history optimization, given the limited regime diversity in the default 2018–2026 backtest window.

---

## 14. Testing, Validation & Rollout Plan (new)

- **Unit tests** for the Darvas state machine transitions (Section 8), the position-sizing formula including `portfolio_loss_cap_qty` and `per_share_risk <= 0` rejection (Section 9), the `structural_rr` ranking and greedy slot-fill logic including sector-cap and concurrency (Section 9), the `TRAIL_OCO` box-bottom ratchet emit rules (Section 9), and kill-switch EOD trip/reset logic (Section 9).
- **Gap-exposure tests (from requirements review):**
  - **PIT / lookahead:** assert fundamentals join uses only `effective_date <= T`; shifting all `effective_date` +90 days must materially reduce trade count.
  - **Max portfolio loss cap:** equity ₹5L, huge `per_share_risk` → `portfolio_loss_cap_qty` binds or candidate dropped; loss at stop ≤ 10% equity.
  - **Breakout volume:** close > `box_top` but volume 1.2× SMA20 → no `BREAKOUT` / no `PLACE_BUY_GTT`.
  - **Target price:** `box_top=110`, `box_bottom=100`, entry `110` → `target_price=120`.
  - **Sector cap:** two Tech candidates each 20% of equity at 30% sector cap → only higher-ranked fills.
  - **Concurrency:** 10 open positions → zero new `PLACE_BUY_GTT`.
  - **Idempotency:** re-run same `target_date` → identical `PlannedGTTAction` list and keys.
- **Live/backtest parity test:** run the same target date through both contexts with identical mocked price/fundamentals inputs and assert `run_daily_strategy_iteration` returns identical `PlannedGTTAction` lists. This is the actual proof that the "decoupled, runs interchangeably" architecture goal (Section 3) holds, rather than an assumption.
- **Paper-trading phase:** run the live Lambda path for a minimum of 4–6 weeks logging signals (and, optionally, placing GTTs with a token but immediately cancelling, or simply logging without placing) before connecting real capital, to catch data/timing issues that a backtest can't surface.
- **Phased capital scale-up:** start live capital well under `backtest.initial_capital_inr`, and only scale up after a few months of live results are consistent with backtest expectations.

---

## 15. Operational & Non-Functional Boundaries — Revised

- **Memory:** backtester must keep peak RAM under 2GB via a chunked/streaming dataframe pipeline (unchanged from v3.0).
- **Throughput:** an ~8-year (`2018-01-01` → `2026-05-31`), `NIFTY_500`-scope backtest should complete within 30 minutes on the chosen execution environment (Section 6 — local/EC2/Fargate, never Lambda); re-benchmark this target if/when scope widens to `ALL_NSE`, since fundamentals-ingest latency (Section 10) is likely to dominate runtime more than the price-data processing itself.
- **Data parity:** split/dividend adjustments must be applied retroactively relative to simulated date T (unchanged from v3.0) — this is now explicitly tied to whichever OHLCV vendor is chosen in Section 10, since the adjustment logic lives in that ingestion layer, not as separate ad hoc code.
- **Lambda packaging:** container image via ECR, not zip+layers, given the `pandas`/`numpy`/TA-library footprint (Section 3.3).

---

## 16. Decision Log (Consolidated)

### Resolved
- **Discretionary vs. fully_automated execution behavior.** Both modes auto-place GTTs immediately after computation; there is no per-trade approval gate. Modes differ at the **auth layer** (`manual_daily_login` vs. `totp_automated_login`) and notification framing (Sections 5, 12).
- **Darvas Box calculation rule.** Hybrid of the classic 3-day Darvas reversal and ATR-based bands — the tighter side wins on each bound (Section 8).
- **Broker auth friction (Zerodha vs. Upstox).** Both require a daily token refresh for order-placing APIs; switching broker does not remove this. `discretionary` uses human-triggered refresh; `fully_automated` accepts TOTP programmatic refresh as a documented risk (Section 5).
- **Trailing stop formula for `TRAIL_OCO`.** Box-bottom ratchet: `new_stop = max(current_stop, box_bottom)` when `box_bottom` rises on a re-formed/extended box; evaluated once daily at 16:30; emit only when improvement ≥ `trailing_stop.min_ratchet_inr`. Initial stop at entry remains `box_bottom - stop_loss_buffer_fraction_inr` (Section 9).
- **Kill switch evaluation timing.** v1 = end-of-day only at 16:30: `daily_loss_inr = max(0, equity_yesterday_close - equity_today_close)`; trip halts **next session's** `PLACE_BUY_GTT` until manual reset. Intraday polling deferred (Section 9).
- **Candidate ranking when capital-constrained.** v1 primary metric = `structural_rr` (minimum objective R to `target_price` at entry); tiebreakers = sector RS percentile, then breakout volume ratio (Section 9).
- **What `fully_automated` means.** Unattended daily operation via `totp_automated_login` — not "zero human involvement ever," but no morning login tap and no per-trade approval (Section 5).
- **Fundamentals data source.** `nse_official_xbrl_pit` — NSE financial-results XBRL + shareholding pattern with `effective_date` discipline (Section 10).
- **`max_portfolio_loss_per_trade_pct` enforcement.** `portfolio_loss_cap_qty` in sizing formula (Section 9).
- **Breakout trigger.** Both close > `box_top` AND volume ≥ threshold required; daily close only in backtester (Section 8).
- **`avoid_days_before_earnings`.** Entry filter only for v1; no forced exit on open positions (Section 6).
- **Sector exposure.** Checked at new-entry time: sector MTM + planned trade ≤ `max_sector_exposure_pct` (Section 9).
- **`max_concurrent_positions`.** Count active open positions; block new buys when count ≥ limit (Section 9).
- **`enforce_long_term_growth_group`.** Positive YoY EPS growth in each of trailing 3 FY; PIT join on filing dates (Section 8).
- **Trend filter mid-box.** Block state advance only; preserve box — no reset to `SCANNING` on index filter alone (Section 8).
- **Cash constraint.** All-or-nothing: skip trade unless `settled_cash_inr` funds full `final_quantity` (Section 9).
- **Default backtest parameters.** Aligned with `BACKTEST_PLAN_Darvas_Trading_v1.md` (Section 6).

### Open — Recommendation Attached to Each

_None remaining from deep-research review as of v4.3 — new gaps should be logged here._

### Confirmed Defaults (carried over from 4.0 — flag if you want these changed)
- `fundamental_filters.source: nse_official_xbrl_pit` (Section 10).
- Cash sizing: all-or-nothing vs settled cash (Section 9).
- Default backtest window: `2018-01-01` → `2026-05-31`, `initial_capital_inr: 500000`, `price_warmup_start_date: 2016-09-01` (Section 6; aligned with `BACKTEST_PLAN_Darvas_Trading_v1.md`).
- `kill_switch_daily_loss_limit_inr: 25000` at default capital (Section 9).
- `kill_switch_action: halt_new_entries` (Section 9).
- `kill_switch_evaluation_timing: eod_only` (Section 9).
- `trailing_stop.method: box_bottom_ratchet` (Section 9).
- `candidate_ranking.primary_metric: structural_rr` (Section 9).
- `system.mode: discretionary` with bound `manual_daily_login` auth (Section 5).
- MVP universe scope: `NIFTY_500` before `ALL_NSE` (Section 2).
- NAT Gateway + Elastic IP cost: accepted as a fixed cost of SEBI's static-IP mandate (Section 4) — there's no compliant alternative, so this is less an open choice than a confirmed requirement.
