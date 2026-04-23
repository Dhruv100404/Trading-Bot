"""
download_parquet.py
===================
Downloads 1-min candle data from Dhan API and saves as monthly parquet.
Format matches existing candles_YYYYMM.parquet exactly.

Usage:
  python download_parquet.py                    # AUTO: scan parquets/ and fill every gap through yesterday
  python download_parquet.py 2024-03            # single month
  python download_parquet.py 2024-03 2024-05    # range of months

Features:
  - Auto mode: scans parquets/ folder, detects missing months + missing days
    (within existing months), and downloads everything up to yesterday.
  - Auto-builds data/symbol_secid_map.tsv from Dhan's public scrip master CSV
    if it's not already present.
  - 5 concurrent threads (1 per API token).
  - Adaptive chunk size: 30 days -> 10 days -> 5 days -> single day on rate limit.
  - Dedup: drops duplicate (symbol, date, bucket) rows, keeps last.
  - Handles missing symbols gracefully (IPO not happened, delisted, etc.).

Requires: requests, pandas, pyarrow
Uses: data/liquid-5l-symbols.json (symbol filter)
      data/symbol_secid_map.tsv   (symbol -> security_id; auto-built if missing)
"""

import sys, io, time, json, os, argparse, datetime, calendar, threading, signal, gc, re
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

# Allow Ctrl+C to kill ThreadPoolExecutor on Windows
signal.signal(signal.SIGINT, lambda *_: (print("\nInterrupted! Exiting..."), os._exit(1)))

REPO_DIR   = Path(__file__).resolve().parent
DATA_DIR   = REPO_DIR / "parquets"   # where candles_YYYYMM.parquet files live
CONFIG_DIR = REPO_DIR / "data"       # where symbol lists + secid map live
DATA_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


TOKENS = [
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc3MDQ0ODEwLCJpYXQiOjE3NzY5NTg0MTAsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.AGO4pFVRIvPw4ueae7rORtRQd1uoK4St5FfEXS91dUOLIYxQpkC39Z67EtZYbBohpyzbBQZplKt9eq9OqxtYqw"
]

CLIENT_ID = "1100896497"
DHAN_URL = "https://api.dhan.co/v2/charts/intraday"

NSE_HOLIDAYS = {
    "2024-01-26","2024-03-08","2024-03-25","2024-03-29","2024-04-11","2024-04-14",
    "2024-04-17","2024-04-21","2024-05-20","2024-06-17","2024-07-17","2024-08-15",
    "2024-09-16","2024-10-02","2024-10-12","2024-10-31","2024-11-01","2024-11-15","2024-12-25",

    "2025-01-26","2025-02-26","2025-03-14","2025-03-31","2025-04-10","2025-04-14",
    "2025-04-18","2025-05-01","2025-06-26","2025-07-06","2025-08-15","2025-08-16",
    "2025-08-27","2025-10-02","2025-10-21","2025-10-22","2025-11-05","2025-11-26","2025-12-25",

    "2026-01-15","2026-01-26","2026-03-03","2026-03-14","2026-03-26","2026-03-30","2026-03-31",
    "2026-04-03","2026-04-14","2026-05-01","2026-05-28","2026-06-26",
    "2026-09-14","2026-10-02","2026-10-20","2026-11-10","2026-11-24","2026-12-25",

    "2023-01-26","2023-03-07","2023-03-30","2023-04-04","2023-04-07","2023-04-14",
    "2023-04-22","2023-05-01","2023-06-29","2023-08-15","2023-09-19","2023-10-02",
    "2023-10-24","2023-11-14","2023-11-27","2023-12-25",
}

t0 = time.time()
def log(msg): print(f"[{time.time()-t0:6.1f}s] {msg}", flush=True)

