"""Typed protocol payloads."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import IntEnum, StrEnum


class MessageType(IntEnum):
    BOOT_0 = 0x0000
    BOOT_1 = 0x0001
    BOOT_2 = 0x0002
    ENTER_ACTIVE = 0x0003
    RESISTANCE_PROFILE = 0x0013
    DEVICE_ANNOUNCE = 0x1000
    STATUS = 0x1001
    BRINGUP_1005 = 0x1005
    TEXT_LOG = 0x1010
    SLOW_STATUS = 0x1018
    STATUS_1019 = 0x1019
    CART_POSITION = 0x1020
    COLUMN_ROTATION = 0x1021
    DEVICE_INFO = 0x1022
    PROFILE_ECHO = 0x1023
    MOTOR_TELEMETRY = 0x1024
    ARM_POSITION = 0x1025
    BRINGUP_1026 = 0x1026
    RESISTANCE_TOGGLE = 0x1028
    BRINGUP_102A = 0x102A
    BRINGUP_102C = 0x102C
    ELECTRICAL = 0x102E


class ResistanceState(IntEnum):
    ENABLED = 0
    DISABLED = 1


class ResistanceMode(StrEnum):
    BASIC = "basic"
    SPOTTER = "spotter"
    DROP_SET = "drop_set"
    CHAINS = "chains"
    ECCENTRIC = "eccentric"
    SMART_FLEX = "smart_flex"


@dataclass(frozen=True, slots=True)
class ResistanceProfile:
    """Twenty-four-byte resistance profile.

    Only ``base_tenths_lb`` has fully proven units. Modifier fields and flags
    reproduce known profiles but should be changed only by informed users.
    """

    base_tenths_lb: int
    modifier_02: int = 0
    modifier_04: int = 0
    unknown_06: int = 0
    unknown_08: int = 0
    unknown_0a: int = 0
    constant_0c: int = 10
    flags: int = 0
    unknown_10: int = 0
    mode: int = 2
    constant_14: int = 20
    unknown_16: int = 0

    _STRUCT = struct.Struct("<12H")

    @classmethod
    def basic(cls, pounds: float) -> ResistanceProfile:
        if not math.isfinite(pounds):
            raise ValueError("pounds must be finite")
        return cls(base_tenths_lb=round(pounds * 10))

    @classmethod
    def for_mode(
        cls,
        pounds: float,
        mode: ResistanceMode,
        *,
        intensity_percent: float = 25.0,
    ) -> ResistanceProfile:
        """Build one of the known advanced-mode profiles.

        ``intensity_percent`` is converted to an added load relative to the
        base resistance. Chains supports 0-100%; Eccentric and Smart Flex
        support 0-60%.
        """
        base = cls.basic(pounds).base_tenths_lb
        if mode is ResistanceMode.BASIC:
            return cls(base_tenths_lb=base)
        if mode is ResistanceMode.SPOTTER:
            return cls(base_tenths_lb=base, flags=0x0040)
        if mode is ResistanceMode.DROP_SET:
            return cls(
                base_tenths_lb=base,
                unknown_06=8000,
                unknown_08=800,
                flags=0x0080,
            )
        if not math.isfinite(intensity_percent):
            raise ValueError("mode intensity must be finite")
        maximum = 100.0 if mode is ResistanceMode.CHAINS else 60.0
        if not 0.0 <= intensity_percent <= maximum:
            raise ValueError(f"{mode.value} intensity must be between 0 and {maximum:g}%")
        modifier = math.floor(base * intensity_percent / 100.0 + 0.5)
        if mode is ResistanceMode.CHAINS:
            return cls(base_tenths_lb=base, modifier_04=modifier)
        if mode is ResistanceMode.ECCENTRIC:
            return cls(base_tenths_lb=base, modifier_02=modifier)
        if mode is ResistanceMode.SMART_FLEX:
            return cls(base_tenths_lb=base, modifier_04=modifier, flags=0x0004)
        raise ValueError(f"unsupported resistance mode: {mode}")

    @property
    def peak_tenths_lb(self) -> int:
        """Maximum configured load implied by the base and modifier fields."""
        return self.base_tenths_lb + self.modifier_02 + self.modifier_04

    def encode(self) -> bytes:
        values = (
            self.base_tenths_lb,
            self.modifier_02,
            self.modifier_04,
            self.unknown_06,
            self.unknown_08,
            self.unknown_0a,
            self.constant_0c,
            self.flags,
            self.unknown_10,
            self.mode,
            self.constant_14,
            self.unknown_16,
        )
        if any(not 0 <= value <= 0xFFFF for value in values):
            raise ValueError("profile values must fit uint16")
        return self._STRUCT.pack(*values)

    @classmethod
    def decode(cls, payload: bytes) -> ResistanceProfile:
        if len(payload) != cls._STRUCT.size:
            raise ValueError("resistance profile must be 24 bytes")
        return cls(*cls._STRUCT.unpack(payload))


def encode_resistance_toggle(state: ResistanceState, mode: int = 2) -> bytes:
    if not 0 <= mode <= 0xFFFF:
        raise ValueError("mode must fit uint16")
    return struct.pack("<HH", state, mode)


@dataclass(frozen=True, slots=True)
class MotorTelemetry:
    """Decoded 50-byte high-rate motor sample."""

    raw_words: tuple[int, ...]
    control_mode: int
    motor_tension_lb: float
    total_weight_lb: float
    base_weight_lb: float
    eccentric_weight_lb: float
    rom_weight_lb: float
    rom_weight_mode: int
    spotted_weight_lb: float
    ramp_weight_lb: float
    rack_weight_lb: float
    motor_position: float
    motor_speed: float
    left_cable_position: float
    left_cable_speed: float
    right_cable_position: float
    right_cable_speed: float
    status: int
    rep_count: int
    power_watts: float | None = None

    _STRUCT = struct.Struct("<25H")

    @property
    def applied_resistance_lb(self) -> float:
        return self.total_weight_lb

    @property
    def target_resistance_lb(self) -> float:
        """Compatibility alias; this field is actually applied base weight."""
        return self.base_weight_lb

    @property
    def active(self) -> bool:
        return self.device_enabled

    @property
    def device_enabled(self) -> bool:
        """Device-reported enable latch inferred from telemetry word 16."""
        return (self.status & 1) == 0

    @property
    def is_racked(self) -> bool:
        return bool(self.status & 1)

    @property
    def is_grounded(self) -> bool:
        return bool(self.status & 0x0C)

    @classmethod
    def decode(cls, payload: bytes) -> MotorTelemetry:
        if len(payload) != cls._STRUCT.size:
            raise ValueError("motor telemetry must be 50 bytes")
        words = cls._STRUCT.unpack(payload)
        signed = tuple(value - 0x10000 if value >= 0x8000 else value for value in words)
        return cls(
            raw_words=words,
            control_mode=words[0],
            motor_tension_lb=signed[1] / 10.0,
            total_weight_lb=signed[2] / 10.0,
            base_weight_lb=signed[3] / 10.0,
            eccentric_weight_lb=signed[4] / 10.0,
            rom_weight_lb=signed[5] / 10.0,
            rom_weight_mode=words[6],
            spotted_weight_lb=signed[7] / 10.0,
            ramp_weight_lb=signed[8] / 10.0,
            rack_weight_lb=signed[9] / 10.0,
            motor_position=signed[10] / 10.0,
            motor_speed=signed[11] / 10.0,
            left_cable_position=signed[12] / 10.0,
            left_cable_speed=signed[13] / 10.0,
            right_cable_position=signed[14] / 10.0,
            right_cable_speed=signed[15] / 10.0,
            status=words[16],
            rep_count=signed[17],
        )


@dataclass(frozen=True, slots=True)
class ElectricalTelemetry:
    """Candidate power telemetry mapping for message 0x102e."""

    raw: bytes
    motor_mechanical_power_w: float
    motor_electrical_power_w: float
    motor_thermal_power_w: float
    supply_power_w: float
    phase_current_a: tuple[float, float, float]
    phase_voltage_v: tuple[float, float, float]
    dc_link_current_a: float
    dc_link_voltage_v: float

    @classmethod
    def decode(cls, payload: bytes) -> ElectricalTelemetry:
        if len(payload) < 56:
            raise ValueError("electrical telemetry must be at least 56 bytes")
        values = struct.unpack_from("<12i", payload, 8)
        return cls(
            raw=payload,
            motor_mechanical_power_w=values[0] / 1000.0,
            motor_electrical_power_w=values[1] / 1000.0,
            motor_thermal_power_w=values[2] / 1000.0,
            supply_power_w=values[3] / 1000.0,
            phase_current_a=(values[4] / 1000.0, values[5] / 1000.0, values[6] / 1000.0),
            phase_voltage_v=(values[7] / 1000.0, values[8] / 1000.0, values[9] / 1000.0),
            dc_link_current_a=values[10] / 1000.0,
            dc_link_voltage_v=values[11] / 1000.0,
        )


@dataclass(frozen=True, slots=True)
class ArmPosition:
    """Four-byte arm-position event carried by message 0x1025."""

    telescope_position: int
    rotation_position: int
    side: int
    is_rotation_locked: bool
    is_rotation_and_telescope_locked: bool
    raw_flags: int

    @property
    def angle(self) -> int:
        """Physical arm angle reported in the second payload byte."""
        return self.rotation_position

    @property
    def telescope_setting(self) -> int:
        """Discrete telescope/arm-position setting in the first byte."""
        return self.telescope_position

    @property
    def side_name(self) -> str:
        return "left" if self.side == 0 else "right"

    @classmethod
    def decode(cls, payload: bytes) -> ArmPosition:
        if len(payload) != 4:
            raise ValueError("arm position must be 4 bytes")
        packed = int.from_bytes(payload, "little")
        return cls(
            telescope_position=packed & 0xFF,
            rotation_position=(packed >> 8) & 0xFF,
            side=(packed >> 16) & 0x0F,
            is_rotation_locked=bool(packed & (1 << 20)),
            is_rotation_and_telescope_locked=bool(packed & (1 << 21)),
            raw_flags=(packed >> 16) & 0xFFFF,
        )


@dataclass(frozen=True, slots=True)
class CartPosition:
    """Left/right vertical cart positions from message 0x1020."""

    left: int
    right: int

    @classmethod
    def decode(cls, payload: bytes) -> CartPosition:
        if len(payload) != 2:
            raise ValueError("cart position must be 2 bytes")
        return cls(left=payload[0], right=payload[1])


@dataclass(frozen=True, slots=True)
class ColumnRotation:
    """Left/right column rotation and unlock state from message 0x1021."""

    left_position: int
    left_unlocked: bool
    right_position: int
    right_unlocked: bool

    @classmethod
    def decode(cls, payload: bytes) -> ColumnRotation:
        if len(payload) != 4:
            raise ValueError("column rotation must be 4 bytes")
        return cls(
            left_position=payload[0],
            left_unlocked=bool(payload[1]),
            right_position=payload[2],
            right_unlocked=bool(payload[3]),
        )


@dataclass(frozen=True, slots=True)
class DeviceAnnouncement:
    raw: bytes
    firmware_version: str | None
    part_number: str | None
    serial_number: str | None
    build_date: str | None
    revision: str | None

    @classmethod
    def decode(cls, payload: bytes) -> DeviceAnnouncement:
        firmware_version = (
            ".".join(str(component) for component in reversed(payload[:4]))
            if len(payload) >= 4
            else None
        )
        strings = [item.decode("ascii", "replace") for item in payload.split(b"\0") if item]
        recognizable = [
            value for value in strings if all(32 <= ord(char) < 127 for char in value)
        ]
        part = next((value for value in recognizable if value.startswith("500-")), None)
        serial = next(
            (value for value in recognizable if len(value) == 8 and value.isdigit()),
            None,
        )
        date = next(
            (value for value in recognizable if len(value) == 8 and value.startswith("20")),
            None,
        )
        revision = next(
            (value for value in recognizable if len(value) <= 3 and value.isdigit()),
            None,
        )
        return cls(payload, firmware_version, part, serial, date, revision)
