#!/usr/bin/env python3
"""Place and verify a live Upstox buy GTT (real broker API)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.broker.instruments import InstrumentResolver
from src.broker.upstox import UpstoxAPIError, UpstoxGTTClient
from src.broker.types import GTTStatus
from src.config import load_config_relaxed
from src.models import ActionType, PlannedGTTAction

logger = logging.getLogger(__name__)


def _verify_gtt(client: UpstoxGTTClient, gtt_id: str) -> dict:
    payload = client._request("GET", "/v3/order/gtt", params={"gtt_order_id": gtt_id})
    data = payload.get("data")
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Place and verify a live Upstox buy GTT")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--symbol", help="NSE symbol, e.g. HDFCBANK")
    parser.add_argument("--trigger", type=float, help="Buy GTT trigger price (INR)")
    parser.add_argument("--qty", type=int, default=1, help="Quantity (default: 1)")
    parser.add_argument("--cancel", metavar="GTT_ID", help="Cancel GTT by id")
    parser.add_argument("--verify-only", metavar="GTT_ID", help="Fetch GTT status only")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        print("ERROR: UPSTOX_ACCESS_TOKEN not set", file=sys.stderr)
        return 1

    cfg = load_config_relaxed(Path(args.config))
    live = cfg.live
    if live.paper_mode:
        print("ERROR: live.paper_mode is true — set false for real GTT placement", file=sys.stderr)
        return 1
    if "sandbox" in live.upstox_api_base.lower():
        print("ERROR: upstox_api_base points at sandbox — set https://api.upstox.com", file=sys.stderr)
        return 1

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

    print(f"Upstox API base: {live.upstox_api_base}")

    if args.cancel:
        print(f"Cancelling GTT {args.cancel} ...")
        client.cancel_gtt(args.cancel)
        print("Cancel OK")
        return 0

    if args.verify_only:
        row = _verify_gtt(client, args.verify_only)
        print(json.dumps(row, indent=2, default=str))
        return 0

    if not args.symbol or args.trigger is None:
        print("ERROR: --symbol and --trigger required unless using --verify-only", file=sys.stderr)
        return 1

    sym = args.symbol.upper()
    instrument_token = instruments.resolve(sym)
    print(f"Symbol {sym} -> {instrument_token}")

    action = PlannedGTTAction(
        symbol=sym,
        action_type=ActionType.PLACE_BUY_GTT,
        trigger_price=args.trigger,
        quantity=args.qty,
        idempotency_key=f"live-gtt-test-{sym}-{date.today().isoformat()}",
    )

    print(f"Placing BUY GTT: {sym} qty={args.qty} trigger={args.trigger} ...")
    try:
        gtt_id = client.place_buy_gtt(action, instrument_token)
    except UpstoxAPIError as exc:
        print(f"ERROR: GTT place failed: {exc}", file=sys.stderr)
        if exc.payload:
            print(json.dumps(exc.payload, indent=2), file=sys.stderr)
        return 1

    print(f"GTT placed: {gtt_id}")
    print("Verifying with GET /v3/order/gtt ...")

    try:
        row = _verify_gtt(client, gtt_id)
    except UpstoxAPIError as exc:
        print(f"WARN: placed {gtt_id} but verify failed: {exc}", file=sys.stderr)
        return 1

    status = str(row.get("status") or row.get("order_status") or "").upper()
    rules = row.get("rules") or []
    rule_statuses = [str(r.get("status") or "").upper() for r in rules]
    trigger = next(
        (r.get("trigger_price") for r in rules if str(r.get("strategy", "")).upper() == "ENTRY"),
        None,
    )
    qty = row.get("quantity")
    if not status and rule_statuses:
        status = rule_statuses[0]
    print(f"Verified GTT {gtt_id}")
    print(f"  status:   {status}")
    print(f"  symbol:   {row.get('trading_symbol') or row.get('tradingsymbol') or sym}")
    print(f"  qty:      {qty}")
    print(f"  trigger:  {trigger}")
    print(json.dumps(row, indent=2, default=str))

    ok_statuses = {
        GTTStatus.ACTIVE.value,
        GTTStatus.TRIGGERED.value,
        "OPEN",
        "PENDING",
        "SCHEDULED",
    }
    if status in ok_statuses or any(rs in ok_statuses for rs in rule_statuses):
        print("RESULT: SUCCESS — GTT is live at Upstox")
        return 0
    print(f"RESULT: UNEXPECTED STATUS {status}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
