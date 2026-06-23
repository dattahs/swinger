import sqlite3
from pathlib import Path
import pandas as pd

db = sqlite3.connect(Path(r"c:\code\Swinger\data\processed\swinger_data.db"))
df = pd.read_sql(
    "SELECT date, open, high, low, close, volume FROM daily_bars "
    "WHERE symbol='VBL' AND date <= '2026-06-19' ORDER BY date",
    db,
)
df["date"] = pd.to_datetime(df["date"])
df["close_sma20"] = df["close"].rolling(20).mean()
df["vol_sma20"] = df["volume"].rolling(20).mean()
tail = df.tail(5)
print(tail[["date", "open", "close", "volume", "close_sma20", "vol_sma20"]].to_string())
print()
last3 = df.tail(3)
for _, r in last3.iterrows():
    red = r["close"] < r["open"]
    hi_vol = r["volume"] > r["vol_sma20"]
    print(f"{r['date'].date()} red={red} hi_vol={hi_vol} close={r['close']:.2f} sma={r['close_sma20']:.2f} vol={r['volume']} vol_sma={r['vol_sma20']:.0f}")
