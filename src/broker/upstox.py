"""Upstox v3 GTT REST client — REQUIREMENTS v1.2 Section 9."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.broker.base import GTTBrokerClient
from src.broker.instruments import InstrumentResolver
from src.broker.types import (
    BrokerFill,
    BrokerFunds,
    BrokerGTT,
    BrokerPosition,
    BrokerSnapshot,
    GTTStatus,
)
from src.models import ActionType, PlannedGTTAction

logger = logging.getLogger(__name__)

_TRANSIENT = (requests.Timeout, requests.ConnectionError)


class UpstoxAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class UpstoxGTTClient(GTTBrokerClient):
    """Upstox GTT client with paper (dry-run) mode."""

    def __init__(
        self,
        access_token: str,
        instruments: InstrumentResolver,
        *,
        api_base: str = "https://api.upstox.com",
        paper_mode: bool = True,
        timeout_sec: int = 30,
    ) -> None:
        self.access_token = access_token
        self.instruments = instruments
        self.api_base = api_base.rstrip("/")
        self.paper_mode = paper_mode
        self.timeout_sec = timeout_sec
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self._paper_counter = 0

    def _paper_id(self, prefix: str) -> str:
        self._paper_counter += 1
        return f"PAPER-{prefix}-{self._paper_counter:06d}"

    @retry(
        retry=retry_if_exception_type(_TRANSIENT),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        url = f"{self.api_base}{path}"
        resp = self._session.request(
            method,
            url,
            json=json_body,
            params=params,
            timeout=self.timeout_sec,
        )
        if resp.status_code >= 500 or resp.status_code == 429:
            raise requests.HTTPError(f"transient {resp.status_code}", response=resp)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}
        if resp.status_code >= 400:
            raise UpstoxAPIError(
                f"Upstox {method} {path} failed: {resp.status_code}",
                status_code=resp.status_code,
                payload=payload,
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    def fetch_snapshot(
        self,
        session_date: date,
        *,
        tracked_gtt_ids: list[str],
        symbols: list[str] | None = None,
    ) -> BrokerSnapshot:
        errors: list[str] = []
        funds = self._fetch_funds(errors)
        positions = self._fetch_positions(errors)
        gtt_orders = self._fetch_gtts(tracked_gtt_ids, errors)
        gtt_orders.extend(self._fetch_gtts_from_order_book(errors, seen={g.gtt_order_id for g in gtt_orders}))
        fills = self._fetch_fills_today(session_date, errors)
        return BrokerSnapshot(
            as_of=datetime.now(timezone.utc),
            funds=funds,
            positions=positions,
            gtt_orders=gtt_orders,
            fills_today=fills,
            errors=errors,
        )

    def _fetch_funds(self, errors: list[str]) -> BrokerFunds:
        if self.paper_mode and not self.access_token:
            return BrokerFunds(available_cash_inr=0.0)
        try:
            payload = self._request("GET", "/v2/user/get-funds-and-margin")
            data = payload.get("data", {})
            equity = data.get("equity", data)
            avail = float(
                equity.get("available_margin")
                or equity.get("available_cash")
                or equity.get("available_margin_cash")
                or 0.0
            )
            used = float(equity.get("used_margin") or 0.0)
            return BrokerFunds(available_cash_inr=avail, used_margin_inr=used, raw=data)
        except Exception as exc:  # noqa: BLE001 — collect snapshot errors
            errors.append(f"funds: {exc}")
            return BrokerFunds(available_cash_inr=0.0)

    def _fetch_positions(self, errors: list[str]) -> list[BrokerPosition]:
        if self.paper_mode and not self.access_token:
            return []
        try:
            payload = self._request("GET", "/v2/portfolio/short-term-positions")
            out: list[BrokerPosition] = []
            for row in payload.get("data", []) or []:
                qty = int(float(row.get("quantity") or row.get("net_quantity") or 0))
                if qty == 0:
                    continue
                token = row.get("instrument_token") or ""
                sym = row.get("tradingsymbol") or row.get("trading_symbol") or ""
                if not sym and token:
                    sym = self.instruments.symbol_for_token(token) or ""
                out.append(
                    BrokerPosition(
                        symbol=sym.upper(),
                        quantity=qty,
                        average_price=float(row.get("average_price") or row.get("buy_price") or 0.0),
                        product=str(row.get("product") or "D"),
                        instrument_token=token,
                        raw=row,
                    )
                )
            return out
        except Exception as exc:  # noqa: BLE001
            errors.append(f"positions: {exc}")
            return []

    def _parse_gtt_row(self, row: dict) -> BrokerGTT | None:
        gtt_id = row.get("gtt_order_id") or row.get("id")
        if not gtt_id:
            return None
        token = row.get("instrument_token") or ""
        sym = row.get("tradingsymbol") or row.get("trading_symbol") or ""
        if not sym and token:
            sym = self.instruments.symbol_for_token(token) or ""
        rules = row.get("rules") or []
        trigger = 0.0
        stop = None
        target = None
        for rule in rules:
            strat = str(rule.get("strategy", "")).upper()
            tp = float(rule.get("trigger_price") or rule.get("price") or 0.0)
            if strat == "ENTRY":
                trigger = tp
            elif strat in ("STOPLOSS", "STOP_LOSS"):
                stop = tp
            elif strat == "TARGET":
                target = tp
        status_raw = str(row.get("status") or row.get("order_status") or "UNKNOWN").upper()
        try:
            status = GTTStatus(status_raw)
        except ValueError:
            status = GTTStatus.UNKNOWN
        return BrokerGTT(
            gtt_order_id=str(gtt_id),
            symbol=sym.upper(),
            instrument_token=token,
            transaction_type=str(row.get("transaction_type") or "BUY").upper(),
            quantity=int(row.get("quantity") or 0),
            trigger_price=trigger,
            status=status,
            gtt_type=str(row.get("type") or "SINGLE"),
            stop_loss_price=stop,
            target_price=target,
            raw=row,
        )

    def _fetch_gtts(self, gtt_ids: list[str], errors: list[str]) -> list[BrokerGTT]:
        if not gtt_ids:
            return []
        out: list[BrokerGTT] = []
        for gtt_id in gtt_ids:
            if self.paper_mode and str(gtt_id).startswith("PAPER-"):
                continue
            try:
                payload = self._request("GET", "/v3/order/gtt", params={"gtt_order_id": gtt_id})
                data = payload.get("data")
                if isinstance(data, list):
                    for row in data:
                        parsed = self._parse_gtt_row(row)
                        if parsed:
                            out.append(parsed)
                elif isinstance(data, dict):
                    parsed = self._parse_gtt_row(data)
                    if parsed:
                        out.append(parsed)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"gtt {gtt_id}: {exc}")
        return out

    def _fetch_gtts_from_order_book(self, errors: list[str], seen: set[str]) -> list[BrokerGTT]:
        if self.paper_mode and not self.access_token:
            return []
        try:
            payload = self._request("GET", "/v2/order/orders")
            out: list[BrokerGTT] = []
            for row in payload.get("data", []) or []:
                gtt_id = row.get("gtt_order_id")
                if not gtt_id or gtt_id in seen:
                    continue
                order_type = str(row.get("order_type") or "").upper()
                if "GTT" not in order_type and not row.get("rules"):
                    continue
                parsed = self._parse_gtt_row(row)
                if parsed:
                    out.append(parsed)
            return out
        except Exception as exc:  # noqa: BLE001
            errors.append(f"order_book_gtt_scan: {exc}")
            return []

    def _fetch_fills_today(self, session_date: date, errors: list[str]) -> list[BrokerFill]:
        if self.paper_mode and not self.access_token:
            return []
        try:
            payload = self._request(
                "GET",
                "/v2/order/trades/get-trades-for-day",
                params={"date": session_date.isoformat()},
            )
            out: list[BrokerFill] = []
            for row in payload.get("data", []) or []:
                sym = str(row.get("tradingsymbol") or row.get("trading_symbol") or "").upper()
                out.append(
                    BrokerFill(
                        symbol=sym,
                        order_id=str(row.get("order_id") or ""),
                        trade_id=str(row.get("trade_id") or row.get("order_id") or ""),
                        transaction_type=str(row.get("transaction_type") or "").upper(),
                        quantity=int(float(row.get("quantity") or 0)),
                        price=float(row.get("price") or row.get("average_price") or 0.0),
                        product=str(row.get("product") or "D"),
                        raw=row,
                    )
                )
            return out
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fills: {exc}")
            return []

    def place_buy_gtt(self, action: PlannedGTTAction, instrument_token: str) -> str:
        body = {
            "type": "SINGLE",
            "quantity": action.quantity,
            "product": "D",
            "instrument_token": instrument_token,
            "transaction_type": "BUY",
            "rules": [
                {
                    "strategy": "ENTRY",
                    "trigger_type": "ABOVE",
                    "trigger_price": round(action.trigger_price, 2),
                }
            ],
        }
        if self.paper_mode:
            logger.info("PAPER place_buy_gtt %s %s", action.symbol, body)
            return self._paper_id("BUY")
        payload = self._request("POST", "/v3/order/gtt/place", json_body=body)
        ids = payload.get("data", {}).get("gtt_order_ids") or []
        if not ids:
            raise UpstoxAPIError("place_buy_gtt returned no gtt_order_ids", payload=payload)
        return str(ids[0])

    def cancel_gtt(self, gtt_order_id: str) -> None:
        if self.paper_mode:
            logger.info("PAPER cancel_gtt %s", gtt_order_id)
            return
        self._request(
            "DELETE",
            "/v3/order/gtt/cancel",
            json_body={"gtt_order_ids": [gtt_order_id]},
        )

    def place_oco_sell(
        self,
        symbol: str,
        instrument_token: str,
        quantity: int,
        stop_loss_price: float,
        target_price: float,
        idempotency_key: str,
    ) -> str:
        body = {
            "type": "MULTIPLE",
            "quantity": quantity,
            "product": "D",
            "instrument_token": instrument_token,
            "transaction_type": "SELL",
            "rules": [
                {
                    "strategy": "STOPLOSS",
                    "trigger_type": "IMMEDIATE",
                    "trigger_price": round(stop_loss_price, 2),
                    "order_type": "LIMIT",
                    "price": round(stop_loss_price, 2),
                },
                {
                    "strategy": "TARGET",
                    "trigger_type": "IMMEDIATE",
                    "trigger_price": round(target_price, 2),
                    "order_type": "LIMIT",
                    "price": round(target_price, 2),
                },
            ],
        }
        if self.paper_mode:
            logger.info("PAPER place_oco_sell %s %s key=%s", symbol, body, idempotency_key)
            return self._paper_id("OCO")
        payload = self._request("POST", "/v3/order/gtt/place", json_body=body)
        ids = payload.get("data", {}).get("gtt_order_ids") or []
        if not ids:
            raise UpstoxAPIError("place_oco_sell returned no gtt_order_ids", payload=payload)
        return str(ids[0])

    def modify_oco_sell(
        self,
        gtt_order_id: str,
        stop_loss_price: float,
        target_price: float,
        quantity: int,
    ) -> None:
        body = {
            "gtt_order_id": gtt_order_id,
            "quantity": quantity,
            "rules": [
                {
                    "strategy": "STOPLOSS",
                    "trigger_type": "IMMEDIATE",
                    "trigger_price": round(stop_loss_price, 2),
                    "order_type": "LIMIT",
                    "price": round(stop_loss_price, 2),
                },
                {
                    "strategy": "TARGET",
                    "trigger_type": "IMMEDIATE",
                    "trigger_price": round(target_price, 2),
                    "order_type": "LIMIT",
                    "price": round(target_price, 2),
                },
            ],
        }
        if self.paper_mode:
            logger.info("PAPER modify_oco_sell %s %s", gtt_order_id, body)
            return
        self._request("PUT", "/v3/order/gtt/modify", json_body=body)

    def planned_action_supported(self, action: PlannedGTTAction) -> bool:
        return action.action_type in (
            ActionType.PLACE_BUY_GTT,
            ActionType.CANCEL_BUY_GTT,
            ActionType.ESTABLISH_OCO,
            ActionType.TRAIL_OCO,
        )
