"""Serial transport abstraction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import serial


class Transport(Protocol):
    def read(self, size: int = 512) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


class SerialTransport:
    """pyserial-backed CDC ACM transport."""

    def __init__(self, port: str, *, timeout: float = 0.1) -> None:
        self.serial = serial.Serial(
            port=port,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=1.0,
            exclusive=True,
        )

    def read(self, size: int = 512) -> bytes:
        return self.serial.read(size)

    def write(self, data: bytes) -> int:
        written = self.serial.write(data)
        if written is None:
            raise serial.SerialTimeoutException("serial write returned no byte count")
        return written

    def close(self) -> None:
        self.serial.close()


class MemoryTransport:
    """Small deterministic transport for tests and simulations."""

    def __init__(self, on_write: Callable[[bytes], None] | None = None) -> None:
        self.incoming = bytearray()
        self.writes: list[bytes] = []
        self.on_write = on_write
        self.closed = False

    def queue(self, data: bytes) -> None:
        self.incoming.extend(data)

    def read(self, size: int = 512) -> bytes:
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        if self.on_write:
            self.on_write(data)
        return len(data)

    def close(self) -> None:
        self.closed = True
