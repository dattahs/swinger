# Backtest Plan: Darvas Box Trading System (Historical Simulation)

**Companion to:** `PRD_Darvas_Trading_v4.md` (v4.2)  
**Version:** 1.2  
**Date:** 2026-06-17  
**Confirmed:** Full-PRD backtest **2018-01-01 → 2026-05-31** (~8.4 years, all filters from day one); fundamentals via **free NSE/BSE filings** (PIT-safe). Price warm-up from **~2016-09** for 280-session lookback.

**Objective:** Replay the full PRD strategy day-by-day from **2018**, starting with **₹5,00,000**, and produce an auditable **decision log** and **trade log** — without placing live orders.

---

## 1. Executive Summary

This system is **end-of-day (EOD) only**: the live Lambda runs once at 16:30 IST post-close. A faithful backtest therefore needs **daily OHLCV** — not tick or 1-minute bars — plus **point-in-time (PIT) fundamentals** and **historical index membership**. Intraday data is optional and not required for v1 parity with the PRD.

**Recommended data stack:**

| Layer | Primary source | Your subscriptions |
|---|---|---|
| Daily OHLCV + volume | NSE Bhavcopy archives (free) | Use Zerodha Kite / Upstox APIs to **validate & adjust** |
| NIFTY 50 index | NSE index bhavcopy / `jugaad-data` | Kite or Upstox (cross-check) |
| NIFTY 500 universe (date T) | Nifty Indices monthly constituent archives (free) | — |
| PIT fundamentals | **NSE/BSE official filings (free)** — XBRL + shareholding pattern | Screener/Tijori = research only; **not** PIT-safe without filing-date ETL |
| ASM/GSM, holidays, sectors | NSE / Nifty Indices official publishes (free) | — |

**Broker subscriptions verdict:**

- **Zerodha (Kite Connect):** Best **paid supplement** you already have — daily history back ~2,000 sessions per API call (~8 years); chunk for 10 years. Corporate-action-adjusted candles. Good for validation, not the only source for 500 symbols × 10 years (rate limits + API churn).
- **Upstox API:** Strong **backup** — V3 daily candles from Jan 2000, up to **one decade per request** per symbol. Free with account; generous rate limits for one-time bulk ingest.
- **AC Agarwal:** **Not a primary backtest data source.** XTS API supports orders + market/historical data for **your account**, and the research desk (SEBI RA) is advisory. No evidence of a bulk, point-in-time fundamentals API comparable to what this backtest needs. Optional: spot-check prices against BLOOM/XTS if you trade there.

---

## 2. Scope

### 2.1 Simulation parameters

| Parameter | Value | Notes |
|---|---|---|
| **Full-PRD backtest window** | **2018-01-01 → 2026-05-31** (~8.4 years) | All PRD filters (price + fundamentals) active from first session — no price-only warm-up year |
| Price warm-up buffer | **+280 trading sessions** before `start_date` | PRD `required_price_history_days`; ingest Bhavcopy from **~2016-09-01** |
| PIT fundamentals ingest | **2017-Q1 onward** (prefetch) | Ensures YoY growth and prior-quarter balance sheet exist before 2018-01-01 |
| Initial capital | **₹5,00,000** | Override PRD default ₹10L for this run |
| Universe | **NIFTY 500** (membership as of each date T) | Not today's list applied retroactively |
| Execution model | EOD at 16:30 IST | Same order of operations as `lambda_function.py` |
| Slippage | **0.05%** per side (PRD default) | Applied on virtual fills |
| Cash segment | T+1 settlement enforced | PRD Section 9 |

### 2.2 PRD config overrides for ₹5L run

Scale kill-switch to capital (PRD default ₹50k assumes ₹10L+ book):

```yaml
backtest:
  start_date: "2018-01-01"   # full PRD — price + PIT fundamentals from session 1
  end_date: "2026-05-31"
  initial_capital_inr: 500000.0
  price_warmup_start_date: "2016-09-01"  # ingest only; engine loop begins at start_date

risk_management:
  kill_switch_daily_loss_limit_inr: 25000  # 5% of book; PRD 50k was ~5% of 10L
```

