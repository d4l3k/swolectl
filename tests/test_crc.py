from swolectl.crc import crc16_bypass


def test_standard_check_value() -> None:
    assert crc16_bypass(b"123456789") == 0xFEE8
