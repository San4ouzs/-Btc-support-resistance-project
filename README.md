# BTC Support/Resistance Levels Generator

This tool fetches recent BTC spot and futures context (Binance public endpoints), builds a **volume profile**, detects **swing highs/lows**, computes **classic daily pivots**, and produces a **confluence‑scored** table of **support/resistance** levels in an Excel file.

## What you get
- `Levels` sheet — ranked S/R levels with a score and component breakdown (POC, HVN/LVN, pivots, swings, touches, proximity).
- `VolumeProfile` — price bins and their volumes.
- `Pivots` — PP, R1/S1/R2/S2/R3/S3 from the last completed day.
- `RawSpot` — the fetched 1h candles used.
- `Context` — spot price, futures basis, funding, open interest, etc.
- `SwingHighs` / `SwingLows` — detected swing points (if any).

## Quick start
```bash
pip install -r requirements.txt
python main.py --symbol BTCUSDT --interval 1h --hours 4320 --outfile btc_support_resistance.xlsx
```
- `--symbol` must be a Binance symbol (default `BTCUSDT`).
- `--interval` e.g. `15m`, `1h`, `4h` (Binance spot klines).
- `--hours` approximate lookback when `interval=1h` (up to ~1000 most recent bars per run).
- `--outfile` Excel path to save (default `btc_support_resistance.xlsx`).

> No API keys are needed; the script uses Binance public endpoints. If you encounter rate limits, re-run after a short pause.

## Methodology (short)
1. **Spot history**: recent klines from Binance spot.
2. **Pivots**: classic PP/R1..S3 from the last completed UTC day.
3. **Volume profile**: candle volume assigned to the close-price bin (smoothed); **POC** is max-vol bin; we extract top **HVN** and **LVN**.
4. **Swings**: local maxima/minima using a symmetric lookback window.
5. **Touches**: times the candle range intersected the level ±ε (ε ~0.1%).
6. **Confluence score**: POC (+3), HVN (+2), LVN (+1), each Pivot (+1.5), Swing (+1.2), touches (0.1× per touch, capped), proximity to current price (up to +0.5). Label as **Support** if below current price; otherwise **Resistance**.

## Notes & tips
- This is a research tool. Always validate levels on your own charts.
- You can adjust the number of volume bins via `--bins` (default 140). Finer bins → more granular levels.
- For longer history than ~1000 bars, modify `fetch_klines` to paginate by `startTime` iteratively over older ranges.
- You can change scoring weights inside `confluence_levels()` to fit your style.
- Funding, basis, and OI are pulled from Binance Futures (`/premiumIndex`, `/openInterest`, `/fundingRate`) and summarized in `Context`.

## Troubleshooting
- **Empty Excel / No data**: try a coarser interval (`--interval 4h`) or run again later. Ensure internet access is allowed.
- **Rate-limited**: the script already sleeps briefly while paginating; wait and re-run.
- **Different exchange/symbol**: modify endpoints to your preferred venue.

Good luck and safe trading.