### 2.3 Deliverables

1. **`decision_log.csv`** — every trading day × symbol: box state, filters pass/fail, `PlannedGTTAction` rows (including `NO_CHANGE`), `structural_rr`, ranking position, skip reasons.
2. **`trade_ledger.csv`** — virtual entries/exits: price, qty, stop, target, trail events, exit reason, P&L.
3. **`equity_curve.csv`** — daily equity, cash (settled/unsettled), drawdown, kill-switch state.
4. **`summary_report.json`** — CAGR, max drawdown, win rate, avg R, trades/year, sector breakdown (feeds `advisor.py` later).
5. **`data_quality_report.md`** — missing bars, symbol mapping issues, PIT gaps.

---

## 3. Data Granularity Requirements (from PRD)

The PRD defines **what resolution each feature needs**. Build only to this grid — avoid over-fetching intraday data.

| Feature | PRD section | Minimum granularity | Lookback | Notes |
|---|---|---|---|---|
| Darvas box (OHLC) | §8 | **Daily** | 280+ sessions | High/low/close for box bounds |
| 52-week high filter | §6 | **Daily** | ~252 sessions | Rolling max of highs |
| ATR(20) | §8 | **Daily** | 20 sessions | True range from daily H/L/C |
| Volume SMA(20), breakout volume | §8 | **Daily** | 20 sessions | NSE Bhavcopy `TOTTRDQTY` |
| NIFTY 50 trend filter (50/200 MA) | §8 | **Daily** | 200 sessions | Index close series |
| Sector RS tiebreaker | §9 | **Daily** | 63 sessions | Sector index or aggregated stock returns |
| Breakout trigger | §8 | **Daily close** | — | PRD allows intraday cross in backtester; **with daily-only data, use close > box_top** (document this assumption) |
| GTT virtual fill | §3 | **Daily** | — | Trigger if `high >= trigger` on session T; fill at `trigger` (+ slippage) |
| Stop / target hit | §9 | **Daily** | — | Stop if `low <= stop`; target if `high >= target` |
| `TRAIL_OCO` ratchet | §9 | **Daily** | — | Recalc `box_bottom` at EOD |
| Kill switch | §9 | **Daily** | — | EOD equity vs prior EOD |
| Fundamental screens | §6 | **Quarterly PIT** | 1–3 FY | Must use metrics **as known on date T**, not today's snapshot |
| Promoter holding % | §6 | **Quarterly PIT** | Latest filing | Not in free price feeds |
| ASM/GSM exclusion | §10 | **Daily list** | — | Cache NSE published list per session |
| Universe membership | §10 | **Semi-annual/periodic** | — | NIFTY 500 rebalances; use official archives |
| Sector classification | §9 | **Slow-changing** | — | NSE official mapping; annual refresh acceptable |

**Conclusion:** **Daily EOD is sufficient for the entire strategy engine.** No minute/tick subscription is required for PRD-faithful v1 backtesting.

---

## 4. Your Existing Subscriptions — Fit Assessment

### 4.1 Zerodha + Kite Connect

| Capability | Useful for this backtest? | Detail |
|---|---|---|
| Historical daily OHLCV API | **Yes — validation & gap-fill** | Included with Kite Connect plan (~₹500/month); historical add-on no longer extra (Feb 2025). Max **~2,000 daily candles per request** → 2 chunked calls per symbol for 10 years. |
| Corporate actions | **Yes** | Kite adjusts historical candles internally. |
| Rate limits | **Caution** | ~3 requests/sec; ~500 symbols × 2 chunks ≈ **1,000 calls** (~6–10 min one-time). Fine for ingest; poor for daily refresh of full universe. |
| Fundamentals (ROE, promoter %) | **No** | Not provided via Kite Connect. |
| NIFTY 500 historical constituents | **No** | Instrument list is **current** contracts only. |
| Live trading later | **Yes** | PRD target broker. |

