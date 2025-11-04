#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC Support/Resistance Levels Generator
--------------------------------------
Fetches recent BTC spot & futures context (Binance), builds a volume profile,
finds swing highs/lows and daily pivots, and outputs a confluence-scored table
of support/resistance levels to an Excel file (multi-sheet).

No API keys required. Uses public endpoints.
"""
import argparse
import math
import time
import sys
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any
import requests
import pandas as pd
import numpy as np
from pandas import Timestamp

BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUT = "https://fapi.binance.com"

def fetch_klines(symbol: str, interval: str, limit: int = 1000, start_ms: int = None, end_ms: int = None) -> list:
    url = f"{BINANCE_SPOT}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(1000, limit)}
    if start_ms is not None: params["startTime"] = start_ms
    if end_ms is not None: params["endTime"] = end_ms
    out = []
    while True:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        out.extend(data)
        if len(data) < params["limit"]:
            break
        params["startTime"] = data[-1][0] + 1
        if len(out) >= limit:
            break
        time.sleep(0.15)
    return out[:limit]

def klines_to_df(raw: list) -> pd.DataFrame:
    cols = ["open_time","open","high","low","close","volume","close_time","quote_asset_volume","num_trades",
            "taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(raw, columns=cols)
    for c in ["open","high","low","close","volume","quote_asset_volume","taker_buy_base","taker_buy_quote"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df

def fetch_premium_index(symbol: str) -> dict:
    url = f"{BINANCE_FUT}/fapi/v1/premiumIndex"
    r = requests.get(url, params={"symbol": symbol}, timeout=20); r.raise_for_status()
    return r.json()

def fetch_open_interest(symbol: str) -> dict:
    url = f"{BINANCE_FUT}/fapi/v1/openInterest"
    r = requests.get(url, params={"symbol": symbol}, timeout=20); r.raise_for_status()
    return r.json()

def fetch_latest_funding_rate(symbol: str, limit: int = 30) -> pd.DataFrame:
    url = f"{BINANCE_FUT}/fapi/v1/fundingRate"
    r = requests.get(url, params={"symbol": symbol, "limit": min(1000, limit)}, timeout=20); r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        return df
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    for c in ["fundingRate"]:
        df[c] = df[c].astype(float)
    return df.sort_values("fundingTime")

def resample_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    d = df.set_index("close_time").sort_index()
    daily = pd.DataFrame({
        "high": d["high"].resample("1D").max(),
        "low": d["low"].resample("1D").min(),
        "close": d["close"].resample("1D").last()
    }).dropna()
    return daily

def classic_pivots(prev_day_high: float, prev_day_low: float, prev_day_close: float) -> Dict[str, float]:
    PP = (prev_day_high + prev_day_low + prev_day_close) / 3.0
    R1 = 2*PP - prev_day_low
    S1 = 2*PP - prev_day_close if False else 2*PP - prev_day_high  # classic uses high
    # Fix S1 classic:
    S1 = 2*PP - prev_day_high
    R2 = PP + (prev_day_high - prev_day_low)
    S2 = PP - (prev_day_high - prev_day_low)
    R3 = prev_day_high + 2*(PP - prev_day_low)
    S3 = prev_day_low - 2*(prev_day_high - PP)
    return {"PP": PP, "R1": R1, "S1": S1, "R2": R2, "S2": S2, "R3": R3, "S3": S3}

def atr(df: pd.DataFrame, period: int = 14) -> float:
    # Use 1h candles; compute ATR in price units
    high = df["high"].values
    low = df["low"].values
    close = df["close"].shift(1).fillna(df["close"]).values
    tr = np.maximum(high - low, np.maximum(np.abs(high - close), np.abs(low - close)))
    return float(pd.Series(tr).rolling(period).mean().dropna().iloc[-1])

def build_volume_profile(df: pd.DataFrame, bin_size: float) -> pd.DataFrame:
    # Approximate: assign each candle's *base* volume to the close price bin
    prices = df["close"].values
    vols = df["volume"].values
    if len(prices) == 0:
        return pd.DataFrame(columns=["price","volume"])
    price_min, price_max = float(np.min(prices)), float(np.max(prices))
    # Guard bin_size
    if bin_size <= 0:
        bin_size = max(1.0, (price_max - price_min) / 200.0)
    bins = np.arange(math.floor(price_min/bin_size)*bin_size, math.ceil(price_max/bin_size)*bin_size + bin_size, bin_size)
    idx = np.clip(((prices - bins[0]) / bin_size).astype(int), 0, len(bins)-2)
    hist = np.zeros(len(bins)-1, dtype=float)
    for i, v in zip(idx, vols):
        hist[i] += v
    centers = (bins[:-1] + bins[1:]) / 2.0
    vp = pd.DataFrame({"price": centers, "volume": hist})
    vp = vp.sort_values("price").reset_index(drop=True)
    return vp

def find_hvn_lvn(vp: pd.DataFrame, n_hvn: int = 7, n_lvn: int = 7) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if vp.empty:
        return vp, vp
    # Smooth a bit to reduce noise
    v = vp["volume"].rolling(3, min_periods=1).mean()
    # Local extrema
    hvn_idx = np.argpartition(v.values, -min(n_hvn, len(v)))[-min(n_hvn, len(v)):] if len(v) else []
    hvn = vp.iloc[sorted(hvn_idx)].copy()
    # LVN: local minima - choose low quantiles
    q = v.quantile(0.2)
    lvn = vp[v <= q].copy().head(n_lvn)
    hvn["kind"] = "HVN"
    lvn["kind"] = "LVN"
    return hvn.sort_values("price"), lvn.sort_values("price")

def swing_points(df: pd.DataFrame, lookback: int = 10) -> Tuple[pd.DataFrame, pd.DataFrame]:
    highs = df["high"].values
    lows = df["low"].values
    swing_highs = []
    swing_lows = []
    for i in range(lookback, len(df)-lookback):
        window_h = highs[i-lookback:i+lookback+1]
        window_l = lows[i-lookback:i+lookback+1]
        if highs[i] == window_h.max() and (highs[i] > window_h[:-1].max()) and (highs[i] > window_h[1:].max()):
            swing_highs.append((df.iloc[i]["close_time"], highs[i]))
        if lows[i] == window_l.min() and (lows[i] < window_l[:-1].min()) and (lows[i] < window_l[1:].min()):
            swing_lows.append((df.iloc[i]["close_time"], lows[i]))
    sh = pd.DataFrame(swing_highs, columns=["time","price"])
    sl = pd.DataFrame(swing_lows, columns=["time","price"])
    return sh, sl

def touches(df: pd.DataFrame, level: float, eps: float) -> int:
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    return int(np.sum((l <= level+eps) & (h >= level-eps)))

def confluence_levels(
    price_now: float,
    vp: pd.DataFrame,
    poc_price: float,
    hvn: pd.DataFrame,
    lvn: pd.DataFrame,
    pivots: Dict[str, float],
    swing_highs: pd.DataFrame,
    swing_lows: pd.DataFrame,
    hist_df: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    # Candidate set
    candidates = []
    if not np.isnan(poc_price):
        candidates.append(("POC", poc_price))
    for _, r in hvn.iterrows():
        candidates.append(("HVN", float(r["price"])))
    for _, r in lvn.iterrows():
        candidates.append(("LVN", float(r["price"])))
    for k, v in pivots.items():
        candidates.append((k, float(v)))
    for _, r in swing_highs.iterrows():
        candidates.append(("SwingHigh", float(r["price"])))
    for _, r in swing_lows.iterrows():
        candidates.append(("SwingLow", float(r["price"])))

    # Deduplicate by price proximity
    candidates = sorted(candidates, key=lambda x: x[1])
    merged = []
    tol = max(1.0, price_now * 0.0015)  # ~0.15%
    for kind, p in candidates:
        placed = False
        for m in merged:
            if abs(m["price"] - p) <= tol:
                m["tags"].add(kind)
                m["source_prices"].append(p)
                placed = True
                break
        if not placed:
            merged.append({"price": p, "tags": set([kind]), "source_prices": [p]})

    # Score
    eps_touch = max(1.0, price_now * 0.001)  # touch band
    for m in merged:
        tags = m["tags"]
        price = float(np.mean(m["source_prices"]))
        score = 0.0
        details = []

        if "POC" in tags: score += 3.0; details.append("POC +3")
        if "HVN" in tags: score += 2.0; details.append("HVN +2")
        if "LVN" in tags: score += 1.0; details.append("LVN +1")
        for pv_name in ["PP","R1","S1","R2","S2","R3","S3"]:
            if pv_name in tags: score += 1.5; details.append(f"{pv_name} +1.5")
        if "SwingHigh" in tags: score += 1.2; details.append("SwingHigh +1.2")
        if "SwingLow" in tags: score += 1.2; details.append("SwingLow +1.2")

        t = touches(hist_df, price, eps_touch)
        score += min(2.0, 0.1 * t)
        details.append(f"Touches {t} ×0.1")

        # Proximity to current price (closer gets slight boost)
        prox = max(0.0, 1.0 - abs(price - price_now) / max(1.0, 0.02*price_now))
        score += 0.5 * prox
        details.append(f"Proximity {prox:.2f} ×0.5")

        level_type = "Resistance" if price > price_now else "Support"
        rows.append({
            "price": round(price, 2),
            "type": level_type,
            "score": round(score, 3),
            "components": ", ".join(sorted(list(tags))),
            "details": " | ".join(details)
        })
    levels = pd.DataFrame(rows).sort_values(["score","price"], ascending=[False, True]).reset_index(drop=True)
    return levels

def main():
    parser = argparse.ArgumentParser(description="Generate BTC Support/Resistance levels and export to Excel")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol (Binance)")
    parser.add_argument("--interval", default="1h", help="Spot kline interval (e.g., 15m, 1h, 4h)")
    parser.add_argument("--hours", type=int, default=24*180, help="Number of hours of history to fetch (approximate)")
    parser.add_argument("--outfile", default="btc_support_resistance.xlsx", help="Output Excel filename")
    parser.add_argument("--bins", type=int, default=140, help="Approx number of price bins for volume profile")
    args = parser.parse_args()

    # Fetch spot history
    print("Fetching spot klines...")
    kl_raw = fetch_klines(args.symbol, args.interval, limit=min(1000, args.hours if args.interval=='1h' else 1000))
    df = klines_to_df(kl_raw)
    if df.empty:
        raise SystemExit("No kline data returned. Try a different interval or run later.")

    # Ensure we have enough history by looping backwards in time if possible
    # (Binance allows pagination via startTime; simple approach used above should suffice for most recent 1000 bars)

    price_now = float(df["close"].iloc[-1])
    # Daily pivots from last completed day
    daily = resample_to_daily(df)
    if len(daily) < 2:
        raise SystemExit("Not enough daily data for pivots.")
    prev = daily.iloc[-2]
    pivots = classic_pivots(prev.high, prev.low, prev.close)

    # Futures context
    print("Fetching futures context (premium index, OI, funding)...")
    prem = fetch_premium_index(args.symbol)
    oi = fetch_open_interest(args.symbol)
    fund = fetch_latest_funding_rate(args.symbol, limit=30)

    index_price = float(prem.get("indexPrice")) if prem.get("indexPrice") is not None else price_now
    mark_price = float(prem.get("markPrice")) if prem.get("markPrice") is not None else price_now
    basis = (mark_price - index_price) / index_price if index_price else 0.0
    last_funding = float(prem.get("lastFundingRate")) if prem.get("lastFundingRate") is not None else (fund["fundingRate"].iloc[-1] if not fund.empty else 0.0)
    open_interest = float(oi.get("openInterest")) if oi.get("openInterest") is not None else np.nan

    # Volume profile
    print("Building volume profile...")
    price_range = df["close"].max() - df["close"].min()
    approx_bin_size = max(1.0, price_range / max(20, args.bins))
    vp = build_volume_profile(df, approx_bin_size)
    poc_row = vp.iloc[vp["volume"].idxmax()] if not vp.empty else pd.Series({"price": np.nan})
    poc_price = float(poc_row["price"]) if not vp.empty else float("nan")

    hvn, lvn = find_hvn_lvn(vp, n_hvn=12, n_lvn=12)

    # Swings
    sh, sl = swing_points(df, lookback=10)

    # Confluence
    levels = confluence_levels(price_now, vp, poc_price, hvn, lvn, pivots, sh, sl, df)

    # Context sheet
    context = pd.DataFrame([{
        "timestamp_utc": pd.Timestamp.utcnow(),
        "spot_price": price_now,
        "index_price": index_price,
        "mark_price": mark_price,
        "basis_pct": basis * 100.0,
        "last_funding_rate_8h": last_funding * 100.0,
        "open_interest_coins": open_interest,
        "interval": args.interval,
        "bars": len(df),
        "volume_profile_bins": len(vp)
    }])

    # Pivots sheet
    piv_df = pd.DataFrame([pivots])

    # Export
    out = args.outfile
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        levels.to_excel(writer, index=False, sheet_name="Levels")
        vp.to_excel(writer, index=False, sheet_name="VolumeProfile")
        piv_df.to_excel(writer, index=False, sheet_name="Pivots")
        df.to_excel(writer, index=False, sheet_name="RawSpot")
        context.to_excel(writer, index=False, sheet_name="Context")
        if not sh.empty: sh.to_excel(writer, index=False, sheet_name="SwingHighs")
        if not sl.empty: sl.to_excel(writer, index=False, sheet_name="SwingLows")

    print(f"Saved: {out}")

if __name__ == "__main__":
    main()
