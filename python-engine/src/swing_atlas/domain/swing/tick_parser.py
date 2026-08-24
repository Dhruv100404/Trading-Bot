"""Dhan's binary market-feed tick parser -- mirrors
engine/src/api/swing.rs's parse_dhan_quote_packets/read_f32_le/read_i32_le.

Dhan's v2 feed multiplexes several packet types into one binary WebSocket
message: a common 8-byte header (code:u8, packet_len:i16 LE, [1 pad byte],
security_id:i32 LE) followed by a code-specific payload. Only 4 codes carry
price data we use; everything else (index packets, OI packets, disconnect
codes, etc.) is silently skipped, exactly matching the Rust original's `_ =>
{}` catch-all -- Dhan adds packet types over time and this must not break on
an unrecognized one.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_HEADER_LEN = 8

# code -> "Full Packet" (LTP + OHLC + volume)
_FULL_PACKET = 4
_FULL_PACKET_MIN_LEN = 50
# code -> "Ticker Packet" (LTP only)
_TICKER_PACKET = 2
_TICKER_PACKET_MIN_LEN = 16
# code -> "Prev Close Packet" (previous day close only)
_PREV_CLOSE_PACKET = 6
_PREV_CLOSE_PACKET_MIN_LEN = 16
# code -> "Quote Packet" (LTP + OHLC + volume, different offsets than Full)
_QUOTE_PACKET = 8
_QUOTE_PACKET_MIN_LEN = 62


@dataclass
class DhanQuoteTick:
    security_id: str = ""
    last_price: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    prev_close: float = 0.0
    volume: int = 0


def read_f32_le(packet: bytes, offset: int) -> float:
    if len(packet) < offset + 4:
        return 0.0
    value: float = struct.unpack_from("<f", packet, offset)[0]
    return value


def read_i32_le(packet: bytes, offset: int) -> int:
    if len(packet) < offset + 4:
        return 0
    value: int = struct.unpack_from("<i", packet, offset)[0]
    return value


def parse_dhan_quote_packets(data: bytes) -> list[DhanQuoteTick]:
    ticks: list[DhanQuoteTick] = []
    offset = 0
    while offset + _HEADER_LEN <= len(data):
        code = data[offset]
        raw_packet_len = max(struct.unpack_from("<h", data, offset + 1)[0], 0)
        packet_len = (
            raw_packet_len
            if raw_packet_len >= _HEADER_LEN and offset + raw_packet_len <= len(data)
            else len(data) - offset
        )
        packet = data[offset : offset + packet_len]
        security_id = str(struct.unpack_from("<i", packet, 4)[0])

        if code == _FULL_PACKET and len(packet) >= _FULL_PACKET_MIN_LEN:
            ticks.append(
                DhanQuoteTick(
                    security_id=security_id,
                    last_price=read_f32_le(packet, 8),
                    volume=max(read_i32_le(packet, 22), 0),
                    open=read_f32_le(packet, 34),
                    prev_close=read_f32_le(packet, 38),
                    high=read_f32_le(packet, 42),
                    low=read_f32_le(packet, 46),
                )
            )
        elif code == _TICKER_PACKET and len(packet) >= _TICKER_PACKET_MIN_LEN:
            ticks.append(DhanQuoteTick(security_id=security_id, last_price=read_f32_le(packet, 8)))
        elif code == _PREV_CLOSE_PACKET and len(packet) >= _PREV_CLOSE_PACKET_MIN_LEN:
            ticks.append(DhanQuoteTick(security_id=security_id, prev_close=read_f32_le(packet, 8)))
        elif code == _QUOTE_PACKET and len(packet) >= _QUOTE_PACKET_MIN_LEN:
            ticks.append(
                DhanQuoteTick(
                    security_id=security_id,
                    last_price=read_f32_le(packet, 8),
                    volume=max(read_i32_le(packet, 22), 0),
                    open=read_f32_le(packet, 46),
                    prev_close=read_f32_le(packet, 50),
                    high=read_f32_le(packet, 54),
                    low=read_f32_le(packet, 58),
                )
            )

        if packet_len == 0:
            break
        offset += packet_len

    return ticks
