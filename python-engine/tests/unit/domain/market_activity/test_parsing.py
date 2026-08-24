from swing_atlas.domain.market_activity.models import MarketActivitySource
from swing_atlas.domain.market_activity.parsing import parse_moneycontrol_activity_json


def test_parses_next_data_activity_rows() -> None:
    """Port of engine/src/market_activity.rs's parses_next_data_activity_rows test."""
    source = MarketActivitySource(
        source="moneycontrol",
        metric_type="volume_shockers",
        exchange="NSE",
        index_name="all",
        url="https://example.com",
    )
    payload = {
        "props": {
            "pageProps": {
                "rows": [
                    {
                        "stockName": "Example Industries",
                        "symbol": "EXAMPLE",
                        "currentPrice": "123.45",
                        "perChange": "2.4",
                        "volume": "1,25,000",
                        "avgVol": "25,000",
                        "volMultiplier": "5.0",
                        "value": "12.4",
                    }
                ]
            }
        }
    }

    rows = parse_moneycontrol_activity_json(source, payload, 20)

    assert len(rows) == 1
    assert rows[0].symbol == "EXAMPLE"
    assert rows[0].volume == 125_000
    assert rows[0].volume_multiplier == 5.0
    assert rows[0].price == 123.45
    assert rows[0].change_pct == 2.4


def test_rows_missing_symbol_or_stock_name_are_skipped() -> None:
    source = MarketActivitySource(
        source="moneycontrol", metric_type="most_active", exchange="NSE", index_name="all", url="u"
    )
    payload = {"rows": [{"stockName": "No Symbol Co", "volume": "1000"}]}

    rows = parse_moneycontrol_activity_json(source, payload, 20)

    assert rows == []


def test_duplicate_symbols_are_deduped_keeping_first() -> None:
    source = MarketActivitySource(
        source="moneycontrol", metric_type="most_active", exchange="NSE", index_name="all", url="u"
    )
    payload = {
        "rows": [
            {"stockName": "A Ltd", "symbol": "A", "volume": "100", "currentPrice": "10"},
            {"stockName": "A Ltd Dup", "symbol": "A", "volume": "200", "currentPrice": "99"},
        ]
    }

    rows = parse_moneycontrol_activity_json(source, payload, 20)

    assert len(rows) == 1
    assert rows[0].price == 10.0


def test_respects_max_rows() -> None:
    source = MarketActivitySource(
        source="moneycontrol", metric_type="most_active", exchange="NSE", index_name="all", url="u"
    )
    payload = {
        "rows": [{"stockName": f"Co {i}", "symbol": f"SYM{i}", "volume": "100"} for i in range(5)]
    }

    rows = parse_moneycontrol_activity_json(source, payload, 3)

    assert len(rows) == 3