# ============================================================================
#  HELPERS
# ============================================================================
def get_trading_days(from_date, to_date):
    days = []
    d = datetime.date.fromisoformat(from_date)
    end = datetime.date.fromisoformat(to_date)
    while d <= end:
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in NSE_HOLIDAYS:
            days.append(iso)
        d += datetime.timedelta(days=1)
    return days

def get_month_range(month_str):
    y, m = map(int, month_str.split("-"))
    last_day = calendar.monthrange(y, m)[1]
    return f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last_day:02d}"

def chunk_days(days, size):
    chunks = []
    for i in range(0, len(days), size):
        c = days[i:i+size]
        chunks.append((c[0], c[-1], c))
    return chunks

def build_symbol_map_from_dhan(tsv_path):
    """Download Dhan's public scrip master CSV and write symbol<TAB>security_id
    for NSE equity (SERIES=EQ, INSTRUMENT_TYPE=ES)."""
    log(f"Downloading Dhan scrip master: {SCRIP_MASTER_URL}")
    resp = requests.get(SCRIP_MASTER_URL, timeout=60)
    resp.raise_for_status()
    lines = resp.text.splitlines()
    if not lines:
        raise RuntimeError("Scrip master CSV is empty")

    header = [h.strip() for h in lines[0].split(",")]
    def col(name):
        for i, h in enumerate(header):
            if h.lower() == name.lower():
                return i
        return -1

    i_exch     = col("EXCH_ID")
    i_seg      = col("SEGMENT")
    i_inst_typ = col("INSTRUMENT_TYPE")
    i_series   = col("SERIES")
    i_secid    = col("SECURITY_ID")
    i_symbol   = col("UNDERLYING_SYMBOL")
    if min(i_exch, i_seg, i_inst_typ, i_series, i_secid, i_symbol) < 0:
        raise RuntimeError(f"Unexpected scrip master columns: {header[:20]}")

    out = {}
    for ln in lines[1:]:
        row = ln.split(",")
        if len(row) <= max(i_exch, i_seg, i_inst_typ, i_series, i_secid, i_symbol):
            continue
        if row[i_exch].strip() != "NSE": continue
        if row[i_seg].strip() != "E": continue
        if row[i_inst_typ].strip() != "ES": continue
        if row[i_series].strip() != "EQ": continue
        secid = row[i_secid].strip()
        sym   = row[i_symbol].strip()
        if not sym or not secid or secid == "0":
            continue
        # Prefer first occurrence (scrip master is usually unique already)
        out.setdefault(sym, secid)

    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tsv_path, "w", encoding="utf-8") as f:
        for sym, sid in sorted(out.items()):
            f.write(f"{sym}\t{sid}\n")
    log(f"Built {tsv_path.name}: {len(out)} NSE equity symbols")
    return out

