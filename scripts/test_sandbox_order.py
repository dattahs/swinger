#!/usr/bin/env python3
"""Smoke-test Upstox sandbox API — places a non-executing GTT or v3 order."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from src.broker.instruments import InstrumentResolver
from src.broker.upstox import UpstoxAPIError, UpstoxGTTClient
from src.config import load_config_relaxed
from src.models import ActionType, PlannedGTTAction

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "RELIANCE"


def _place_v3_market_buy(client: UpstoxGTTClient, instrument_token: str, qty: int = 1) -> dict:
    body = {
        "quantity": qty,
        "product": "D",
        "validity": "DAY",
        "price": 0,
        "instrument_token": instrument_token,
        "order_type": "MARKET",
        "transaction_type": "BUY",
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False,
    }
    return client._request("POST", "/v3/order/place", json_body=body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upstox sandbox order smoke test")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--cancel", metavar="GTT_ID", help="Cancel a sandbox GTT by id")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        print("ERROR: UPSTOX_ACCESS_TOKEN not set (add to shared/.env on VPS)", file=sys.stderr)
        return 1

    cfg = load_config_relaxed(Path(args.config))
    live = cfg.live
    instruments = InstrumentResolver(
        Path(live.instrument_map_path)
        if Path(live.instrument_map_path).is_absolute()
        else ROOT / live.instrument_map_path
    )

    client = UpstoxGTTClient(
        access_token=token,
        instruments=instruments,
        api_base=live.upstox_api_base,
        paper_mode=False,
        timeout_sec=live.api_timeout_sec,
    )

    api_base = live.upstox_api_base
    print(f"Upstox API base: {api_base}")

    if args.cancel:
        print(f"Cancelling GTT {args.cancel} ...")
        client.cancel_gtt(args.cancel)
        print("Cancel OK")
        return 0

    sym = args.symbol.upper()
    instrument_token = instruments.resolve(sym)
    if not instrument_token:
        print(f"ERROR: no instrument token for {sym}", file=sys.stderr)
        return 1
    print(f"Symbol {sym} -> {instrument_token}")

    # High trigger — sandbox GTT should accept without market fill.
    action = PlannedGTTAction(
        symbol=sym,
        action_type=ActionType.PLACE_BUY_GTT,
        trigger_price=99_999.0,
        quantity=1,
        idempotency_key=f"sandbox-test-{date.today().isoformat()}",
    )

    print("Attempting sandbox GTT place (trigger=99999, qty=1) ...")
    gtt_err: str | None = None
    try:
        gtt_id = client.place_buy_gtt(action, instrument_token)
        print(f"GTT place OK: {gtt_id}")
        print(f"To cancel: python scripts/test_sandbox_order.py --config {args.config} --cancel {gtt_id}")
        return 0
    except (UpstoxAPIError, requests.HTTPError, requests.RequestException) as exc:
        gtt_err = str(exc)
        print(f"GTT not available on sandbox ({gtt_err}) — trying v3 MARKET place ...")

    try:
        payload = _place_v3_market_buy(client, instrument_token)
        order_id = (payload.get("data") or {}).get("order_id") or payload
        print(f"v3 place OK: {order_id}")
        return 0
    except UpstoxAPIError as exc:
        print(f"ERROR: sandbox order failed: {exc}", file=sys.stderr)
        if exc.payload:
            print(f"Response: {exc.payload}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