**Role in plan:** secondary source after Bhavcopy ingest; primary for **split/bonus adjustment cross-check** on open positions.

### 4.2 Upstox API

| Capability | Useful for this backtest? | Detail |
|---|---|---|
| Historical daily OHLCV (V3) | **Yes — strong backup** | Daily unit: data from **Jan 2000**, up to **1 decade per request**. |
| Intraday history | **Not needed** | 1-min only back to ~2022 on V3. |
| Fundamentals | **No** | Not in standard historical candle API. |
| Analytics token (1-year) | **No** | Read-only; no orders. |

**Role in plan:** alternative one-shot downloader; dispute resolution when Bhavcopy and Kite disagree.

### 4.3 AC Agarwal

| Capability | Useful for this backtest? | Detail |
|---|---|---|
| XTS API (historical + live) | **Marginal** | Supports algo platforms; historical access is account-scoped, not a research warehouse. |
| Research desk / ACA MATH | **Human advisory** | SEBI RA (INH000023913); morning notes, RM support — **not** a machine-readable 10-year PIT database. |
| Brokerage | **Execution only** | PRD targets Kite; ACA not on critical path for backtest. |

**Role in plan:** skip for bulk historical build; optional manual spot checks only.

### 4.4 Screener.in (if premium)

| Capability | Useful for this backtest? | Detail |
|---|---|---|
| 10+ years financial statements | **Research only** | UI shows historical tables as they exist **today**; pre-computed CAGRs and ratios are **not** safe for backtest unless re-stamped with filing dates. |
| CSV/Excel export | **Cross-check only** | Can validate your NSE-derived ROE/ROCE; do **not** load exports directly into the simulator as PIT data. |
| Promoter holding, ROCE | **Cross-check** | Good sanity check against NSE shareholding-pattern XBRL. |
| Backtesting | **No** | No native PIT backtest feature. |

**Role in plan:** optional validation UI — **not** the fundamentals ingest path if you want zero forward knowledge.

### 4.5 Tijori Finance (if premium)

| Capability | Useful for this backtest? | Detail |
|---|---|---|
| Historic operational metrics | **Research UI** | Premium shows historic sector/market share screens. |
| Bulk API / PIT export | **Unlikely** | Product is portfolio + screener UX, not quant data feed. |
| Broker sync (Zerodha) | **Live portfolio** | Irrelevant to historical simulation. |

**Role in plan:** optional human validation of names; **do not depend** on it for automated 10-year loop.

---

## 5. Recommended Data Architecture

### 5.1 Tier A — Price & volume (required)

**Primary: NSE Bhavcopy (free, official)**

- **Source:** `https://www.nseindia.com` daily CM Bhavcopy (2000–present; format changed mid-2024 — handle both schemas).
- **Tools:** `jugaad-data`, `aynse`, or `bhavcopy-pipeline` (GitHub) for bulk 2000–2026 download with resume.
- **Fields used:** `SYMBOL`, `SERIES` (EQ only), `OPEN`, `HIGH`, `LOW`, `CLOSE`, `TOTTRDQTY`, `TOTTRDVAL`.
- **Storage:** Parquet or SQLite table `daily_bars(symbol, date, o, h, l, c, volume, turnover_inr)`.
- **Estimated size:** ~2,500 sessions × ~1,800 active EQ symbols × 10 years ≈ **45M rows** if storing full market; **~1.2M rows** if filtered post-download to ever-NIFTY-500 names only.

**Secondary: Kite Connect + Upstox (you have both)**

- After Bhavcopy build, sample **50 random symbols** and full **NIFTY 50** — assert OHLC agrees within ₹0.05 or 0.1%.
- Use Kite-adjusted series to build `corporate_actions` adjustment factors where Bhavcopy raw series breaks (splits).

**Not recommended as primary:** `yfinance` (PRD §10 — rate limits, gaps, weak India fundamentals).

### 5.2 Tier B — Index & universe (required)