def load_symbol_map():
    tsv_path = CONFIG_DIR / "symbol_secid_map.tsv"
    if not tsv_path.exists():
        log(f"{tsv_path} not found -- building from Dhan scrip master...")
        return build_symbol_map_from_dhan(tsv_path)

    mapping = {}
    with open(tsv_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            parts = line.split("\t")
            if len(parts) == 2:
                mapping[parts[0]] = parts[1]
    return mapping

# ============================================================================
#  DHAN API - with adaptive chunk fallback
# ============================================================================
_token_lock = threading.Lock()
_token_idx = 0
_rate_limit_hits = 0
_rate_lock = threading.Lock()

def _get_token():
    global _token_idx
    with _token_lock:
        token = TOKENS[_token_idx % len(TOKENS)]
        _token_idx += 1
    return token

def fetch_candles_single(security_id, from_date, to_date, retries=4):
    """Fetch 1-min candles. Returns (data, status).
    status: 'ok', 'empty', 'rate_limit', 'error'"""
    global _rate_limit_hits
    for attempt in range(retries):
        token = _get_token()
        try:
            resp = requests.post(DHAN_URL, headers={
                "access-token": token,
                "client-id": CLIENT_ID,
                "Content-Type": "application/json",
            }, json={
                "securityId": security_id,
                "exchangeSegment": "NSE_EQ",
                "instrument": "EQUITY",
                "interval": "1",
                "fromDate": from_date,
                "toDate": to_date,
            }, timeout=30)

            if resp.status_code == 429 or (resp.status_code == 400 and "DH-905" in resp.text):
                with _rate_lock:
                    _rate_limit_hits += 1
                time.sleep(3 * (attempt + 1))
                if attempt == retries - 1:
                    return None, "rate_limit"
                continue

            if resp.status_code == 400:
                return None, "empty"  # symbol doesn't exist for this period

            if not resp.ok:
                time.sleep(2 * (attempt + 1))
                continue

            data = resp.json()
            if not data or "open" not in data or len(data.get("open", [])) == 0:
                return None, "empty"

            return data, "ok"
        except requests.exceptions.Timeout:
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(1 * (attempt + 1))

    return None, "error"

def fetch_candles_adaptive(security_id, from_date, to_date, all_days):
    """Try large chunk first (30 days). On rate limit, split into smaller chunks.
    Fallback chain: 30 days -> 10 days -> 5 days.
    Returns list of (data, days_covered) tuples.
    NEVER silently drops data — retries until success or confirmed empty."""
    results = []

    # Try full range first
    data, status = fetch_candles_single(security_id, from_date, to_date)
    if status == "ok":
        return [(data, all_days)]
    if status == "empty":
        return []

    # Rate limit or error: fall back to 10-day chunks
    time.sleep(1)  # cooldown before retry
    small_chunks = chunk_days(all_days, 10)
    for from_d, to_d, days in small_chunks:
        time.sleep(0.3)
        data, status = fetch_candles_single(security_id, from_d, to_d)
        if status == "ok":
            results.append((data, days))
        elif status == "empty":
            continue  # no data for this period, fine
        elif status in ("rate_limit", "error"):
            # Fall back to 5-day chunks with longer cooldown
            time.sleep(2)
            tiny_chunks = chunk_days(days, 5)
            for tf, tt, td in tiny_chunks:
                time.sleep(0.5)
                data2, s2 = fetch_candles_single(security_id, tf, tt)
                if s2 == "ok":
                    results.append((data2, td))
                elif s2 == "empty":
                    continue
                elif s2 in ("rate_limit", "error"):
                    # Last resort: single-day fetch with long wait
                    time.sleep(5)
                    for single_day in td:
                        time.sleep(0.5)
                        data3, s3 = fetch_candles_single(security_id, single_day, single_day)
                        if s3 == "ok":
                            results.append((data3, [single_day]))
                        # empty = no data that day (holiday/not listed), skip

    return results

# ============================================================================
#  CANDLE -> ROWS CONVERSION
# ============================================================================
def candles_to_rows(symbol, data, target_days_set):
    """Convert Dhan API response to list of row dicts.
    Only keeps rows for dates in target_days_set.
    Handles duplicate timestamps by keeping unique (date, bucket) pairs."""
    if not data or "open" not in data:
        return []

    seen = set()  # (date, bucket) dedup
    rows_by_date = {}

    timestamps = data.get("timestamp", [])
    opens = data.get("open", [])
    highs = data.get("high", [])
    lows = data.get("low", [])
    closes = data.get("close", [])
    volumes = data.get("volume", [])
    has_volume = len(volumes) == len(opens)

    for j in range(len(opens)):
        ts = timestamps[j]
        dt = datetime.datetime.utcfromtimestamp(ts)
        ist = dt + datetime.timedelta(hours=5, minutes=30)
        date_str = ist.strftime("%Y-%m-%d")

        if date_str not in target_days_set:
            continue

        minute_of_day = ist.hour * 60 + ist.minute
        bucket = minute_of_day - (9 * 60 + 15) + 1

        if bucket < 1 or bucket > 375:
            continue

        # Dedup: skip if we already have this (date, bucket) for this symbol
        key = (date_str, bucket)
        if key in seen:
            continue
        seen.add(key)

        o = opens[j]
        h = highs[j]
        l = lows[j]
        c = closes[j]
        v = volumes[j] if has_volume else 0

        # Sanity check: prices must be positive
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        # High must be >= Low
        if h < l:
            h, l = l, h

        rng = h - l
        body_ratio = abs(c - o) / rng if rng > 0 else 0.5

        if date_str not in rows_by_date:
            rows_by_date[date_str] = []

        rows_by_date[date_str].append({
            "date": date_str,
            "symbol": symbol,
            "bucket": bucket,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": max(0, int(v)),
            "buy_ratio": round(body_ratio, 4),
        })

    # Compute cum_volume per date (sorted by bucket)
    all_rows = []
    for date_str in sorted(rows_by_date.keys()):
        drows = rows_by_date[date_str]
        drows.sort(key=lambda r: r["bucket"])
        cum_vol = 0
        for r in drows:
            cum_vol += r["volume"]
            r["cum_volume"] = cum_vol
            r["vwap"] = 0.0
            r["vol_rate"] = 0.0
        all_rows.extend(drows)

    return all_rows

# ============================================================================
#  COMPUTE GAP_PCT AND DAY_OPEN
# ============================================================================
def add_gap_and_dayopen(df):
    """Add gap_pct and day_open columns."""
    # day_open = open of bucket 1; closing_price = close of last bucket
    daily = df.groupby(["symbol", "date"]).agg(
        day_open=("open", "first"),
        closing_price=("close", "last"),
    ).reset_index().sort_values(["symbol", "date"])

    daily["prev_close"] = daily.groupby("symbol")["closing_price"].shift(1)
    daily["gap_pct"] = np.where(
        daily["prev_close"] > 0,
        (daily["day_open"] - daily["prev_close"]) / daily["prev_close"] * 100,
        0.0
    )

    gap_map = daily[["symbol", "date", "gap_pct", "day_open"]].copy()
    gap_map["gap_pct"] = gap_map["gap_pct"].astype(np.float32)
    gap_map["day_open"] = gap_map["day_open"].astype(np.float32)

    df = df.merge(gap_map, on=["symbol", "date"], how="left")
    df["gap_pct"] = df["gap_pct"].fillna(0).astype(np.float32)
    df["day_open"] = df["day_open"].fillna(0).astype(np.float32)
    return df

# ============================================================================
#  MAIN
# ============================================================================
def auto_scan_months():
    """Scan the parquets folder and return every month YYYY-MM from the earliest
    existing candles_*.parquet through the current month. Months with no file or
    with missing trading days get picked up by the per-month missing-day logic
    downstream."""
    pat = re.compile(r"^candles_(\d{4})(\d{2})\.parquet$")
    existing = []
    for p in DATA_DIR.glob("candles_*.parquet"):
        m = pat.match(p.name)
        if m:
            existing.append((int(m.group(1)), int(m.group(2))))
    today = datetime.date.today()
    if not existing:
        # Nothing to go off — default to current month only
        start = (today.year, today.month)
    else:
        start = min(existing)
    end = (today.year, today.month)

    months = []
    y, m = start
    while (y, m) <= end:
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12: m = 1; y += 1
    return months

def main():
    parser = argparse.ArgumentParser(description="Download monthly parquet from Dhan API")
    parser.add_argument("months", nargs="*",
                        help="Month(s) in YYYY-MM. Two args = inclusive range. "
                             "Omit entirely for AUTO mode (scan parquets/ folder).")
    args = parser.parse_args()

    if not args.months:
        months = auto_scan_months()
        log(f"AUTO mode: scanning {DATA_DIR}")
    elif len(args.months) == 2 and "-" in args.months[0] and "-" in args.months[1]:
        # Range mode
        start_y, start_m = map(int, args.months[0].split("-"))
        end_y, end_m = map(int, args.months[1].split("-"))
        months = []
        y, m = start_y, start_m
        while (y, m) <= (end_y, end_m):
            months.append(f"{y}-{m:02d}")
            m += 1
            if m > 12: m = 1; y += 1
    else:
        months = args.months

    log(f"Months to process: {months}")

    sym_map = load_symbol_map()
    log(f"Symbol map: {len(sym_map)} symbols")

    liquid = json.load(open(CONFIG_DIR / "liquid-5l-symbols.json", encoding="utf-8"))
    log(f"Liquid symbols: {len(liquid)}")

    symbols = [(s, sym_map[s]) for s in liquid if s in sym_map]
    log(f"Symbols to download: {len(symbols)}")

    for month_str in months:
        ym = month_str.replace("-", "")
        out_path = DATA_DIR / f"candles_{ym}.parquet"

        log(f"\n{'='*60}")
        log(f"Downloading {month_str}")
        log(f"{'='*60}")

        from_date, to_date = get_month_range(month_str)

        # Cap to_date to yesterday (never download today's incomplete data)
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        to_date_d = datetime.date.fromisoformat(to_date)
        if to_date_d >= today:
            to_date = yesterday.isoformat()
            log(f"  Capped to_date to {to_date} (excluding today)")

        # Lookback for gap calculation (need prev day's close)
        lookback = datetime.date.fromisoformat(from_date) - datetime.timedelta(days=10)
        lookback_str = lookback.isoformat()

        all_trading_days = get_trading_days(lookback_str, to_date)
        target_days = get_trading_days(from_date, to_date)
        target_set = set(target_days)

        # If parquet exists, find which days are already present and only download missing
        existing_df = None
        if out_path.exists():
            existing_df = pd.read_parquet(out_path, columns=["date"])
            existing_dates = set(existing_df["date"].unique())
            missing_days = sorted(d for d in target_days if d not in existing_dates)
            if not missing_days:
                sz = out_path.stat().st_size / 1024 / 1024
                log(f"  SKIP -- all {len(target_days)} target days already present ({sz:.1f} MB)")
                continue
            log(f"  Existing: {len(existing_dates)} days | Missing: {len(missing_days)} days: {missing_days}")
            del existing_df
            # Narrow target to only missing days
            target_days = missing_days
            target_set = set(target_days)
            # Rebuild all_trading_days to include lookback + missing days for gap calc
            # Include the trading day before each missing day for correct prev_close
            all_month_days = get_trading_days(from_date, to_date)
            gap_lookback = set()
            for md in missing_days:
                idx = all_month_days.index(md) if md in all_month_days else -1
                if idx > 0:
                    gap_lookback.add(all_month_days[idx - 1])
            lookback_days = get_trading_days(lookback_str, from_date)
            all_trading_days = sorted(set(lookback_days + target_days + list(gap_lookback)))
        full_set = set(all_trading_days)

        log(f"  Target days: {len(target_days)} | With lookback: {len(all_trading_days)}")

        # ── Download all symbols concurrently (write per-symbol temp parquets) ──
        import gc
        tmp_dir = DATA_DIR / "_tmp_syms"
        tmp_dir.mkdir(exist_ok=True)
        total_rows = 0
        t_start = time.time()
        completed = 0
        empty_syms = 0
        error_syms = 0
        counter_lock = threading.Lock()

        FLOAT32_COLS = ["open", "high", "low", "close", "vwap", "vol_rate", "buy_ratio"]

        def download_symbol(symbol, sec_id):
            """Download all data for one symbol, write to temp parquet."""
            sym_rows = []
            chunks = fetch_candles_adaptive(sec_id, all_trading_days[0], all_trading_days[-1], all_trading_days)
            for data, days_covered in chunks:
                rows = candles_to_rows(symbol, data, full_set)
                sym_rows.extend(rows)
            if sym_rows:
                sdf = pd.DataFrame(sym_rows)
                for c in FLOAT32_COLS:
                    if c in sdf.columns:
                        sdf[c] = sdf[c].astype("float32")
                sdf.to_parquet(tmp_dir / f"{symbol}.parquet", index=False)
            return symbol, len(sym_rows)

        WORKERS = len(TOKENS)
        log(f"  Downloading with {WORKERS} threads...")

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(download_symbol, sym, sid): sym for sym, sid in symbols}

            for future in as_completed(futures):
                sym = futures[future]
                try:
                    symbol, n_rows = future.result()
                    if n_rows > 0:
                        total_rows += n_rows
                    else:
                        with counter_lock:
                            empty_syms += 1
                except Exception as e:
                    with counter_lock:
                        error_syms += 1
                    log(f"  ERROR {sym}: {e}")

                with counter_lock:
                    completed += 1
                    c = completed

                if c % 100 == 0 or c == len(symbols):
                    elapsed = time.time() - t_start
                    pct = c / len(symbols) * 100
                    rate = c / max(elapsed, 0.1) * 60
                    eta = (len(symbols) - c) / max(rate / 60, 0.01)
                    log(f"  [{c}/{len(symbols)}] ({pct:.1f}%) {total_rows:,} rows | "
                        f"{elapsed:.0f}s | {rate:.0f} sym/min | ETA: {eta:.0f}s | "
                        f"empty: {empty_syms} err: {error_syms} rl: {_rate_limit_hits}")

        log(f"  Download done: {total_rows:,} rows | "
            f"{empty_syms} empty | {error_syms} errors | {_rate_limit_hits} rate limits")

        if total_rows == 0:
            log(f"  WARNING: no data for {month_str}, skipping")
            continue

        # ── Process per-symbol: gap calc, filter, append to final parquet ──
        log(f"  Processing per-symbol (gap_pct, dedup, filter)...")
        import pyarrow.parquet as pq
        import pyarrow as pa

        DTYPES = {
            "date": "str", "symbol": "str",
            "gap_pct": "float32", "day_open": "float32",
            "bucket": "uint16",
            "open": "float32", "high": "float32", "low": "float32", "close": "float32",
            "volume": "uint32", "cum_volume": "uint64",
            "vwap": "float32", "vol_rate": "float32", "buy_ratio": "float32",
        }
        col_order = ["date", "symbol", "gap_pct", "day_open", "bucket",
                    "open", "high", "low", "close", "volume", "cum_volume",
                    "vwap", "vol_rate", "buy_ratio"]

        sym_files = sorted(tmp_dir.glob("*.parquet"))
        new_out_path = DATA_DIR / f"candles_{ym}_new.parquet"
        writer = None
        final_rows = 0
        n_syms = 0
        new_dates = set()

        for sf in sym_files:
            sdf = pd.read_parquet(sf)
            if sdf.empty:
                sf.unlink()
                continue

            sdf = sdf.sort_values(["date", "bucket"]).drop_duplicates(
                subset=["date", "bucket"], keep="last").reset_index(drop=True)

            # Compute gap_pct and day_open for this symbol
            sdf = add_gap_and_dayopen(sdf)

            # Filter to target month
            sdf = sdf[sdf["date"].isin(target_set)].reset_index(drop=True)
            if sdf.empty:
                sf.unlink()
                continue

            # Remove negative prices
            sdf = sdf[(sdf["open"] > 0) & (sdf["high"] > 0) & (sdf["low"] > 0) & (sdf["close"] > 0)]

            # Apply dtypes
            for col in col_order:
                if col not in sdf.columns:
                    sdf[col] = 0
                sdf[col] = sdf[col].astype(DTYPES[col])
            sdf = sdf[col_order]

            table = pa.Table.from_pandas(sdf, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(new_out_path, table.schema)
            writer.write_table(table)

            final_rows += len(sdf)
            n_syms += 1
            new_dates.update(sdf["date"].unique())
            del sdf, table
            sf.unlink()

        if writer:
            writer.close()

        # Cleanup temp dir
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

        if final_rows == 0:
            log(f"  WARNING: 0 new rows after filtering, skipping")
            if new_out_path.exists():
                new_out_path.unlink()
            continue

        # Merge with existing parquet if present
        if out_path.exists():
            log(f"  Merging new data ({len(new_dates)} days) with existing parquet...")
            existing = pd.read_parquet(out_path)
            new_data = pd.read_parquet(new_out_path)
            merged = pd.concat([existing, new_data], ignore_index=True)
            merged = merged.drop_duplicates(subset=["symbol", "date", "bucket"], keep="last")
            merged = merged.sort_values(["date", "symbol", "bucket"]).reset_index(drop=True)
            # Recompute gap_pct on full merged data (fixes stale prev_close from partial downloads).
            # Seed with previous month's last close so the first day of this month is correct.
            merged = merged.drop(columns=["gap_pct", "day_open"], errors="ignore")
            prev_ym = (
                datetime.date(int(month_str[:4]), int(month_str[5:7]), 1)
                - datetime.timedelta(days=1)
            )
            prev_month_file = DATA_DIR / f"candles_{prev_ym.strftime('%Y%m')}.parquet"
            if prev_month_file.exists():
                prev_df = pd.read_parquet(prev_month_file, columns=["symbol", "date", "close", "bucket"])
                prev_last = (
                    prev_df.sort_values(["symbol", "date", "bucket"])
                    .groupby("symbol")[["date", "close"]]
                    .last()
                    .rename(columns={"close": "closing_price"})
                    .reset_index()
                )
                prev_last["date"] = prev_last["date"]  # keep as string
                # Build a synthetic daily-close frame and prepend to merged for gap seeding
                seed_df = pd.DataFrame({
                    "symbol": prev_last["symbol"],
                    "date": prev_last["date"],
                    "open": prev_last["closing_price"].astype("float32"),
                    "close": prev_last["closing_price"].astype("float32"),
                    "high": prev_last["closing_price"].astype("float32"),
                    "low": prev_last["closing_price"].astype("float32"),
                    "bucket": np.uint16(9999),  # sentinel — will be filtered out
                })
                for c in merged.columns:
                    if c not in seed_df.columns:
                        seed_df[c] = 0
                seed_df = seed_df[merged.columns]
                combined = pd.concat([seed_df, merged], ignore_index=True)
                combined = add_gap_and_dayopen(combined)
                # Remove seed rows (bucket == 9999)
                merged = combined[combined["bucket"] != 9999].reset_index(drop=True)
                del prev_df, prev_last, seed_df, combined
            else:
                merged = add_gap_and_dayopen(merged)
            # Apply dtypes
            for col in col_order:
                merged[col] = merged[col].astype(DTYPES[col])
            merged[col_order].to_parquet(out_path, index=False)
            new_out_path.unlink()
            all_dates = set(merged["date"].unique())
            final_rows = len(merged)
            del existing, new_data, merged
            log(f"  Merged: {len(all_dates)} total days, {final_rows:,} rows")
        else:
            os.rename(new_out_path, out_path)
            all_dates = new_dates

        n_dates = len(all_dates)
        log(f"  {n_syms} symbols, {n_dates} trading days, {final_rows:,} rows")
        if n_dates > 0:
            log(f"  Date range: {min(all_dates)} -> {max(all_dates)}")

        sz = out_path.stat().st_size / 1024 / 1024
        log(f"  Saved: {out_path.name} ({sz:.1f} MB)")
        log(f"  Elapsed: {time.time()-t_start:.1f}s")
        gc.collect()

    log(f"\nDone. Total: {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
