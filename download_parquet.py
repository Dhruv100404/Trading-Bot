"""
download_parquet.py
===================
Downloads 1-min candle data from Dhan API and saves as monthly parquet.
Format matches existing candles_YYYYMM.parquet exactly.

Usage:
  python download_parquet.py                    # AUTO: audit previous+current month, fill gaps through today
  python download_parquet.py 2024-03            # single month
  python download_parquet.py 2024-03 2024-05    # range of months
  python download_parquet.py --day 2026-04-30   # one day -> parquets/daily/candles_20260430.parquet
  python download_parquet.py --day-range 2026-04-01 2026-04-30
                                                # separate daily parquet for each trading day

Features:
  - Auto mode: audits the recent monthly parquet files first using NSE holidays
    and weekday filtering, then downloads only missing trading days up to today.
  - Daily mode: writes day-wise files under parquets/daily/ so monthly
    scanner/backtest files do not double-count rows.
  - Auto-builds data/symbol_secid_map.tsv from Dhan's public scrip master CSV
    if it's not already present.
  - Concurrent downloads default to API token count; override with PARQUET_WORKERS.
  - Adaptive chunk size: 30 days -> 10 days -> 5 days -> single day on rate limit.
  - Dedup: drops duplicate (symbol, date, bucket) rows, keeps last.
  - Handles missing symbols gracefully (IPO not happened, delisted, etc.).

Requires: requests, pandas, pyarrow
Uses: data/liquid-5l-symbols.json (symbol filter)
      data/symbol_secid_map.tsv   (symbol -> security_id; auto-built if missing)
"""

import sys, io, time, json, os, argparse, datetime, calendar, threading, signal, gc, re, base64, traceback
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

STOP_EVENT = threading.Event()


def handle_sigint(signum, frame):
    STOP_EVENT.set()
    print("\nInterrupted. Cancelling downloads...", flush=True)
    raise KeyboardInterrupt


signal.signal(signal.SIGINT, handle_sigint)

REPO_DIR   = Path(__file__).resolve().parent
DATA_DIR   = REPO_DIR / "parquets"   # where candles_YYYYMM.parquet files live
DAILY_DIR  = DATA_DIR / "daily"      # where day-wise candles_YYYYMMDD.parquet files live
CONFIG_DIR = REPO_DIR / "data"       # where symbol lists + secid map live
DATA_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(REPO_DIR / ".env")

TOKENS = [
    token.strip()
    for token in os.getenv("DHAN_ACCESS_TOKENS", os.getenv("DHAN_ACCESS_TOKEN", "")).split(",")
    if token.strip()
]

CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
DHAN_URL = "https://api.dhan.co/v2/charts/intraday"

FLOAT32_COLS = ["open", "high", "low", "close", "vwap", "vol_rate", "buy_ratio"]
PARQUET_DTYPES = {
    "date": "str", "symbol": "str",
    "gap_pct": "float32", "day_open": "float32",
    "bucket": "uint16",
    "open": "float32", "high": "float32", "low": "float32", "close": "float32",
    "volume": "uint32", "cum_volume": "uint64",
    "vwap": "float32", "vol_rate": "float32", "buy_ratio": "float32",
}
PARQUET_COL_ORDER = ["date", "symbol", "gap_pct", "day_open", "bucket",
                     "open", "high", "low", "close", "volume", "cum_volume",
                     "vwap", "vol_rate", "buy_ratio"]

def validate_token_timestamps(tokens):
    if not tokens:
        raise RuntimeError("Missing DHAN_ACCESS_TOKEN in .env")
    if not CLIENT_ID:
        raise RuntimeError("Missing DHAN_CLIENT_ID in .env")

    now = datetime.datetime.now(datetime.timezone.utc)
    for idx, token in enumerate(tokens, start=1):
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            exp = claims.get("exp")
            if exp and datetime.datetime.fromtimestamp(exp, datetime.timezone.utc) <= now:
                exp_ist = datetime.datetime.fromtimestamp(
                    exp,
                    datetime.timezone.utc,
                ).astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
                raise RuntimeError(
                    f"DHAN_ACCESS_TOKEN #{idx} expired at {exp_ist:%Y-%m-%d %H:%M:%S %z}. "
                    "Generate a fresh token and update .env."
                )
        except RuntimeError:
            raise
        except Exception:
            log(f"WARNING: Could not decode expiry for DHAN_ACCESS_TOKEN #{idx}; continuing")


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


