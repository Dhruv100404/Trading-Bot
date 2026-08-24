"""Tests for Dhan's binary market-feed tick parser -- mirrors
engine/src/api/swing.rs's parse_dhan_quote_packets/read_f32_le/read_i32_le.

Packets are hand-built with struct.pack against the documented wire layout
(code:u8, packet_len:i16 LE, pad:u8, security_id:i32 LE, then a code-specific
payload) rather than a captured Dhan session, since none was available --
each byte offset here is cross-checked against the Rust source's literal
offsets (8, 22, 34/38/42/46 for Full; 46/50/54/58 for Quote) so a fixture
mistake would show up as a wrong-offset test failure, not a silent pass.
"""

import struct

from swing_atlas.domain.swing.tick_parser import (
    parse_dhan_quote_packets,
    read_f32_le,
    read_i32_le,
)


def _header(code: int, packet_len: int, security_id: int) -> bytes:
    return struct.pack("<Bhxi", code, packet_len, security_id)


def _ticker_packet(security_id: int, last_price: float) -> bytes:
    header = _header(2, 16, security_id)
    body = struct.pack("<f", last_price)
    return header + body + b"\x00" * (16 - len(header) - len(body))


def _prev_close_packet(security_id: int, prev_close: float) -> bytes:
    header = _header(6, 16, security_id)
    body = struct.pack("<f", prev_close)
    return header + body + b"\x00" * (16 - len(header) - len(body))


def _full_packet(
    security_id: int,
    last_price: float,
    volume: int,
    open_: float,
    prev_close: float,
    high: float,
    low: float,
) -> bytes:
    packet = bytearray(50)
    packet[0:8] = _header(4, 50, security_id)
    struct.pack_into("<f", packet, 8, last_price)
    struct.pack_into("<i", packet, 22, volume)
    struct.pack_into("<f", packet, 34, open_)
    struct.pack_into("<f", packet, 38, prev_close)
    struct.pack_into("<f", packet, 42, high)
    struct.pack_into("<f", packet, 46, low)
    return bytes(packet)


def _quote_packet(
    security_id: int,
    last_price: float,
    volume: int,
    open_: float,
    prev_close: float,
    high: float,
    low: float,
) -> bytes:
    packet = bytearray(62)
    packet[0:8] = _header(8, 62, security_id)
    struct.pack_into("<f", packet, 8, last_price)
    struct.pack_into("<i", packet, 22, volume)
    struct.pack_into("<f", packet, 46, open_)
    struct.pack_into("<f", packet, 50, prev_close)
    struct.pack_into("<f", packet, 54, high)
    struct.pack_into("<f", packet, 58, low)
    return bytes(packet)


def test_read_f32_le_and_read_i32_le_bounds_checking() -> None:
    packet = struct.pack("<f", 123.5)
    assert read_f32_le(packet, 0) == 123.5
    assert read_f32_le(packet, 1) == 0.0  # insufficient bytes remaining

    ipacket = struct.pack("<i", 42)
    assert read_i32_le(ipacket, 0) == 42
    assert read_i32_le(ipacket, 1) == 0


def test_ticker_packet_extracts_last_price_only() -> None:
    data = _ticker_packet(security_id=1333, last_price=755.5)

    [tick] = parse_dhan_quote_packets(data)

    assert tick.security_id == "1333"
    assert tick.last_price == 755.5
    assert tick.open == 0.0
    assert tick.volume == 0


def test_prev_close_packet_extracts_prev_close_only() -> None:
    data = _prev_close_packet(security_id=999, prev_close=100.25)

    [tick] = parse_dhan_quote_packets(data)

    assert tick.security_id == "999"
    assert tick.prev_close == 100.25
    assert tick.last_price == 0.0


def test_full_packet_extracts_all_fields_at_documented_offsets() -> None:
    data = _full_packet(
        security_id=1333,
        last_price=755.5,
        volume=12345,
        open_=750.0,
        prev_close=752.0,
        high=760.0,
        low=748.0,
    )

    [tick] = parse_dhan_quote_packets(data)

    assert tick.security_id == "1333"
    assert tick.last_price == 755.5
    assert tick.volume == 12345
    assert tick.open == 750.0
    assert tick.prev_close == 752.0
    assert tick.high == 760.0
    assert tick.low == 748.0


def test_quote_packet_extracts_all_fields_at_documented_offsets() -> None:
    data = _quote_packet(
        security_id=2001,
        last_price=100.0,
        volume=5000,
        open_=98.0,
        prev_close=97.0,
        high=101.0,
        low=96.5,
    )

    [tick] = parse_dhan_quote_packets(data)

    assert tick.security_id == "2001"
    assert tick.last_price == 100.0
    assert tick.volume == 5000
    assert tick.open == 98.0
    assert tick.prev_close == 97.0
    assert tick.high == 101.0
    assert tick.low == 96.5


def test_multiple_packets_in_one_buffer_are_all_parsed() -> None:
    data = _ticker_packet(1, 10.0) + _prev_close_packet(2, 20.0) + _ticker_packet(3, 30.0)

    ticks = parse_dhan_quote_packets(data)

    assert [t.security_id for t in ticks] == ["1", "2", "3"]
    assert ticks[0].last_price == 10.0
    assert ticks[1].prev_close == 20.0
    assert ticks[2].last_price == 30.0


def test_unrecognized_packet_code_is_skipped_but_does_not_break_parsing() -> None:
    unknown = _header(99, 16, 5) + b"\x00" * 8  # code 99 isn't handled -> no tick
    data = unknown + _ticker_packet(6, 42.0)

    ticks = parse_dhan_quote_packets(data)

    assert len(ticks) == 1
    assert ticks[0].security_id == "6"


def test_packet_too_short_for_its_code_is_skipped() -> None:
    # code=4 (Full Packet) needs len>=50 but this one is only 16 bytes.
    short_full = _header(4, 16, 7) + b"\x00" * 8
    data = short_full + _ticker_packet(8, 55.0)

    ticks = parse_dhan_quote_packets(data)

    assert len(ticks) == 1
    assert ticks[0].security_id == "8"


def test_truncated_buffer_is_treated_as_the_final_packet() -> None:
    # packet_len claims 50 bytes but the buffer only has 20 -- falls back to
    # "rest of buffer" per the Rust original, then the loop terminates.
    header = _header(4, 50, 42)
    data = header + b"\x00" * 12  # 20 bytes total, less than the claimed 50

    ticks = parse_dhan_quote_packets(data)

    assert ticks == []  # 20 bytes < Full Packet's 50-byte minimum, so no tick


def test_empty_buffer_returns_no_ticks() -> None:
    assert parse_dhan_quote_packets(b"") == []


def test_buffer_shorter_than_header_returns_no_ticks() -> None:
    assert parse_dhan_quote_packets(b"\x00" * 5) == []
