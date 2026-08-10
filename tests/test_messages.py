import pytest

from swolectl.messages import (
    ArmPosition,
    CartPosition,
    ColumnRotation,
    DeviceAnnouncement,
    ElectricalTelemetry,
    MotorTelemetry,
    ResistanceMode,
    ResistanceProfile,
    ResistanceState,
    encode_resistance_toggle,
)

ANNOUNCEMENT = bytes.fromhex(
    "00 12 02 05 01 01 01 01 01 00 04 00 30 30 31 00 "
    "02 00 0a 00 35 30 30 2d 30 31 30 32 00 00 03 00 "
    "0a 00 30 30 30 31 35 30 31 33 00 00 04 00 0a 00 "
    "32 30 32 30 31 31 31 36 00 66 05 00 04 00 30 34 00 00"
)


def test_device_announcement_firmware_version() -> None:
    announcement = DeviceAnnouncement.decode(ANNOUNCEMENT)
    assert announcement.firmware_version == "5.2.18.0"
    assert announcement.part_number == "500-0102"


def test_arm_position_packed_fields() -> None:
    packed = 7 | (12 << 8) | (1 << 16) | (1 << 20) | (1 << 21)
    arm = ArmPosition.decode(packed.to_bytes(4, "little"))
    assert arm.telescope_position == 7
    assert arm.rotation_position == 12
    assert arm.side_name == "right"
    assert arm.is_rotation_locked
    assert arm.is_rotation_and_telescope_locked


def test_separate_arm_adjustment_events() -> None:
    assert CartPosition.decode(bytes.fromhex("01 08")) == CartPosition(1, 8)
    assert ColumnRotation.decode(bytes.fromhex("00 04 00 05")) == ColumnRotation(
        left_position=0,
        left_unlocked=True,
        right_position=0,
        right_unlocked=True,
    )


def test_basic_resistance_profile() -> None:
    profile = ResistanceProfile.basic(5)
    assert profile.encode().hex(" ") == (
        "32 00 00 00 00 00 00 00 00 00 00 00 "
        "0a 00 00 00 00 00 02 00 14 00 00 00"
    )
    assert ResistanceProfile.decode(profile.encode()) == profile


def test_toggle_payloads() -> None:
    assert encode_resistance_toggle(ResistanceState.ENABLED) == b"\0\0\2\0"
    assert encode_resistance_toggle(ResistanceState.DISABLED) == b"\1\0\2\0"


def test_motor_telemetry_resistance_fields() -> None:
    words = [0] * 25
    words[2] = 160
    words[3] = 160
    words[9] = 110
    payload = MotorTelemetry._STRUCT.pack(*words)
    telemetry = MotorTelemetry.decode(payload)
    assert telemetry.applied_resistance_lb == 16
    assert telemetry.target_resistance_lb == 16
    assert telemetry.rack_weight_lb == 11
    assert telemetry.active
    assert telemetry.power_watts is None


def test_profile_rejects_non_finite_weight() -> None:
    with pytest.raises(ValueError):
        ResistanceProfile.basic(float("nan"))


def test_motor_telemetry_signed_rack_weight() -> None:
    words = [0] * 25
    words[9] = 0xFF9C
    telemetry = MotorTelemetry.decode(MotorTelemetry._STRUCT.pack(*words))
    assert telemetry.rack_weight_lb == -10


def test_device_enabled_bit() -> None:
    words = [0] * 25
    words[16] = 0x00A5
    assert MotorTelemetry.decode(MotorTelemetry._STRUCT.pack(*words)).device_enabled is False
    words[16] = 0x66A4
    assert MotorTelemetry.decode(MotorTelemetry._STRUCT.pack(*words)).device_enabled is True


def test_electrical_telemetry() -> None:
    values = [123_456, -2_500, 3_000, 4_000, 1, 2, 3, 4, 5, 6, 7, 8]
    payload = b"\0" * 8 + b"".join(value.to_bytes(4, "little", signed=True) for value in values)
    telemetry = ElectricalTelemetry.decode(payload)
    assert telemetry.motor_mechanical_power_w == 123.456
    assert telemetry.motor_electrical_power_w == -2.5


@pytest.mark.parametrize(
    ("mode", "level", "modifier_02", "modifier_04", "flags"),
    [
        (ResistanceMode.BASIC, 1, 0, 0, 0),
        (ResistanceMode.SPOTTER, 1, 0, 0, 0x40),
        (ResistanceMode.DROP_SET, 1, 0, 0, 0x80),
        (ResistanceMode.CHAINS, 1, 0, 10, 0),
        (ResistanceMode.CHAINS, 2, 0, 30, 0),
        (ResistanceMode.ECCENTRIC, 1, 10, 0, 0),
        (ResistanceMode.ECCENTRIC, 2, 20, 0, 0),
        (ResistanceMode.SMART_FLEX, 1, 0, 10, 4),
    ],
)
def test_resistance_modes(
    mode: ResistanceMode,
    level: int,
    modifier_02: int,
    modifier_04: int,
    flags: int,
) -> None:
    profile = ResistanceProfile.for_mode(5, mode, level=level)
    assert profile.base_tenths_lb == 50
    assert profile.modifier_02 == modifier_02
    assert profile.modifier_04 == modifier_04
    assert profile.flags == flags


def test_drop_set_profile_uses_controller_defaults() -> None:
    profile = ResistanceProfile.for_mode(50, ResistanceMode.DROP_SET)
    assert profile.unknown_06 == 8000
    assert profile.unknown_08 == 800
    assert profile.flags == 0x80
    assert profile.encode().hex(" ") == (
        "f4 01 00 00 00 00 40 1f 20 03 00 00 "
        "0a 00 80 00 00 00 02 00 14 00 00 00"
    )
