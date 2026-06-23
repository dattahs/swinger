"""NSE symbol → Upstox instrument_token resolver."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import requests


class InstrumentResolver:
    """Maps NSE symbols to Upstox instrument_token (NSE_EQ|ISIN)."""

    def __init__(self, map_path: str | Path) -> None:
        self.map_path = Path(map_path)
        self._symbol_to_token: dict[str, str] = {}
        if self.map_path.exists():
            self._load_file()

    def _load_file(self) -> None:
        if self.map_path.suffix == ".json":
            data = json.loads(self.map_path.read_text(encoding="utf-8"))
            self._symbol_to_token = {k.upper(): v for k, v in data.items()}
            return
        with self.map_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = (row.get("symbol") or row.get("trading_symbol") or "").strip().upper()
                token = (row.get("instrument_token") or row.get("instrument_key") or "").strip()
                if sym and token:
                    self._symbol_to_token[sym] = token

    def resolve(self, symbol: str) -> str:
        token = self._symbol_to_token.get(symbol.upper())
        if not token:
            raise KeyError(
                f"No instrument_token for {symbol}. "
                f"Run scripts/download_upstox_instruments.py or add to {self.map_path}"
            )
        return token

    def symbol_for_token(self, instrument_token: str) -> str | None:
        for sym, tok in self._symbol_to_token.items():
            if tok == instrument_token:
                return sym
        return None

    def __len__(self) -> int:
        return len(self._symbol_to_token)


def download_upstox_nse_instruments(
    dest: str | Path,
    *,
    timeout: int = 120,
) -> int:
    """Download Upstox NSE EQ master and write symbol→token JSON."""
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    import gzip
    import io

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = gzip.decompress(resp.content)
    rows = json.loads(payload.decode("utf-8"))

    mapping: dict[str, str] = {}
    for row in rows:
        if row.get("segment") != "NSE_EQ":
            continue
        sym = str(row.get("trading_symbol", "")).upper()
        token = row.get("instrument_key") or row.get("instrument_token")
        if sym and token:
            mapping[sym] = token

    dest.write_text(json.dumps(mapping, indent=0, sort_keys=True), encoding="utf-8")
    return len(mapping)
