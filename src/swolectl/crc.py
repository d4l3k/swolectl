"""CRC used by the serial protocol."""


def crc16_bypass(data: bytes | bytearray | memoryview, crc: int = 0) -> int:
    """Return CRC-16/BUYPASS (poly 0x8005, init 0, non-reflected)."""
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc
