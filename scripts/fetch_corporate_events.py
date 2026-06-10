from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY_CACHE = ROOT / "docs" / "moving_average_strategy_lab" / "daily_bars_cache.parquet"
DEFAULT_OUT_DIR = ROOT / "data" / "events"


NSE_HEADERS = { 
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.bseindia.com/",
}


@dataclass(frozen=True)
class DateChunk:
    start: date
    end: date

    @property
    def key(self) -> str:
        return f"{self.start:%Y%m%d}_{self.end:%Y%m%d}"


def parse_date(value: str | date | pd.Timestamp) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.Timestamp(value).date()


def month_chunks(start: date, end: date) -> Iterable[DateChunk]:
    cursor = date(start.year, start.month, 1)
    if cursor < start:
        cursor = start
    while cursor <= end:
        if cursor.day != 1:
            next_month = date(cursor.year + (cursor.month // 12), cursor.month % 12 + 1, 1)
        else:
            next_month = date(cursor.year + (cursor.month // 12), cursor.month % 12 + 1, 1)
        chunk_end = min(end, next_month - timedelta(days=1))
        yield DateChunk(cursor, chunk_end)
        cursor = next_month


def day_chunks(start: date, end: date) -> Iterable[DateChunk]:
    cursor = start
    while cursor <= end:
        yield DateChunk(cursor, cursor)
        cursor += timedelta(days=1)


def read_json_gz(path: Path) -> object:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_json_gz(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


class NSEClient:
    def __init__(self, sleep_seconds: float = 0.35) -> None:
        self.session = requests.Session()
        self.sleep_seconds = sleep_seconds

    def get_json(self, url: str, referer: str, params: dict[str, str], retries: int = 3) -> object:
        headers = {**NSE_HEADERS, "Referer": referer}
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                if attempt == 0:
                    self.session.get(referer, headers=headers, timeout=30)
                resp = self.session.get(url, headers=headers, params=params, timeout=90)
                if resp.status_code in {401, 403}:
                    self.session.get("https://www.nseindia.com", headers=headers, timeout=30)
                    resp = self.session.get(url, headers=headers, params=params, timeout=90)
                resp.raise_for_status()
                time.sleep(self.sleep_seconds)
                return resp.json()
            except Exception as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"NSE request failed for {url} {params}: {last_error}")


class BSEClient:
    def __init__(self, sleep_seconds: float = 0.2) -> None:
        self.session = requests.Session()
        self.sleep_seconds = sleep_seconds

    def get_json(self, url: str, params: dict[str, object], retries: int = 3) -> object:
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.get(url, headers=BSE_HEADERS, params=params, timeout=60)
                resp.raise_for_status()
                time.sleep(self.sleep_seconds)
                return resp.json()
            except Exception as exc:
                last_error = exc
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"BSE request failed for {url} {params}: {last_error}")


def cached_fetch(cache_path: Path, refresh: bool, fetch_fn) -> object:
    if cache_path.exists() and not refresh:
        return read_json_gz(cache_path)
    payload = fetch_fn()
    write_json_gz(cache_path, payload)
    return payload


def fetch_nse_announcements(client: NSEClient, out_dir: Path, chunks: Iterable[DateChunk], refresh: bool) -> list[dict]:
    rows: list[dict] = []
    url = "https://www.nseindia.com/api/corporate-announcements"
    referer = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    for chunk in chunks:
        cache_path = out_dir / "raw" / f"nse_announcements_{chunk.key}.json.gz"
        params = {
            "index": "equities",
            "from_date": chunk.start.strftime("%d-%m-%Y"),
            "to_date": chunk.end.strftime("%d-%m-%Y"),
        }
        data = cached_fetch(cache_path, refresh, lambda params=params: client.get_json(url, referer, params))
        if isinstance(data, list):
            rows.extend(data)
        print(f"NSE announcements {chunk.key}: {len(data) if isinstance(data, list) else 0}")
    return rows


def fetch_nse_financial_results(client: NSEClient, out_dir: Path, chunks: Iterable[DateChunk], refresh: bool) -> list[dict]:
    rows: list[dict] = []
    url = "https://www.nseindia.com/api/corporates-financial-results"
    referer = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
    for chunk in chunks:
        cache_path = out_dir / "raw" / f"nse_financial_results_{chunk.key}.json.gz"
        params = {
            "index": "equities",
            "period": "Quarterly",
            "from_date": chunk.start.strftime("%d-%m-%Y"),
            "to_date": chunk.end.strftime("%d-%m-%Y"),
        }
        data = cached_fetch(cache_path, refresh, lambda params=params: client.get_json(url, referer, params))
        if isinstance(data, list):
            rows.extend(data)
        print(f"NSE financial results {chunk.key}: {len(data) if isinstance(data, list) else 0}")
    return rows


def fetch_nse_event_calendar(client: NSEClient, out_dir: Path, chunks: Iterable[DateChunk], refresh: bool) -> list[dict]:
    rows: list[dict] = []
    url = "https://www.nseindia.com/api/event-calendar"
    referer = "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"
    for chunk in chunks:
        cache_path = out_dir / "raw" / f"nse_event_calendar_{chunk.key}.json.gz"
        params = {
            "index": "equities",
            "from_date": chunk.start.strftime("%d-%m-%Y"),
            "to_date": chunk.end.strftime("%d-%m-%Y"),
        }
        data = cached_fetch(cache_path, refresh, lambda params=params: client.get_json(url, referer, params))
        if isinstance(data, list):
            rows.extend(data)
        print(f"NSE event calendar {chunk.key}: {len(data) if isinstance(data, list) else 0}")
    return rows


def fetch_bse_announcements(client: BSEClient, out_dir: Path, chunks: Iterable[DateChunk], refresh: bool) -> list[dict]:
    rows: list[dict] = []
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    for chunk in chunks:
        cache_path = out_dir / "raw" / f"bse_announcements_{chunk.key}.json.gz"

        def fetch_one_day(chunk: DateChunk = chunk) -> list[dict]:
            day_key = chunk.start.strftime("%Y%m%d")
            day_rows: list[dict] = []
            page = 1
            while True:
                params = {
                    "pageno": page,
                    "strCat": "-1",
                    "strPrevDate": day_key,
                    "strScrip": "",
                    "strSearch": "P",
                    "strToDate": day_key,
                    "strType": "C",
                    "subcategory": "",
                }
                data = client.get_json(url, params)
                table = data.get("Table", []) if isinstance(data, dict) else []
                if not table:
                    break
                day_rows.extend(table)
                total_pages = int(table[0].get("TotalPageCnt") or 0)
                if total_pages and page >= total_pages:
                    break
                page += 1
            return day_rows

        data = cached_fetch(cache_path, refresh, fetch_one_day)
        if isinstance(data, list):
            rows.extend(data)
        print(f"BSE announcements {chunk.key}: {len(data) if isinstance(data, list) else 0}")
    return rows


def clean_text(*parts: object) -> str:
    text = " ".join("" if part is None else str(part) for part in parts)
    return re.sub(r"\s+", " ", text).strip()


def classify_event(raw_category: str, title: str, summary: str) -> tuple[str, int]:
    text = clean_text(raw_category, title, summary).lower()
    routine_noise = [
        r"trading window",
        r"newspaper publication",
        r"newspaper advertisement",
        r"copy of newspaper",
        r"clarification",
        r"delayed/non-submission",
        r"non-submission of financial results",
        r"board meeting intimation",
        r"notice of board meeting",
        r"schedule of analysts",
        r"analyst(?:s)?/institutional investor meet",
        r"conference call",
        r"con\. call",
        r"transcript",
        r"recording of",
        r"compliance certificate",
        r"certificate under sebi",
        r"loss of certificate",
        r"issue of duplicate certificate",
        r"secretarial compliance",
        r"shareholding pattern",
        r"voting results",
        r"scrutinizer",
        r"annual report",
        r"related party transaction",
    ]
    if any(re.search(pattern, text) for pattern in routine_noise):
        return "other", 0

    actual_result = (
        re.search(r"\bfinancial result updates?\b", text)
        or re.search(r"\bintegrated filing[- ]+financial\b", text)
        or re.search(r"\bsubmit(?:ted|s)? .*financial results?\b", text)
        or re.search(r"\bapproved .*financial results?\b", text)
        or re.search(r"\boutcome of board meeting.*financial results?\b", text)
        or re.search(r"\bfinancial results? for (?:the )?(?:quarter|period|year)\b", text)
        or re.search(r"\bunaudited financial results?\b", text)
        or re.search(r"\baudited financial results?\b", text)
        or re.search(r"\bquarterly result\b", text)
        or re.search(r"\bannual result\b", text)
    )
    if actual_result:
        return "financial_results", 95
    if re.search(r"\b(letter of award|loa|work order|purchase order|order win|receipt of order|bagged|contract awarded|new order|epc contract|project awarded)\b", text):
        return "big_order", 85
    if re.search(r"\b(acquisition|acquire|merger|amalgamation|scheme of arrangement|slump sale|joint venture|subsidiary|stake purchase|divestment)\b", text):
        return "merger_acquisition", 75
    if re.search(r"\b(government|ministry|rbi approval|regulatory approval|drug approval|usfda|us fda|license|licence|policy change|tariff|tender awarded|railway board|defence ministry|environmental clearance)\b", text):
        return "policy_regulatory", 72
    if re.search(r"\b(promoter|promoter group|open offer|change in control|shareholding)\b", text):
        return "promoter_change", 62
    if re.search(r"\b(appointment|resignation|ceased|chief executive|ceo|chief financial|cfo|managing director|whole-time director|key managerial|kmp)\b", text):
        return "management_change", 58
    if re.search(r"\b(fund raising|fund raise|preferential|qip|qualified institutional|rights issue|warrant|fccb|debenture|allotment of equity)\b", text):
        return "fund_raise", 55
    if re.search(r"\b(investor presentation|press release|earnings presentation|analyst meet)\b", text):
        return "investor_presentation", 40
    return "other", 0


def normalize_nse_announcements(rows: list[dict]) -> pd.DataFrame:
    out = []
    for row in rows:
        title = clean_text(row.get("desc"))
        summary = clean_text(row.get("attchmntText"))
        category, score = classify_event(title, title, summary)
        event_time = pd.to_datetime(row.get("sort_date") or row.get("an_dt"), errors="coerce", dayfirst=True)
        out.append(
            {
                "source": "nse_announcements",
                "source_event_id": row.get("seq_id"),
                "symbol": str(row.get("symbol") or "").upper().strip(),
                "company_name": row.get("sm_name"),
                "isin": row.get("sm_isin"),
                "event_time": event_time,
                "event_date": event_time.date() if not pd.isna(event_time) else pd.NaT,
                "raw_category": title,
                "event_category": category,
                "catalyst_score": score,
                "title": title,
                "summary": summary,
                "attachment_url": row.get("attchmntFile"),
                "source_url": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            }
        )
    return pd.DataFrame(out)


def normalize_nse_financial_results(rows: list[dict]) -> pd.DataFrame:
    out = []
    for row in rows:
        title = clean_text("Financial Results", row.get("period"), row.get("relatingTo"))
        summary = clean_text(row.get("companyName"), row.get("audited"), row.get("consolidated"), row.get("financialYear"))
        event_time = pd.to_datetime(row.get("broadCastDate") or row.get("filingDate"), errors="coerce", dayfirst=True)
        out.append(
            {
                "source": "nse_financial_results",
                "source_event_id": row.get("seqNumber"),
                "symbol": str(row.get("symbol") or "").upper().strip(),
                "company_name": row.get("companyName"),
                "isin": row.get("isin"),
                "event_time": event_time,
                "event_date": event_time.date() if not pd.isna(event_time) else pd.NaT,
                "raw_category": "Financial Results",
                "event_category": "financial_results",
                "catalyst_score": 95,
                "title": title,
                "summary": summary,
                "attachment_url": row.get("xbrl") or row.get("resultDetailedDataLink"),
                "source_url": "https://www.nseindia.com/companies-listing/corporate-filings-financial-results",
                "period_end": row.get("toDate"),
                "relating_to": row.get("relatingTo"),
                "consolidated": row.get("consolidated"),
                "audited": row.get("audited"),
            }
        )
    return pd.DataFrame(out)


def normalize_nse_event_calendar(rows: list[dict]) -> pd.DataFrame:
    out = []
    for row in rows:
        title = clean_text(row.get("purpose"))
        summary = clean_text(row.get("bm_desc"))
        category, score = classify_event("event calendar", title, summary)
        event_time = pd.to_datetime(row.get("date"), errors="coerce", dayfirst=True)
        out.append(
            {
                "source": "nse_event_calendar",
                "source_event_id": clean_text(row.get("symbol"), row.get("date"), row.get("purpose")),
                "symbol": str(row.get("symbol") or "").upper().strip(),
                "company_name": row.get("company"),
                "isin": None,
                "event_time": event_time,
                "event_date": event_time.date() if not pd.isna(event_time) else pd.NaT,
                "raw_category": "Board Meeting Calendar",
                "event_category": category,
                "catalyst_score": max(score - 20, 20) if category != "other" else 20,
                "title": title,
                "summary": summary,
                "attachment_url": None,
                "source_url": "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar",
            }
        )
    return pd.DataFrame(out)


def bse_symbol_guess(row: dict) -> str:
    url = str(row.get("NSURL") or "")
    parts = [part for part in url.strip("/").split("/") if part]
    if len(parts) >= 2:
        return parts[-2].upper().replace("-", "")
    return ""


def normalize_bse_announcements(rows: list[dict]) -> pd.DataFrame:
    out = []
    for row in rows:
        title = clean_text(row.get("CATEGORYNAME"), row.get("SUBCATNAME"))
        summary = clean_text(row.get("NEWSSUB"), row.get("HEADLINE"), row.get("MORE"))
        category, score = classify_event(title, title, summary)
        event_time = pd.to_datetime(row.get("DissemDT") or row.get("NEWS_DT") or row.get("DT_TM"), errors="coerce")
        attachment = row.get("ATTACHMENTNAME")
        attachment_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attachment}" if attachment else None
        out.append(
            {
                "source": "bse_announcements",
                "source_event_id": row.get("NEWSID"),
                "symbol": bse_symbol_guess(row),
                "bse_scrip_code": row.get("SCRIP_CD"),
                "company_name": row.get("SLONGNAME"),
                "isin": None,
                "event_time": event_time,
                "event_date": event_time.date() if not pd.isna(event_time) else pd.NaT,
                "raw_category": title,
                "event_category": category,
                "catalyst_score": score,
                "title": title,
                "summary": summary,
                "attachment_url": attachment_url,
                "source_url": "https://www.bseindia.com/corporates/ann.html",
            }
        )
    return pd.DataFrame(out)


def drop_duplicate_events(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    key_cols = ["source", "source_event_id"]
    has_id = df["source_event_id"].notna() & df["source_event_id"].astype(str).ne("")
    with_id = df.loc[has_id].drop_duplicates(key_cols)
    without_id = df.loc[~has_id].drop_duplicates(["source", "symbol", "event_time", "title", "summary"])
    out = pd.concat([with_id, without_id], ignore_index=True)
    out = out.sort_values(["event_time", "source", "symbol"], na_position="last").reset_index(drop=True)
    return out


def write_outputs(out_dir: Path, frames: list[pd.DataFrame]) -> pd.DataFrame:
    events = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
    events = drop_duplicate_events(events)
    events["event_time"] = pd.to_datetime(events["event_time"], errors="coerce")
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce").dt.date
    events["symbol"] = events["symbol"].fillna("").astype(str).str.upper().str.strip()
    events["is_catalyst"] = (events["catalyst_score"].fillna(0) >= 50) & events["symbol"].ne("")

    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "corporate_events.csv", index=False)
    events.to_parquet(out_dir / "corporate_events.parquet", index=False)
    catalysts = events[events["is_catalyst"]].copy()
    catalysts.to_csv(out_dir / "corporate_catalysts.csv", index=False)
    catalysts.to_parquet(out_dir / "corporate_catalysts.parquet", index=False)

    summary = (
        events.groupby(["source", "event_category"], dropna=False)
        .agg(rows=("symbol", "size"), symbols=("symbol", "nunique"))
        .reset_index()
        .sort_values(["source", "rows"], ascending=[True, False])
    )
    summary.to_csv(out_dir / "event_summary.csv", index=False)
    return events


def infer_date_window(args: argparse.Namespace) -> tuple[date, date]:
    if args.start_date and args.end_date:
        return parse_date(args.start_date), parse_date(args.end_date)
    if args.daily_cache.exists():
        daily = pd.read_parquet(args.daily_cache, columns=["trade_date"])
        start = parse_date(args.start_date) if args.start_date else pd.Timestamp(daily["trade_date"].min()).date()
        end = parse_date(args.end_date) if args.end_date else pd.Timestamp(daily["trade_date"].max()).date()
        return start, end
    if not args.start_date or not args.end_date:
        raise ValueError("Provide --start-date and --end-date when the daily cache is unavailable.")
    return parse_date(args.start_date), parse_date(args.end_date)


def run(args: argparse.Namespace) -> None:
    start, end = infer_date_window(args)
    if start > end:
        raise ValueError("--start-date must be before --end-date")

    sources = set(args.sources)
    out_dir = Path(args.out_dir)
    frames: list[pd.DataFrame] = []
    print(f"Fetching corporate events from {start} to {end}: {', '.join(sorted(sources))}")

    if sources & {"nse_announcements", "nse_financial_results", "nse_event_calendar"}:
        nse = NSEClient(sleep_seconds=args.nse_sleep)
        chunks = list(month_chunks(start, end))
        if "nse_announcements" in sources:
            rows = fetch_nse_announcements(nse, out_dir, chunks, args.refresh)
            frames.append(normalize_nse_announcements(rows))
        if "nse_financial_results" in sources:
            rows = fetch_nse_financial_results(nse, out_dir, chunks, args.refresh)
            frames.append(normalize_nse_financial_results(rows))
        if "nse_event_calendar" in sources:
            rows = fetch_nse_event_calendar(nse, out_dir, chunks, args.refresh)
            frames.append(normalize_nse_event_calendar(rows))

    if "bse_announcements" in sources:
        bse = BSEClient(sleep_seconds=args.bse_sleep)
        chunks = list(day_chunks(start, end))
        rows = fetch_bse_announcements(bse, out_dir, chunks, args.refresh)
        frames.append(normalize_bse_announcements(rows))

    events = write_outputs(out_dir, frames)
    catalysts = events[events["is_catalyst"]] if not events.empty else pd.DataFrame()
    print(f"Wrote {len(events):,} events and {len(catalysts):,} catalyst rows to {out_dir}")
    if not events.empty:
        print(
            events.groupby(["source", "event_category"])
            .size()
            .sort_values(ascending=False)
            .head(20)
            .to_string()
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and normalize NSE/BSE corporate event data for catalyst-aware backtests.")
    parser.add_argument("--start-date", default=None, help="Inclusive start date, YYYY-MM-DD. Defaults to daily cache min date.")
    parser.add_argument("--end-date", default=None, help="Inclusive end date, YYYY-MM-DD. Defaults to daily cache max date.")
    parser.add_argument("--daily-cache", type=Path, default=DEFAULT_DAILY_CACHE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["nse_announcements", "nse_financial_results", "nse_event_calendar"],
        choices=["nse_announcements", "nse_financial_results", "nse_event_calendar", "bse_announcements"],
    )
    parser.add_argument("--refresh", action="store_true", help="Refetch even when raw cache files exist.")
    parser.add_argument("--nse-sleep", type=float, default=0.35)
    parser.add_argument("--bse-sleep", type=float, default=0.20)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
