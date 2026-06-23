"""Progress and action-based debug logging for backtests."""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.config import DebugLogConfig, ProgressLogConfig


def humanize_filter_reason(code: str | None) -> str:
    if not code:
        return "Filter check failed"
    mapping = {
        "NO_BARS": "No price bars available for this date",
        "PRICE_TOO_LOW": "Stock price below minimum threshold",
        "VOLUME_TOO_LOW": "Daily volume below minimum threshold",
        "TURNOVER_TOO_LOW": "Daily turnover below minimum threshold (INR Cr)",
        "ASM_GSM": "Symbol is on ASM/GSM surveillance list",
        "NO_FUNDAMENTALS": "No point-in-time fundamentals available",
        "DE_TOO_HIGH": "Debt-to-equity above maximum",
        "EARNINGS_BLACKOUT": "Within earnings announcement blackout window",
        "LTG_GROUP": "Failed long-term EPS growth group requirement",
        "NO_BOX": "Breakout state but box top/bottom missing",
        "NO_52WK_HIGH": "No new 52-week high on this session",
        "TREND_FAIL": "Symbol or sector trend filter failed",
        "BREAKOUT_VOLUME_LOW": "Price above box top but volume below breakout threshold",
        "STALE_BARS": "Latest price bar is not for this session date",
        "PRICE_BELOW_SMA": "Close below SMA — weak price momentum",
        "ENTRY_SMA_HISTORY": "Insufficient bars for entry SMA check",
        "DISTRIBUTION_ANTIPATTERN": "Two consecutive post-breakout red sessions on high volume",
    }
    if code in mapping:
        return mapping[code]
    if "<" in code:
        metric, threshold = code.split("<", 1)
        labels = {
            "REV_GROWTH": "Revenue growth",
            "EPS_GROWTH": "EPS growth",
            "ROE": "ROE",
            "ROCE": "ROCE",
            "PROMOTER": "Promoter holding",
        }
        label = labels.get(metric, metric)
        return f"{label} below minimum ({threshold}%)"
    return code.replace("_", " ").lower()


def humanize_skip_reason(code: str) -> str:
    mapping = {
        "STRUCTURAL_R_BELOW_MIN": "Structural risk-reward below minimum",
        "KILL_SWITCH": "Kill switch active — no new entries",
        "MAX_POSITIONS": "Maximum concurrent positions reached",
        "INSUFFICIENT_CASH": "Insufficient settled cash for position size",
        "SECTOR_CAP": "Sector exposure cap would be exceeded",
        "RANKED_OUT": "Lower rank than selected candidates",
        "BOX_RESET_REQUIRED": "Re-entry blocked until box resets to SCANNING",
    }
    return mapping.get(code, code.replace("_", " ").lower())