class DownloadCancelled(RuntimeError):
    pass


class UserFacingError(RuntimeError):
    pass


def check_cancelled():
    if STOP_EVENT.is_set():
        raise DownloadCancelled("Download cancelled by user")


def sleep_interruptible(seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        check_cancelled()
        time.sleep(min(0.25, deadline - time.time()))
    check_cancelled()

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

def preview_days(days, limit=8):
    if len(days) <= limit:
        return ", ".join(days)
    return f"{', '.join(days[:limit])}, ... +{len(days) - limit} more"

def missing_days_for_month(month_str):
    from_date, to_date = get_month_range(month_str)
    today = datetime.date.today()
    from_date_d = datetime.date.fromisoformat(from_date)
    to_date_d = datetime.date.fromisoformat(to_date)

    if from_date_d > today:
        return [], 0, "future"
    if to_date_d > today:
        to_date = today.isoformat()

    target_days = get_trading_days(from_date, to_date)
    if not target_days:
        return [], 0, "no_trading_days"

    out_path = DATA_DIR / f"candles_{month_str.replace('-', '')}.parquet"
    if not out_path.exists():
        return target_days, len(target_days), "missing_file"

    try:
        existing_df = pd.read_parquet(out_path, columns=["date"])
        existing_dates = {str(d) for d in existing_df["date"].dropna().unique()}
    except Exception as exc:
        log(f"  AUDIT {month_str}: could not read date column ({type(exc).__name__}: {exc}); will rebuild missing window")
        return target_days, len(target_days), "unreadable"

    missing_days = sorted(d for d in target_days if d not in existing_dates)
    return missing_days, len(target_days), "partial" if missing_days else "complete"

def chunk_days(days, size):
    chunks = []
    for i in range(0, len(days), size):
        c = days[i:i+size]
        chunks.append((c[0], c[-1], c))
    return chunks

def prepare_tmp_dir(tmp_dir, clear=True):
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if not clear:
        existing = len(list(tmp_dir.glob("*.parquet")))
        if existing:
            log(f"  Resume cache: found {existing} temp symbol files in {tmp_dir.name}")
        return

    stale = 0
    for sf in tmp_dir.glob("*.parquet"):
        sf.unlink()
        stale += 1
    if stale:
        log(f"  Cleared {stale} stale temp files from {tmp_dir.name}")

def temp_symbol_has_target_days(path, target_days):
    """Return True when an interrupted monthly run already fetched this symbol."""
    if not path.exists():
        return False
    try:
        dates = pd.read_parquet(path, columns=["date"])["date"].astype(str).unique()
    except Exception:
        return False
    return set(target_days).issubset(set(dates))

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

def select_symbols(sym_map, symbols_arg=None):
    if symbols_arg:
        requested = [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
        requested = list(dict.fromkeys(requested))
        if not requested:
            raise UserFacingError("--symbols was provided but no valid symbols were parsed")
        missing = [s for s in requested if s not in sym_map]
        if missing:
            preview = ", ".join(missing[:15])
            more = "" if len(missing) <= 15 else f", ... +{len(missing) - 15} more"
            log(f"WARNING: symbols not found in symbol_secid_map.tsv: {preview}{more}")
        symbols = [(s, sym_map[s]) for s in requested if s in sym_map]
        if not symbols:
            raise UserFacingError("None of the requested symbols were found in symbol_secid_map.tsv")
        log(f"Requested symbols: {len(requested)} | matched: {len(symbols)}")
        return symbols

    liquid = json.load(open(CONFIG_DIR / "liquid-5l-symbols.json", encoding="utf-8"))
    log(f"Liquid symbols: {len(liquid)}")
    return [(s, sym_map[s]) for s in liquid if s in sym_map]

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
    status: 'ok', 'empty', 'rate_limit', 'auth_error', 'error'"""
    global _rate_limit_hits
    for attempt in range(retries):
        check_cancelled()
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
                log(f"  RATE LIMIT {security_id} {from_date}->{to_date} attempt {attempt + 1}/{retries}")
                sleep_interruptible(3 * (attempt + 1))
                if attempt == retries - 1:
                    return None, "rate_limit"
                continue

            if resp.status_code == 400:
                if "DH-905" not in resp.text and resp.text:
                    log(f"  Dhan 400 {security_id} {from_date}->{to_date}: {resp.text[:180]}")
                return None, "empty"  # symbol doesn't exist for this period

            if resp.status_code in (401, 403):
                log(f"  AUTH ERROR from Dhan ({resp.status_code}): {resp.text[:180]}")
                return None, "auth_error"

            if not resp.ok:
                log(f"  Dhan HTTP {resp.status_code} {security_id} {from_date}->{to_date}: {resp.text[:180]}")
                sleep_interruptible(2 * (attempt + 1))
                continue

            data = resp.json()
            if not data or "open" not in data or len(data.get("open", [])) == 0:
                return None, "empty"

            return data, "ok"
        except requests.exceptions.Timeout as e:
            log(f"  TIMEOUT {security_id} {from_date}->{to_date} attempt {attempt + 1}/{retries}: {e}")
            sleep_interruptible(2 * (attempt + 1))
        except requests.exceptions.RequestException as e:
            log(f"  REQUEST ERROR {security_id} {from_date}->{to_date} attempt {attempt + 1}/{retries}: {e}")
            sleep_interruptible(2 * (attempt + 1))
        except (KeyboardInterrupt, DownloadCancelled):
            raise
        except Exception as e:
            log(f"  UNEXPECTED ERROR {security_id} {from_date}->{to_date}: {type(e).__name__}: {e}")
            log(traceback.format_exc().rstrip())
            sleep_interruptible(1 * (attempt + 1))

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
    if status == "auth_error":
        raise RuntimeError("Dhan rejected the configured access token/client id")

    # Rate limit or error: fall back to 10-day chunks
    sleep_interruptible(1)  # cooldown before retry
    small_chunks = chunk_days(all_days, 10)
    for from_d, to_d, days in small_chunks:
        check_cancelled()
        sleep_interruptible(0.3)
        data, status = fetch_candles_single(security_id, from_d, to_d)
        if status == "ok":
            results.append((data, days))
        elif status == "empty":
            continue  # no data for this period, fine
        elif status == "auth_error":
            raise RuntimeError("Dhan rejected the configured access token/client id")
        elif status in ("rate_limit", "error"):
            # Fall back to 5-day chunks with longer cooldown
            sleep_interruptible(2)
            tiny_chunks = chunk_days(days, 5)
            for tf, tt, td in tiny_chunks:
                check_cancelled()
                sleep_interruptible(0.5)
                data2, s2 = fetch_candles_single(security_id, tf, tt)
                if s2 == "ok":
                    results.append((data2, td))
                elif s2 == "empty":
                    continue
                elif s2 == "auth_error":
                    raise RuntimeError("Dhan rejected the configured access token/client id")
                elif s2 in ("rate_limit", "error"):
                    # Last resort: single-day fetch with long wait
                    sleep_interruptible(5)
                    for single_day in td:
                        check_cancelled()
                        sleep_interruptible(0.5)
                        data3, s3 = fetch_candles_single(security_id, single_day, single_day)
                        if s3 == "ok":
                            results.append((data3, [single_day]))
                        elif s3 == "auth_error":
                            raise RuntimeError("Dhan rejected the configured access token/client id")
                        elif s3 in ("rate_limit", "error"):
                            log(f"  FAILED DAY {security_id} {single_day}: status={s3}")
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
def add_months(year, month, delta):
    month_index = (year * 12 + (month - 1)) + delta
    return month_index // 12, month_index % 12 + 1

def auto_scan_months(recent_months=2, all_months=False):
    """Return the months auto mode should audit before any Dhan downloads."""
    pat = re.compile(r"^candles_(\d{4})(\d{2})\.parquet$")
    existing = []
    for p in DATA_DIR.glob("candles_*.parquet"):
        m = pat.match(p.name)
        if m:
            existing.append((int(m.group(1)), int(m.group(2))))
    today = datetime.date.today()
    end = (today.year, today.month)

    if all_months:
        start = min(existing) if existing else end
    else:
        recent_months = max(1, int(recent_months))
        start = add_months(today.year, today.month, -(recent_months - 1))

    months = []
    y, m = start
    while (y, m) <= end:
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12: m = 1; y += 1
    return months

def validate_completed_trading_day(day_str):
    try:
        day = datetime.date.fromisoformat(day_str)
    except ValueError as exc:
        raise UserFacingError(f"Invalid day '{day_str}'. Use YYYY-MM-DD.") from exc

    today = datetime.date.today()
    if day > today:
        raise UserFacingError(
            f"{day_str} is in the future. "
            f"Use a date on or before {today.isoformat()}."
        )
    if day.weekday() >= 5:
        raise UserFacingError(f"{day_str} is a weekend, so no NSE intraday parquet will be downloaded.")
    if day_str in NSE_HOLIDAYS:
        raise UserFacingError(f"{day_str} is in the NSE holiday list, so no parquet will be downloaded.")
    return day

def expand_daily_targets(day_arg, day_range):
    if day_arg and day_range:
        raise UserFacingError("Use either --day or --day-range, not both.")
    if day_arg:
        return [validate_completed_trading_day(day_arg).isoformat()]
    if not day_range:
        return []

    try:
        start = datetime.date.fromisoformat(day_range[0])
        end = datetime.date.fromisoformat(day_range[1])
    except ValueError as exc:
        raise UserFacingError("--day-range values must be YYYY-MM-DD YYYY-MM-DD") from exc

    if start > end:
        raise UserFacingError("--day-range start date must be before or equal to end date")

    days = get_trading_days(start.isoformat(), end.isoformat())
    days = [validate_completed_trading_day(d).isoformat() for d in days]
    if not days:
        raise UserFacingError("No eligible trading days found in the requested --day-range")
    return days

def download_day_parquet(day_str, symbols, force=False):
    day = validate_completed_trading_day(day_str)
    ymd = day.strftime("%Y%m%d")
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DAILY_DIR / f"candles_{ymd}.parquet"

    if out_path.exists() and not force:
        sz = out_path.stat().st_size / 1024 / 1024
        log(f"  SKIP -- daily parquet already exists: {out_path.relative_to(REPO_DIR)} ({sz:.1f} MB)")
        log("  Use --force to rebuild it from Dhan.")
        return out_path

    lookback = day - datetime.timedelta(days=10)
    all_trading_days = get_trading_days(lookback.isoformat(), day_str)
    if day_str not in all_trading_days:
        raise UserFacingError(f"{day_str} is not a trading day after holiday/weekend filtering")

    target_set = {day_str}
    full_set = set(all_trading_days)

    log(f"\n{'='*60}")
    log(f"Downloading daily parquet for {day_str}")
    log(f"{'='*60}")
    log(f"  Output: {out_path.relative_to(REPO_DIR)}")
    log(f"  Target days: 1 | With lookback: {len(all_trading_days)}")

    tmp_dir = DATA_DIR / f"_tmp_day_{ymd}"
    prepare_tmp_dir(tmp_dir)
    new_out_path = DAILY_DIR / f"candles_{ymd}_new.parquet"
    if new_out_path.exists():
        new_out_path.unlink()

    total_rows = 0
    t_start = time.time()
    completed = 0
    empty_syms = 0
    error_syms = 0
    failed_symbols = []
    counter_lock = threading.Lock()

    def download_symbol(symbol, sec_id):
        check_cancelled()
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

    WORKERS = max(1, int(os.getenv("PARQUET_WORKERS", str(len(TOKENS)))))
    log(f"  Downloading with {WORKERS} threads...")

    pool = ThreadPoolExecutor(max_workers=WORKERS)
    futures = {}
    shutdown_wait = True
    try:
        futures = {pool.submit(download_symbol, sym, sid): sym for sym, sid in symbols}
        pending = set(futures)

        while pending:
            check_cancelled()
            done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            if not done:
                continue

            for future in done:
                sym = futures[future]
                try:
                    symbol, n_rows = future.result()
                    if n_rows > 0:
                        total_rows += n_rows
                    else:
                        with counter_lock:
                            empty_syms += 1
                except (KeyboardInterrupt, DownloadCancelled):
                    raise
                except Exception as e:
                    with counter_lock:
                        error_syms += 1
                        failed_symbols.append(sym)
                    log(f"  ERROR {sym}: {type(e).__name__}: {e}")
                    log(traceback.format_exc().rstrip())

                with counter_lock:
                    completed += 1
                    c = completed

                if c % 25 == 0 or c == len(symbols):
                    elapsed = time.time() - t_start
                    pct = c / len(symbols) * 100
                    rate = c / max(elapsed, 0.1) * 60
                    eta = (len(symbols) - c) / max(rate / 60, 0.01)
                    log(f"  [{c}/{len(symbols)}] ({pct:.1f}%) {total_rows:,} rows | "
                        f"{elapsed:.0f}s | {rate:.0f} sym/min | ETA: {eta:.0f}s | "
                        f"empty: {empty_syms} err: {error_syms} rl: {_rate_limit_hits}")
    except (KeyboardInterrupt, DownloadCancelled):
        STOP_EVENT.set()
        for future in futures:
            future.cancel()
        shutdown_wait = False
        log(f"  Cancelled. Partial temp files are left in {tmp_dir.relative_to(REPO_DIR)} for inspection.")
        raise
    finally:
        pool.shutdown(wait=shutdown_wait, cancel_futures=True)

    log(f"  Download done: {total_rows:,} rows | "
        f"{empty_syms} empty | {error_syms} errors | {_rate_limit_hits} rate limits")
    if failed_symbols:
        preview = ", ".join(failed_symbols[:20])
        more = "" if len(failed_symbols) <= 20 else f", ... +{len(failed_symbols) - 20} more"
        log(f"  Failed symbols: {preview}{more}")

    if total_rows == 0:
        log(f"  WARNING: no data for {day_str}, skipping")
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        return None

    log("  Processing per-symbol daily rows (gap_pct, dedup, filter)...")
    import pyarrow.parquet as pq
    import pyarrow as pa

    sym_files = sorted(tmp_dir.glob("*.parquet"))
    writer = None
    final_rows = 0
    n_syms = 0

    try:
        for sf in sym_files:
            sdf = pd.read_parquet(sf)
            if sdf.empty:
                sf.unlink()
                continue

            sdf = sdf.sort_values(["date", "bucket"]).drop_duplicates(
                subset=["date", "bucket"], keep="last").reset_index(drop=True)

            sdf = add_gap_and_dayopen(sdf)
            sdf = sdf[sdf["date"].isin(target_set)].reset_index(drop=True)
            if sdf.empty:
                sf.unlink()
                continue

            sdf = sdf[(sdf["open"] > 0) & (sdf["high"] > 0) & (sdf["low"] > 0) & (sdf["close"] > 0)]
            if sdf.empty:
                sf.unlink()
                continue

            for col in PARQUET_COL_ORDER:
                if col not in sdf.columns:
                    sdf[col] = 0
                sdf[col] = sdf[col].astype(PARQUET_DTYPES[col])
            sdf = sdf[PARQUET_COL_ORDER]

            table = pa.Table.from_pandas(sdf, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(new_out_path, table.schema)
            writer.write_table(table)

            final_rows += len(sdf)
            n_syms += 1
            del sdf, table
            sf.unlink()
    finally:
        if writer:
            writer.close()

    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    if final_rows == 0:
        log(f"  WARNING: 0 rows after filtering, skipping")
        if new_out_path.exists():
            new_out_path.unlink()
        return None

    os.replace(new_out_path, out_path)
    sz = out_path.stat().st_size / 1024 / 1024
    log(f"  {n_syms} symbols, 1 trading day, {final_rows:,} rows")
    log(f"  Saved: {out_path.relative_to(REPO_DIR)} ({sz:.1f} MB)")
    log(f"  Elapsed: {time.time()-t_start:.1f}s")
    gc.collect()
    return out_path

def main():
    parser = argparse.ArgumentParser(description="Download monthly or day-wise parquet from Dhan API")
    parser.add_argument("--day",
                        help="Trading day in YYYY-MM-DD, including today. Writes parquets/daily/candles_YYYYMMDD.parquet.")
    parser.add_argument("--day-range", nargs=2, metavar=("FROM", "TO"),
                        help="Trading-day range in YYYY-MM-DD YYYY-MM-DD, including today. Writes one daily parquet per day.")
    parser.add_argument("--symbols",
                        help="Comma-separated symbols to download instead of data/liquid-5l-symbols.json.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild an existing day-wise parquet. Monthly mode clears temp cache but still merges missing days only.")
    parser.add_argument("--recent-months", type=int, default=2,
                        help="AUTO mode audit window. Default 2 means previous month plus current month.")
    parser.add_argument("--all-months", action="store_true",
                        help="AUTO mode audits every candles_YYYYMM.parquet month through today.")
    parser.add_argument("months", nargs="*",
                        help="Month(s) in YYYY-MM. Two args = inclusive range. "
                             "Omit entirely for AUTO mode (recent monthly audit).")
    args = parser.parse_args()

    daily_targets = expand_daily_targets(args.day, args.day_range)
    if daily_targets and args.months:
        parser.error("--day/--day-range cannot be combined with month arguments")

    if daily_targets:
        months = []
    elif not args.months:
        months = auto_scan_months(recent_months=args.recent_months, all_months=args.all_months)
        scope = "all monthly parquets" if args.all_months else f"last {max(1, args.recent_months)} month(s)"
        log(f"AUTO mode: auditing {scope} in {DATA_DIR}")
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

    if months:
        log(f"Months to audit: {months}")
    if daily_targets:
        log(f"Daily targets to process: {daily_targets}")

    if months:
        log("Audit rule: NSE holidays are excluded and Saturdays/Sundays are skipped.")
        months_to_download = []
        complete_months = 0
        missing_day_count = 0
        for month_str in months:
            missing_days, expected_days, status = missing_days_for_month(month_str)
            if status == "future":
                log(f"  AUDIT {month_str}: future month, skipping")
                continue
            if missing_days:
                months_to_download.append(month_str)
                missing_day_count += len(missing_days)
                log(f"  AUDIT {month_str}: missing {len(missing_days)}/{expected_days} trading days -> {preview_days(missing_days)}")
            else:
                complete_months += 1
                log(f"  AUDIT {month_str}: complete ({expected_days} trading days)")

        months = months_to_download
        if not months:
            log(f"\nAUDIT OK: {complete_months} month(s) complete. No Dhan download needed.")
            log(f"Done. Total: {time.time()-t0:.1f}s")
            return
        log(f"Months to download: {months} ({missing_day_count} missing trading day(s))")

    validate_token_timestamps(TOKENS)

    sym_map = load_symbol_map()
    log(f"Symbol map: {len(sym_map)} symbols")

    symbols = select_symbols(sym_map, args.symbols)
    log(f"Symbols to download: {len(symbols)}")

    if daily_targets:
        for day_str in daily_targets:
            download_day_parquet(day_str, symbols, force=args.force)
        log(f"\nDone. Total: {time.time()-t0:.1f}s")
        return

    for month_str in months:
        ym = month_str.replace("-", "")
        out_path = DATA_DIR / f"candles_{ym}.parquet"

        log(f"\n{'='*60}")
        log(f"Downloading {month_str}")
        log(f"{'='*60}")

        from_date, to_date = get_month_range(month_str)

        # Cap current/future month requests to today. Existing files still skip days already present.
        today = datetime.date.today()
        to_date_d = datetime.date.fromisoformat(to_date)
        if to_date_d > today:
            to_date = today.isoformat()
            log(f"  Capped to_date to {to_date} (including today)")

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
        prepare_tmp_dir(tmp_dir, clear=args.force)
        total_rows = 0
        t_start = time.time()
        completed = 0
        cached_syms = 0
        empty_syms = 0
        error_syms = 0
        failed_symbols = []
        counter_lock = threading.Lock()

        def download_symbol(symbol, sec_id):
            """Download all data for one symbol, write to temp parquet."""
            check_cancelled()
            sym_path = tmp_dir / f"{symbol}.parquet"
            if temp_symbol_has_target_days(sym_path, target_days):
                return symbol, None

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
                sdf.to_parquet(sym_path, index=False)
            return symbol, len(sym_rows)

        WORKERS = max(1, int(os.getenv("PARQUET_WORKERS", str(len(TOKENS)))))
        log(f"  Downloading with {WORKERS} threads...")

        pool = ThreadPoolExecutor(max_workers=WORKERS)
        futures = {}
        shutdown_wait = True
        try:
            futures = {pool.submit(download_symbol, sym, sid): sym for sym, sid in symbols}
            pending = set(futures)

            while pending:
                check_cancelled()
                done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                for future in done:
                    sym = futures[future]
                    try:
                        symbol, n_rows = future.result()
                        if n_rows is None:
                            with counter_lock:
                                cached_syms += 1
                        elif n_rows > 0:
                            total_rows += n_rows
                        else:
                            with counter_lock:
                                empty_syms += 1
                    except (KeyboardInterrupt, DownloadCancelled):
                        raise
                    except Exception as e:
                        with counter_lock:
                            error_syms += 1
                            failed_symbols.append(sym)
                        log(f"  ERROR {sym}: {type(e).__name__}: {e}")
                        log(traceback.format_exc().rstrip())

                    with counter_lock:
                        completed += 1
                        c = completed

                    if c % 25 == 0 or c == len(symbols):
                        elapsed = time.time() - t_start
                        pct = c / len(symbols) * 100
                        rate = c / max(elapsed, 0.1) * 60
                        eta = (len(symbols) - c) / max(rate / 60, 0.01)
                        log(f"  [{c}/{len(symbols)}] ({pct:.1f}%) {total_rows:,} rows | "
                            f"{elapsed:.0f}s | {rate:.0f} sym/min | ETA: {eta:.0f}s | "
                            f"cached: {cached_syms} empty: {empty_syms} err: {error_syms} rl: {_rate_limit_hits}")
        except (KeyboardInterrupt, DownloadCancelled):
            STOP_EVENT.set()
            for future in futures:
                future.cancel()
            shutdown_wait = False
            log("  Cancelled. Partial temp files are left in parquets/_tmp_syms for inspection.")
            raise
        finally:
            pool.shutdown(wait=shutdown_wait, cancel_futures=True)

        log(f"  Download done: {total_rows:,} rows | "
            f"{cached_syms} cached | {empty_syms} empty | {error_syms} errors | {_rate_limit_hits} rate limits")
        if failed_symbols:
            preview = ", ".join(failed_symbols[:20])
            more = "" if len(failed_symbols) <= 20 else f", ... +{len(failed_symbols) - 20} more"
            log(f"  Failed symbols: {preview}{more}")

        if total_rows == 0 and cached_syms == 0:
            log(f"  WARNING: no data for {month_str}, skipping")
            continue

        # ── Process per-symbol: gap calc, filter, append to final parquet ──
        log(f"  Processing per-symbol (gap_pct, dedup, filter)...")
        import pyarrow.parquet as pq
        import pyarrow as pa

        DTYPES = PARQUET_DTYPES
        col_order = PARQUET_COL_ORDER

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
    try:
        main()
    except (KeyboardInterrupt, DownloadCancelled):
        STOP_EVENT.set()
        log("Stopped by user.")
        sys.exit(130)
    except UserFacingError as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
    except Exception as exc:
        log(f"FATAL: {type(exc).__name__}: {exc}")
        log(traceback.format_exc().rstrip())
        sys.exit(1)
