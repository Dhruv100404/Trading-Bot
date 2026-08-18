from swing_atlas.domain.news.nse_deals_parsing import parse_nse_daily_report


def test_parses_current_nse_historical_csv_headers() -> None:
    """Port of engine/src/news.rs's parses_current_nse_historical_csv_headers test."""
    csv_text = (
        '﻿"Date ","Symbol ","Security Name ","Client Name ",'
        '"Buy / Sell ","Quantity Traded ",'
        '"Trade Price / Wght. Avg. Price ","Remarks "\n'
        '"10-JUL-2026","DEMO","Demo Limited","Buyer LLP",'
        '"BUY","1,00,000","125.50","-"\n'
    )

    rows = parse_nse_daily_report(csv_text, "BULK", "2026-07-11 10:00:00")

    assert len(rows) == 1
    assert rows[0].symbol == "DEMO"
    assert rows[0].deal_date == "2026-07-10"
    assert rows[0].side == "BUY"
    assert rows[0].quantity == 100_000.0
    assert rows[0].value_lakh == 125.5


_HEADER_ROW = (
    '"Date","Symbol","Security Name","Client Name","Buy/Sell",'
    '"Quantity Traded","Trade Price / Wght. Avg. Price"\n'
)


def test_skips_no_records_row() -> None:
    csv_text = _HEADER_ROW + '"-","NO RECORDS","","","","",""\n'

    rows = parse_nse_daily_report(csv_text, "BULK", "2026-07-11 10:00:00")

    assert rows == []


def test_skips_row_with_unparseable_date() -> None:
    csv_text = _HEADER_ROW + '"not-a-date","DEMO","Demo Ltd","Buyer","BUY","100","10"\n'

    rows = parse_nse_daily_report(csv_text, "BULK", "2026-07-11 10:00:00")

    assert rows == []
