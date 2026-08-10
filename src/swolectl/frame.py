"""Wire framing and incremental stream decoding."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .crc import crc16_bypass

MAGIC = 0x1AA055AA
MAGIC_BYTES = struct.pack("<I", MAGIC)
HEADER = struct.Struct("<IHHHHI")
MAX_PAYLOAD = 0x1F0


class ProtocolError(ValueError):
    """A malformed or invalid protocol frame."""


@dataclass(frozen=True, slots=True)
class Frame:
    """One protocol frame."""

    message_type: int
    payload: bytes = b""
    sequence: int = 0
    source: int = 7
    destination: int = 1
    timestamp_ms: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.message_type <= 0xFFFF:
            raise ValueError("message_type must fit uint16")
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload exceeds {MAX_PAYLOAD} bytes")
        if not 0 <= self.sequence <= 0xFF:
            raise ValueError("sequence must fit uint8")
        if not 0 <= self.source <= 0xF or not 0 <= self.destination <= 0xF:
            raise ValueError("source and destination must fit four bits")
        if not 0 <= self.timestamp_ms <= 0xFFFFFFFF:
            raise ValueError("timestamp_ms must fit uint32")

    @property
    def route(self) -> int:
        return self.sequence | (self.source << 8) | (self.destination << 12)

    def encode(self) -> bytes:
        header = HEADER.pack(
            MAGIC,
            self.message_type,
            len(self.payload),
            self.route,
            0,
            self.timestamp_ms,
        )
        packet = bytearray(header + self.payload)
        struct.pack_into("<H", packet, 10, crc16_bypass(packet))
        return bytes(packet)

    @classmethod
    def decode(cls, packet: bytes, *, verify_crc: bool = True) -> Frame:
        if len(packet) < HEADER.size:
            raise ProtocolError("truncated header")
        magic, message_type, size, route, stored_crc, timestamp = HEADER.unpack_from(packet)
        if magic != MAGIC:
            raise ProtocolError("invalid magic")
        if size > MAX_PAYLOAD:
            raise ProtocolError("payload length exceeds protocol maximum")
        expected = HEADER.size + size
        if len(packet) != expected:
            raise ProtocolError(f"frame is {len(packet)} bytes; expected {expected}")
        if verify_crc:
            checked = bytearray(packet)
            checked[10:12] = b"\0\0"
            actual_crc = crc16_bypass(checked)
            if actual_crc != stored_crc:
                raise ProtocolError(f"CRC mismatch: stored {stored_crc:04x}, got {actual_crc:04x}")
        return cls(
            message_type=message_type,
            payload=packet[HEADER.size:],
            sequence=route & 0xFF,
            source=(route >> 8) & 0xF,
            destination=(route >> 12) & 0xF,
            timestamp_ms=timestamp,
        )


class FrameStream:
    """Incremental decoder that resynchronizes on frame magic."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.discarded_bytes = 0
        self.invalid_frames = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        result: list[Frame] = []
        while True:
            start = self._buffer.find(MAGIC_BYTES)
            if start < 0:
                retain = min(len(self._buffer), len(MAGIC_BYTES) - 1)
                self.discarded_bytes += len(self._buffer) - retain
                if retain:
                    del self._buffer[:-retain]
                else:
                    self._buffer.clear()
                break
            if start:
                self.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < HEADER.size:
                break
            size = struct.unpack_from("<H", self._buffer, 6)[0]
            if size > MAX_PAYLOAD:
                self.invalid_frames += 1
                del self._buffer[0]
                continue
            end = HEADER.size + size
            if len(self._buffer) < end:
                break
            packet = bytes(self._buffer[:end])
            try:
                result.append(Frame.decode(packet))
            except ProtocolError:
                self.invalid_frames += 1
                del self._buffer[0]
                continue
            del self._buffer[:end]
        return result
