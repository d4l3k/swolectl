import pytest

from swolectl.frame import Frame, FrameStream, ProtocolError


def test_frame_round_trip() -> None:
    frame = Frame(
        message_type=0x1028,
        payload=b"\x00\x00\x02\x00",
        sequence=42,
        source=7,
        destination=1,
        timestamp_ms=123456,
    )
    assert Frame.decode(frame.encode()) == frame


def test_crc_corruption_is_rejected() -> None:
    encoded = bytearray(Frame(message_type=1, payload=b"hello").encode())
    encoded[-1] ^= 1
    with pytest.raises(ProtocolError, match="CRC mismatch"):
        Frame.decode(bytes(encoded))


def test_stream_handles_noise_and_split_frames() -> None:
    first = Frame(message_type=0x1000, payload=b"abc", sequence=1).encode()
    second = Frame(message_type=0x1024, payload=bytes(50), sequence=2).encode()
    stream = FrameStream()
    assert stream.feed(b"noise" + first[:7]) == []
    decoded = stream.feed(first[7:] + second)
    assert [frame.message_type for frame in decoded] == [0x1000, 0x1024]
    assert stream.discarded_bytes == 5