@dataclass
class ProgressLogger:
    """Session-level heartbeat logs (tail-friendly, flushed each line)."""

    cfg: ProgressLogConfig
    log_path: Path | None = None
    _file: Any = None
    _csv: Any = None

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def open(self, log_path: Path) -> None:
        if not self.enabled:
            return
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = log_path.open("w", encoding="utf-8", newline="", buffering=1)
        self._csv = csv.writer(self._file)
        self._csv.writerow(
            ["timestamp", "session_date", "day_num", "total_days", "equity_inr", "open_positions", "message"]
        )
        self._file.flush()
        self._emit_console(f"Progress log: {log_path}")

    def session(
        self,
        session_date: date,
        day_num: int,
        total_days: int,
        equity: float,
        open_positions: int,
        *,
        universe_size: int = 0,
        breakout_count: int = 0,
        buy_actions: int = 0,
        closed_trades: int = 0,
        elapsed_sec: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        pct = 100.0 * day_num / total_days if total_days else 0.0
        elapsed = f" | elapsed {elapsed_sec:.0f}s" if elapsed_sec is not None else ""
        message = (
            f"Day {day_num}/{total_days} ({pct:.1f}%) | universe={universe_size} "
            f"| breakouts={breakout_count} | buy_gtt={buy_actions} "
            f"| closed_trades={closed_trades}{elapsed}"
        )
        row = [
            datetime.now().isoformat(timespec="seconds"),
            session_date.isoformat(),
            day_num,
            total_days,
            round(equity, 2),
            open_positions,
            message,
        ]
        if self._csv:
            self._csv.writerow(row)
            self._file.flush()
        self._emit_console(f"{session_date} | {message} | equity={equity:,.0f}")

    def info(self, message: str) -> None:
        if not self.enabled:
            return
        if self._csv:
            self._csv.writerow([datetime.now().isoformat(timespec="seconds"), "", "", "", "", "", message])
            self._file.flush()
        self._emit_console(message)

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self._csv = None

    def _emit_console(self, message: str) -> None:
        if self.cfg.log_to_console:
            print(message, file=sys.stderr, flush=True)


class ActionDebugLogger:
    """Detailed action-based strategy logs for post-run analysis."""

    def __init__(self, cfg: DebugLogConfig) -> None:
        self.cfg = cfg
        self.log_path: Path | None = None
        self._file: Any = None
        self._csv: Any = None

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def open(self, log_path: Path) -> None:
        if not self.enabled:
            return
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = log_path.open("w", encoding="utf-8", newline="", buffering=1)
        self._csv = csv.writer(self._file)
        self._csv.writerow(["timestamp", "date", "symbol", "category", "action", "message", "details_json"])
        self._file.flush()

    def log(
        self,
        session_date: date,
        category: str,
        action: str,
        message: str,
        *,
        symbol: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        row = [
            datetime.now().isoformat(timespec="seconds"),
            session_date.isoformat(),
            symbol or "",
            category,
            action,
            message,
            json.dumps(details, default=str) if details else "",
        ]
        if self._csv:
            self._csv.writerow(row)
            self._file.flush()
        if self.cfg.log_to_console:
            sym = f" {symbol}" if symbol else ""
            print(f"{session_date} |{sym} | {category} | {action} | {message}", file=sys.stderr, flush=True)

    def session_start(
        self,
        session_date: date,
        *,
        trend_ok: bool,
        universe_size: int,
        index_close: float | None = None,
        trend_mode: str = "nifty",
        sector_trend_bullish: int | None = None,
    ) -> None:
        if trend_mode == "sector_index":
            bullish = sector_trend_bullish if sector_trend_bullish is not None else 0
            msg = (
                f"Session open — sector-index trend mode, "
                f"{bullish}/{universe_size} symbols bullish, universe {universe_size} symbols"
            )
            details: dict[str, Any] = {
                "trend_mode": trend_mode,
                "sector_trend_bullish": bullish,
                "universe_size": universe_size,
                "nifty_trend_ok": trend_ok,
            }
        else:
            trend = "bullish" if trend_ok else "bearish"
            msg = f"Session open — NIFTY trend {trend}, universe {universe_size} symbols"
            details = {"trend_mode": trend_mode, "trend_ok": trend_ok, "universe_size": universe_size}
        if index_close is not None:
            details["index_close"] = index_close
        self.log(session_date, "SESSION", "OPEN", msg, details=details)

    def box_transition(
        self,
        session_date: date,
        symbol: str,
        from_state: str,
        to_state: str,
        message: str,
        **details: Any,
    ) -> None:
        if not self.cfg.include_box_transitions and to_state in ("SCANNING", "FORMING"):
            return
        self.log(
            session_date,
            "BOX",
            "TRANSITION",
            f"{symbol}: {from_state} → {to_state} — {message}",
            symbol=symbol,
            details={"from": from_state, "to": to_state, **details},
        )

    def consider_breakout(self, session_date: date, symbol: str, box_top: float, box_bottom: float) -> None:
        self.log(
            session_date,
            "BREAKOUT",
            "CONSIDER",
            f"Considered {symbol} based on Darvas box breakout "
            f"(top={box_top:.2f}, bottom={box_bottom:.2f})",
            symbol=symbol,
            details={"box_top": box_top, "box_bottom": box_bottom},
        )

    def reject(
        self,
        session_date: date,
        symbol: str,
        category: str,
        reason_code: str,
        message: str | None = None,
        **details: Any,
    ) -> None:
        self.log(
            session_date,
            category,
            "REJECT",
            message or humanize_filter_reason(reason_code),
            symbol=symbol,
            details={"reason_code": reason_code, **details},
        )

    def select(self, session_date: date, symbol: str, rank: int, structural_rr: float, quantity: int) -> None:
        self.log(
            session_date,
            "RANK",
            "SELECT",
            f"Selected {symbol} at rank {rank} (structural_rr={structural_rr:.4f}, qty={quantity})",
            symbol=symbol,
            details={"rank": rank, "structural_rr": structural_rr, "quantity": quantity},
        )

    def broker(self, session_date: date, symbol: str, action: str, message: str, **details: Any) -> None:
        self.log(session_date, "BROKER", action, message, symbol=symbol, details=details or None)

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self._csv = None