| Dataset | Source | Method |
|---|---|---|
| NIFTY 50 daily | NSE index bhavcopy / Kite `NIFTY 50` | 200-session MA warmup — covered by price ingest from 2016-09 |
| NIFTY 500 membership over time | [Nifty Indices historical reports](https://www.niftyindices.com/reports/historical-data) → "Archives of Daily/Monthly Reports" → "Indices - Market Capitalisation & Weightage" | Download **monthly** ZIP/PDF/CSV from **2008** onward; parse symbol column; forward-fill membership between rebalance dates |
| Delisted symbols | Bhavcopy history + corporate action notes | Map old symbols (e.g. Ranbaxy → Sun) manually table |

**Survivorship bias:** Trading only names in NIFTY 500 **on date T**, including symbols that later left or delisted. Applying today's list to 2018 **overstates** results.

**Pragmatic MVP fallback:** If constituent parsing slips schedule, run **two** backtests and bracket truth: (1) survivorship-biased current-500 list — optimistic; (2) point-in-time constituents — realistic.

### 5.3 Tier C — Fundamentals PIT (required for full PRD fidelity)

Hardest layer. Required fields per PRD §6:

- Revenue growth %, EPS growth %, ROE, ROCE, debt/equity, promoter holding %, earnings calendar (for `avoid_days_before_earnings`).

**Recommended path: free official filings → local PIT warehouse.** This is the only approach that is both **$0** and **auditable** without trusting a third-party screen's current snapshot of history.

#### The PIT rule (non-negotiable)

A fundamental value may be used on simulation date `T` only if the market could have known it by then:

```
effective_date = next_trading_session_after(submission_date)
```

- Use **SUBMISSION DATE** / **BROADCAST DATE** from NSE/BSE filings — **not** `period_end` (quarter end) and **not** today's republished ratio.
- On date `T`, join: `MAX(effective_date) WHERE effective_date <= T` per `(symbol, metric)`.
- **Conservative default:** if submission time is unknown, use **submission_date + 1 trading session**. If only `period_end` is known, use **period_end + 45 calendar days** (flag as lower confidence in `data_quality_report.md`).

#### Free source #1 — NSE Financial Results XBRL (primary)

| Item | Detail |
|---|---|
| Portal | [NSE Corporate Filings → Financial Results](https://www.nseindia.com/companies-listing/corporate-filings-financial-results) and [Integrated Filing - Financials](https://www.nseindia.com/companies-listing/corporate-integrated-filing) (Mar 2025+) |
| Cost | **Free** (public regulatory disclosure) |
| PIT-safe? | **Yes** — each row has filing metadata; store `submission_date` + `xbrl_url` |
| History | **Mandatory XBRL from ~Apr 2017** (BSE circular 30-Mar-2017; NSE aligned). Pre-2017: sparse machine-readable coverage — see gap below. |
| PRD fields derived | Revenue, PAT/EPS → growth %; balance sheet → equity, debt, capital employed → **ROE, ROCE, D/E** |
| Tooling | [`nse-xbrl`](https://pypi.org/project/nse-xbrl/) (Integrated Filing parser); extend for legacy taxonomy; or download XBRL ZIPs and parse offline |

**Derived metrics (compute yourself — do not trust pre-aggregated screen ratios):**

```
ROE   = PAT / average_shareholders_equity   # prior quarter + current quarter equity
ROCE  = EBIT / average_capital_employed     # CE = total_assets - current_liabilities (document formula)
D/E   = total_debt / shareholders_equity
rev_growth_yoy = (revenue_q - revenue_q_yoy) / abs(revenue_q_yoy) * 100
eps_growth_yoy = (eps_q - eps_q_yoy) / abs(eps_q_yoy) * 100
```

#### Free source #2 — NSE Shareholding Pattern (promoter %)

| Item | Detail |
|---|---|
| Portal | [NSE Corporate Filings → Shareholding Patterns](https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern) |
| Cost | **Free** |
| PIT-safe? | **Yes** — table includes `As on Date`, `SUBMISSION DATE`, `XBRL FILE LINK` |
| Frequency | Quarterly (SEBI Reg 31) |
| PRD field | `min_promoter_holding_pct` |

Store `promoter_pct` with `effective_date` from **submission date**, not quarter-end "as on" date alone (submission is typically weeks after quarter end — using "as on" alone would leak early).

#### Free source #3 — NSE Corporate Announcements (earnings calendar)

| Item | Detail |
|---|---|
| Portal | NSE company announcements / board-meeting outcomes |
| Cost | **Free** |
| PIT-safe? | **Yes** |
| PRD field | `avoid_days_before_earnings` — block new entries when next known result date ∈ [T, T+5 sessions] |

Parse "Outcome of Board Meeting" / "Results" announcement timestamps.

#### Free source #4 — BSE Listing Centre (backup / dual-listed)

| Item | Detail |
|---|---|
| Portal | `bseindia.com` → corporate filings per scrip |
| Cost | **Free** (per-company download) |
| PIT-safe? | **Yes** with same submission-date rule |
| Use when | NSE XBRL missing for a symbol; symbol BSE-only history |

Bulk BSE corporate data is also sold via Deutsche Börse commercially — **ignore for this plan**; per-filing scrape is free but slower.

#### Free source #5 — MCA / Annual Report XBRL (optional backup)

| Item | Detail |
|---|---|
| Portal | Ministry of Corporate Affairs XBRL filings |
| Cost | **Free** |
| PIT-safe? | **Yes** with **long lag** (annual only; filed months after FY end) |
| History | Mandatory annual XBRL on BSE from **FY2018–19** onward |
| Use when | Filling rare NSE XBRL gaps for a symbol-quarter |

#### Why **2018-01-01** as full-PRD start (confirmed)

| Requirement | Satisfied from 2018? |
|---|---|
| NSE quarterly financial-results XBRL | **Yes** — mandatory from Apr 2017; by Jan 2018 several filed quarters exist per symbol |
| NSE shareholding pattern (promoter %) | **Yes** — quarterly Reg 31 filings throughout 2017 |
| YoY revenue/EPS growth | **Yes** — needs prior-year quarter; available via 2017 filings before 2018 starts |
| No price-only / partial-filter period | **Yes** — single consistent rule set for entire reported backtest |

Trade-off: ~2 fewer years vs a 2016 start (misses 2016 bull leg and 2017 pre-GST transition) in exchange for **100% quarterly PIT** with no hybrid filter schedule.

#### Sources that are free but **not** PIT-safe (avoid as ingest)

| Source | Why it leaks forward knowledge |
|---|---|
| **Screener.in** (incl. scrapers / `screener-india` npm) | Shows full history through today's lens; "10Y ROE" and CAGRs are computed with data you didn't have in 2018. |
| **Tijori / Trendlyne / Moneycontrol** | Aggregated research UI; no `effective_date` per field for backtest join. |
| **yfinance** | Sparse India fundamentals; current snapshot; unreliable promoter %. |
| **Apify Screener scraper** | Same lookahead risk as Screener unless you scrape only dated filing documents. |
| **Wikipedia / static CSVs** | Current fundamentals pasted once. |

#### PIT warehouse schema

```sql
CREATE TABLE fundamentals_pit (
    symbol           TEXT NOT NULL,
    metric           TEXT NOT NULL,  -- e.g. 'roe_pct', 'promoter_holding_pct'
    period_end       DATE,           -- quarter end (informational)
    effective_date   DATE NOT NULL,  -- first session value is knowable
    value            REAL NOT NULL,
    source           TEXT NOT NULL,  -- 'nse_xbrl', 'nse_shp', 'bse_xbrl'
    source_url       TEXT,           -- audit trail to original filing
    PRIMARY KEY (symbol, metric, effective_date)
);
```

#### Ingest pipeline (`build_pit_fundamentals.py`)

1. For each ever-NIFTY-500 symbol, fetch NSE financial-results XBRL ( **2017** –2025) + Integrated Filing (2025+).
2. Parse → store raw line items + compute derived metrics → stamp `effective_date`.
3. Fetch shareholding-pattern XBRL/CSV quarterly → `promoter_holding_pct`.
4. Fetch corp announcements → `next_earnings_date` rolling calendar.
5. QA: random 20 symbols × manual compare to Screener **for validation only**.
6. Emit `pit_coverage_report.csv`: % of symbol-quarters with each metric.

#### Paid fallback (only if free ingest blocks you)

| Vendor | Cost | When to use |
|---|---|---|
| [Genka](https://genka.dev/) | ~$29/mo | Has `?as_of=YYYY-MM-DD` on XBRL — cheap if build time > 2 weeks |
| [ftInvstr](https://ftinvstr.in/) | Free tier limited | Their backtester, not your custom engine — reference only |
| EODHD / CMIE | $–₹₹₹ | India fundamental depth uncertain vs NSE first-source |

**Phased approach:**

| Phase | Window / coverage | Goal |
|---|---|---|
| **Phase 0** (week 1–2) | Bhavcopy **2016-09 → 2026** + constituents | Price data lake + warm-up buffer |
| **Phase 1** (week 3–5) | NSE XBRL + SHP PIT (**2017 prefetch** → 2026) | `fundamentals_pit` + lookahead unit tests |
| **Phase 2** (week 6–7) | Engine smoke test (optional: 3-month slice) | Validate Darvas + PIT join before full run |
| **Phase 3** (week 8) | **Full PRD 2018-01-01 → 2026-05-31** | Decision log, trade log, equity curve |
| **Phase 4** (3–5 days) | Analysis | `summary_report.json`, advisor inputs |

### 5.4 Tier D — Reference data (required, mostly free)

| Dataset | Source | Frequency |
|---|---|---|
| NSE trading holidays | NSE annual holiday PDF | Yearly refresh |
| ASM/GSM lists | `nseindia.com/reports/asm`, `/gsm` | Cache per session (historical lists incomplete — flag pre-2015 gaps) |
| Sector mapping | NSE security classification CSV | Annual |
| Earnings dates | NSE corp announcements (free) | Per event; **not** Screener |

---

## 6. Backtest Method (PRD-Aligned)

### 6.1 Engine loop (mirror `backtester.py` + `engine.py`)

For each **trading session** `T` from `start_date` to `end_date`:

```
1. Load market slice: OHLCV for universe(T) through T (inclusive).
2. Update active_state_registry — Darvas state machine (§8).
3. Reconcile virtual portfolio vs prior ledger (corporate actions).
4. Virtual broker leg:
   a. Fill pending GTT buy triggers if high[T] >= trigger.
   b. On fill → ESTABLISH_OCO with initial stop/target (§9).
   c. Intraday path not modeled — use daily high/low for stop/target hits.
   d. Apply TRAIL_OCO ratchet using box_bottom[T] (§9).
5. Mark portfolio to market at close[T].
6. Kill switch check: equity[T-1] - equity[T] (§9); latch if tripped.
7. Run fundamental + universe filters with PIT data as_of T.
8. Generate candidate PLACE_BUY_GTT for BREAKOUT names passing filters.
9. Rank by structural_rr; apply slot/sector/cash constraints (§9).
10. Append all PlannedGTTAction rows to decision_log (including skips).
11. Persist equity_snapshot for T+1 kill switch.
```

### 6.2 Virtual execution assumptions (document in report)

| Event | Rule |
|---|---|
| Buy GTT trigger | `high >= box_top` on T → fill at `box_top × (1 + slippage)` |
| Breakout confirmation | `close > box_top` AND volume rule (§8) |
| Stop hit | `low <= stop` → exit at `stop × (1 - slippage)` |
| Target hit | `high >= target` → exit at `target × (1 - slippage)` |
| Same-bar stop & target | **Conservative:** assume stop hit first |
| T+1 cash | Sale proceeds available T+2 for new buys (CASH segment) |
| GTT not filled | Order remains working; cancel after N days (define: 30 calendar or box invalidation) |

### 6.3 Decision log schema (`decision_log.csv`)

| Column | Description |
|---|---|
| `date` | Session date T |
| `symbol` | NSE symbol |
| `box_state` | SCANNING / FORMING / VALIDATED / BREAKOUT |
| `box_top`, `box_bottom` | Active box bounds |
| `filter_pass` | bool — all universe + fundamental gates |
| `filter_fail_reason` | e.g. `ROE<15`, `ASM`, `NIFTY_TREND` |
| `structural_rr` | If candidate |
| `rank` | 1..N among day's candidates |
| `selected` | bool — received PLACE_BUY_GTT after ranking |
| `action_type` | PlannedGTTAction enum |
| `trigger_price`, `stop_loss_price`, `target_price` | If applicable |
| `quantity` | Sized per §9 |
| `skip_reason` | e.g. `MAX_POSITIONS`, `SECTOR_CAP`, `KILL_SWITCH`, `RANKED_OUT` |

### 6.4 Trade log schema (`trade_ledger.csv`)

Extends PRD §11 with backtest fields: `entry_date`, `exit_date`, `entry_price`, `exit_price`, `realized_pnl_inr`, `r_multiple`, `structural_rr_at_entry`, `exit_reason`, `max_favorable_excursion`, `max_adverse_excursion`.

---

## 7. Implementation Phases & Timeline

| Phase | Duration | Data | Output |
|---|---|---|---|
| **0 — Data lake** | 1–2 weeks | Bhavcopy **2016-09 → 2026** + NIFTY 50 + constituent parser | `daily_bars.sqlite`, quality report |
| **1 — PIT fundamentals ingest** | 2–3 weeks | NSE XBRL + SHP (**2017 prefetch**) → `fundamentals_pit` | `pit_coverage_report.csv`; lookahead tests |
| **2 — Engine smoke test** | 2–3 days | Optional Q1-2018 slice | Sanity-check before full run |
| **3 — Full PRD backtest** | 1 week | **2018-01-01 → 2026-05-31**, all filters on | Decision log, trade log, equity curve |
| **4 — Analysis** | 3–5 days | Ledger only | Summary report |

**Compute:** Local machine or EC2 `t3.large` (PRD §3.2 — not Lambda). Target: full **2018–2026** NIFTY 500 run **< 20 minutes** once data is local.

**Project layout (suggested):**

```
Swinger/
├── config.yaml              # PRD config; backtest overrides
├── data/
│   ├── raw/bhavcopy/        # ZIP archives
│   ├── processed/           # Parquet/SQLite
│   └── reference/           # holidays, sectors, constituents
├── scripts/
│   ├── ingest_bhavcopy.py
│   ├── ingest_kite_validate.py      # optional Zerodha cross-check
│   ├── ingest_nse_xbrl.py           # financial results → fundamentals_pit
│   ├── ingest_nse_shareholding.py   # promoter % → fundamentals_pit
│   ├── ingest_nse_announcements.py  # earnings calendar
│   └── run_backtest.py
└── backtest_outputs/
    ├── decision_log.csv
    ├── trade_ledger.csv
    ├── equity_curve.csv
    └── summary_report.json
```

---

## 8. Free vs Paid Source Summary

### Price (daily OHLCV) — 2016 warm-up through 2026

| Source | Cost | 10y daily? | Verdict |
|---|---|---|---|
| NSE Bhavcopy | **Free** | Yes (2000+) | **Primary** |
| Kite Connect | ~₹500/mo | Yes (chunked) | **Validate** (you have) |
| Upstox V3 API | **Free** w/ account | Yes (1 decade/call) | **Backup** (you have) |
| AC Agarwal XTS | Broker | Per-account | Skip for bulk |
| EODHD | ~$20+/mo | Yes | Optional all-in-one |
| yfinance | Free | Unreliable at scale | Avoid |

### Fundamentals (PIT) — free vs paid

| Source | Cost | PIT-safe? | Verdict |
|---|---|---|---|
| **NSE Financial Results XBRL** | **Free** | **Yes** (with submission_date rule) | **Primary ingest** (2017+) |
| **NSE Shareholding Pattern** | **Free** | **Yes** | **Primary** for promoter % |
| **NSE Corp announcements** | **Free** | **Yes** | Earnings-date filter |
| **BSE Listing Centre** | **Free** | **Yes** | Per-symbol backup |
| **MCA annual XBRL** | **Free** | **Yes** (annual lag) | Rare gap-fill only |
| Screener.in / scrapers | Paid/free | **No** (unless filing-date ETL) | Cross-check only |
| Tijori / Trendlyne / Moneycontrol | Paid/free | **No** | Research UI only |
| yfinance | Free | **No** | Avoid |
| Genka API | ~$29/mo | **Yes** (`as_of` param) | Paid shortcut if build stalls |
| EODHD / CMIE | Paid | Varies | Institutional fallback |

### Universe / survivorship

| Source | Cost | Verdict |
|---|---|---|
| Nifty Indices monthly archives | **Free** | **Required** |
| EODHD index constituents | Paid marketplace | Optional shortcut |
| Optuma NIFTY 500 history | Paid software | Optional |

---

## 9. Known Limitations (disclose in summary report)

1. **Survivorship bias** — until Tier B constituent table is complete.
2. **Fundamental lookahead** — if `as_of_date` discipline slips, results are optimistic.
3. **ASM/GSM historical** — incomplete archives before ~2015; may omit early exclusions.
4. **Daily-bar fill ambiguity** — stop/target same-bar ordering affects tail statistics.
5. **Liquidity** — Bhavcopy does not model your order impact; slippage 0.05% may be low for small caps.
6. **Regime concentration** — 2018–2026 covers 2020 COVID crash, 2022–24 range, 2024–25 rally; does not include 2016–17 bull leg.
7. **GTT modeling** — real GTT may behave differently from daily high/low touch rules.

---

## 10. Immediate Next Steps

1. **Provision price ingest** — Bhavcopy **2016-09-01 → present** (~2,400 files; resumable).
2. **Build NSE PIT warehouse** — XBRL financials from **2017** + shareholding pattern + announcements.
3. **Parse NIFTY 500 monthly constituent files** from Nifty Indices (2008+).
4. **Implement `run_backtest.py`** with `start_date: 2018-01-01`, PIT join, and lookahead regression test (below).
5. **Optional:** Kite historical pull for NIFTY 50 + 20 names to validate Bhavcopy.

#### Lookahead regression test (run before trusting results)

```python
# For random (symbol, date T) pairs: assert simulator uses only
# fundamentals_pit WHERE effective_date <= T
# Flip test: shift all effective_dates +90 days → trade count should DROP materially
```

---

## 11. Decision Record

| Decision | Choice | Rationale |
|---|---|---|
| Full-PRD backtest start | **2018-01-01** | User confirmed — clean quarterly PIT from session 1 |
| Backtest end | **2026-05-31** | Last complete Bhavcopy at run time; adjust if needed |
| Price warm-up ingest | **2016-09-01** | 280-session lookback before 2018 start |
| PIT prefetch | **2017-Q1 onward** | YoY growth + promoter % available before first trade day |
| Bar size | **Daily EOD** | PRD runs once post-close; no intraday path in v1 |
| Primary price source | **NSE Bhavcopy** | Free, official, 20+ years, no API rate pain |
| Primary fundamentals | **NSE XBRL + SHP (free)** | $0 and PIT-auditable to source filing |
| Screener / Tijori | **Cross-check only** | Not PIT-safe as direct ingest |
| Use Zerodha subscription | **Validation + corp actions** | Already paid; not bulk primary |
| Use Upstox subscription | **Backup downloader** | 10-year daily in one call/symbol |
| Use AC Agarwal subscription | **Not for backtest ingest** | Broker/advisory, not quant warehouse |
| Initial capital | **₹5,00,000** | User requirement |
| Universe | **NIFTY 500 PIT membership** | PRD MVP scope |

---

*This plan is intended to be executable by the Swinger codebase (`backtester.py`, ingest scripts) once implementation begins. Update version when data vendors or backtest window are finalized.*
