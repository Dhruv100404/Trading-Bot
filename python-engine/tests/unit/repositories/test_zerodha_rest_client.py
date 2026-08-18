from swing_atlas.repositories.zerodha.rest_client import normalize_kite_positions


def test_long_position_uses_realised_zero_and_pnl_as_realized() -> None:
    net = [
        {
            "tradingsymbol": "RELIANCE",
            "instrument_token": 738561,
            "quantity": 10,
            "buy_price": 2500.0,
            "sell_price": 0.0,
            "pnl": 150.0,
            "unrealised": 150.0,
            "realised": 0.0,
            "exchange": "NSE",
            "product": "CNC",
        }
    ]

    [position] = normalize_kite_positions(net)

    assert position["positionType"] == "LONG"
    assert position["securityId"] == "738561"
    assert position["costPrice"] == 2500.0
    # net_qty != 0 -> unrealizedProfit comes from `unrealised`, realizedProfit from `realised`
    assert position["unrealizedProfit"] == 150.0
    assert position["realizedProfit"] == 0.0


def test_closed_position_uses_pnl_as_realized_and_zero_unrealized() -> None:
    net = [
        {
            "tradingsymbol": "TCS",
            "instrument_token": 2953217,
            "quantity": 0,
            "buy_price": 3500.0,
            "sell_price": 3550.0,
            "pnl": 500.0,
            "unrealised": 0.0,
            "realised": 500.0,
            "exchange": "NSE",
            "product": "CNC",
        }
    ]

    [position] = normalize_kite_positions(net)

    assert position["positionType"] == "CLOSED"
    # net_qty == 0 -> realizedProfit comes from `pnl`, unrealizedProfit forced to 0
    assert position["realizedProfit"] == 500.0
    assert position["unrealizedProfit"] == 0.0


def test_short_position_and_cost_price_falls_back_to_sell_avg() -> None:
    net = [
        {
            "tradingsymbol": "INFY",
            "instrument_token": 408065,
            "quantity": -5,
            "buy_price": 0.0,
            "sell_price": 1500.0,
            "pnl": 0.0,
            "unrealised": -25.0,
            "realised": 0.0,
        }
    ]

    [position] = normalize_kite_positions(net)

    assert position["positionType"] == "SHORT"
    assert position["costPrice"] == 1500.0
    assert position["exchangeSegment"] == "NSE"  # default when "exchange" is missing
    assert position["productType"] == "MIS"  # default when "product" is missing


def test_empty_net_returns_empty_list() -> None:
    assert normalize_kite_positions([]) == []
