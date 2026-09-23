import pytest

from swolectl.controller import Controller, SafetyError, SafetyPolicy
from swolectl.frame import Frame
from swolectl.messages import (
    DeviceAnnouncement,
    MessageType,
    MotorTelemetry,
    ResistanceMode,
    ResistanceProfile,
    ResistanceState,
)
from swolectl.transport import MemoryTransport


def make_controller(policy: SafetyPolicy) -> tuple[Controller, MemoryTransport]:
    transport = MemoryTransport()
    controller = Controller(safety=policy, transport_factory=lambda _port: transport)
    controller.open()
    return controller, transport


def mark_supported(controller: Controller) -> None:
    controller.last_announcement = DeviceAnnouncement(
        raw=b"\0\x12\x02\x05",
        firmware_version="5.2.18.0",
        part_number="500-0102",
        serial_number=None,
        build_date=None,
        revision="001",
    )


def test_motor_commands_are_locked_by_default() -> None:
    controller, _transport = make_controller(SafetyPolicy())
    try:
        with pytest.raises(SafetyError, match="disabled"):
            controller.set_resistance(5)
    finally:
        controller.close()


def test_resistance_bounds() -> None:
    controller, _transport = make_controller(
        SafetyPolicy(allow_motor_commands=True, minimum_resistance_lb=5, maximum_resistance_lb=20)
    )
    try:
        with pytest.raises(SafetyError, match="outside"):
            controller.set_resistance(25)
    finally:
        controller.close()


def test_resistance_bounds_include_mode_modifier() -> None:
    controller, transport = make_controller(
        SafetyPolicy(allow_motor_commands=True, maximum_resistance_lb=200)
    )
    try:
        mark_supported(controller)
        profile = ResistanceProfile.for_mode(
            150, ResistanceMode.CHAINS, intensity_percent=50
        )
        with pytest.raises(SafetyError, match=r"225\.0 lb is outside"):
            controller.configure_resistance(profile)
        assert transport.writes == []
    finally:
        controller.close()


def test_configure_enable_disable_frames() -> None:
    controller, transport = make_controller(
        SafetyPolicy(allow_motor_commands=True, maximum_resistance_lb=20, disable_on_close=False)
    )
    try:
        mark_supported(controller)
        controller.set_resistance(10)
        controller.enable_resistance()
        controller.disable_resistance()
        decoded = [Frame.decode(data) for data in transport.writes]
        assert [frame.message_type for frame in decoded] == [
            MessageType.RESISTANCE_PROFILE,
            MessageType.RESISTANCE_TOGGLE,
            MessageType.RESISTANCE_TOGGLE,
        ]
        assert decoded[1].payload == b"\0\0\2\0"
        assert decoded[2].payload == b"\1\0\2\0"
        assert controller.resistance_state is ResistanceState.DISABLED
    finally:
        controller.close()


def test_motor_commands_require_known_supported_firmware() -> None:
    controller, transport = make_controller(
        SafetyPolicy(allow_motor_commands=True, maximum_resistance_lb=20, disable_on_close=False)
    )
    try:
        with pytest.raises(SafetyError, match="firmware is unknown"):
            controller.set_resistance(10)
        assert transport.writes == []

        controller.last_announcement = DeviceAnnouncement(
            raw=b"\0\0\0\0",
            firmware_version="0.0.0.0",
            part_number="500-0102",
            serial_number=None,
            build_date=None,
            revision="001",
        )
        with pytest.raises(SafetyError, match=r"0\.0\.0\.0 is unverified"):
            controller.set_resistance(10)
        assert transport.writes == []
    finally:
        controller.close()


def test_wait_for_translates_empty_queue_to_timeout() -> None:
    controller = Controller(transport_factory=lambda _port: MemoryTransport())
    with pytest.raises(TimeoutError, match="protocol frame"):
        controller.wait_for(lambda _frame: True, timeout=0.001)


def test_reopen_clears_stale_device_session() -> None:
    controller, _transport = make_controller(SafetyPolicy())
    mark_supported(controller)
    controller.close()
    controller.open()
    try:
        assert controller.last_announcement is None
        assert controller.configured_profile is None
        assert controller.resistance_state is ResistanceState.DISABLED
    finally:
        controller.close()


def make_already_active_controller(policy: SafetyPolicy) -> tuple[Controller, MemoryTransport]:
    transport = MemoryTransport()
    telemetry_payload = MotorTelemetry._STRUCT.pack(*([0] * 25))
    transport.queue(
        Frame(message_type=MessageType.MOTOR_TELEMETRY, payload=telemetry_payload).encode()
    )
    controller = Controller(safety=policy, transport_factory=lambda _port: transport)
    controller.bring_up(timeout=1.0)
    return controller, transport


def test_already_active_session_fails_closed_by_default() -> None:
    controller, transport = make_already_active_controller(
        SafetyPolicy(allow_motor_commands=True, maximum_resistance_lb=20, disable_on_close=False)
    )
    try:
        with pytest.raises(SafetyError, match="already streaming"):
            controller.set_resistance(10)
        assert transport.writes == []
    finally:
        controller.close()


def test_already_active_session_requires_unverified_firmware_opt_in() -> None:
    controller, _transport = make_already_active_controller(
        SafetyPolicy(
            allow_motor_commands=True,
            maximum_resistance_lb=20,
            disable_on_close=False,
            allow_unverified_firmware=True,
        )
    )
    try:
        controller.set_resistance(10)
        assert controller.require_compatible_firmware() == "already-active"
    finally:
        controller.close()


def test_send_raw_refuses_motor_messages_by_default() -> None:
    controller, transport = make_controller(SafetyPolicy())
    try:
        mark_supported(controller)
        for message_type, payload in [
            (MessageType.RESISTANCE_PROFILE, ResistanceProfile.basic(10).encode()),
            (MessageType.RESISTANCE_TOGGLE, b"\0\0\2\0"),
            (MessageType.RESISTANCE_TOGGLE, b"\1\0\2\0"),
            (0x0003, b""),
        ]:
            with pytest.raises(SafetyError, match="disabled"):
                controller.send_raw(message_type, payload)
        assert transport.writes == []
    finally:
        controller.close()


def test_send_raw_allows_bring_up_messages_by_default() -> None:
    controller, transport = make_controller(SafetyPolicy())
    try:
        controller.send_raw(MessageType.BOOT_0)
        controller.send_raw(MessageType.BRINGUP_1026, b"\x09\x00", destination=9)
        assert len(transport.writes) == 2
    finally:
        controller.close()


def test_send_raw_profile_uses_resistance_bounds() -> None:
    controller, transport = make_controller(
        SafetyPolicy(allow_motor_commands=True, maximum_resistance_lb=20, disable_on_close=False)
    )
    try:
        mark_supported(controller)
        with pytest.raises(SafetyError, match="outside"):
            controller.send_raw(
                MessageType.RESISTANCE_PROFILE, ResistanceProfile.basic(25).encode()
            )
        chains = ResistanceProfile.for_mode(15, ResistanceMode.CHAINS, intensity_percent=50)
        with pytest.raises(SafetyError, match=r"22\.5 lb is outside"):
            controller.send_raw(MessageType.RESISTANCE_PROFILE, chains.encode())
        with pytest.raises(SafetyError, match="malformed"):
            controller.send_raw(MessageType.RESISTANCE_PROFILE, b"\xff" * 4)
        assert transport.writes == []

        profile = ResistanceProfile.basic(10)
        controller.send_raw(MessageType.RESISTANCE_PROFILE, profile.encode())
        assert controller.configured_profile == profile
        assert len(transport.writes) == 1
    finally:
        controller.close()


def test_send_raw_profile_requires_supported_firmware() -> None:
    controller, transport = make_controller(
        SafetyPolicy(allow_motor_commands=True, maximum_resistance_lb=20, disable_on_close=False)
    )
    try:
        with pytest.raises(SafetyError, match="firmware is unknown"):
            controller.send_raw(
                MessageType.RESISTANCE_PROFILE, ResistanceProfile.basic(10).encode()
            )
        assert transport.writes == []
    finally:
        controller.close()


def test_send_raw_toggle_checks_match_typed_methods() -> None:
    controller, transport = make_controller(
        SafetyPolicy(allow_motor_commands=True, maximum_resistance_lb=20, disable_on_close=False)
    )
    try:
        # Disable stays available without a firmware match, like disable_resistance().
        controller.send_raw(MessageType.RESISTANCE_TOGGLE, b"\1\0\2\0")
        mark_supported(controller)
        with pytest.raises(SafetyError, match="configure resistance"):
            controller.send_raw(MessageType.RESISTANCE_TOGGLE, b"\0\0\2\0")
        with pytest.raises(SafetyError, match="4 bytes"):
            controller.send_raw(MessageType.RESISTANCE_TOGGLE, b"\0\0")
        assert len(transport.writes) == 1

        controller.set_resistance(10)
        controller.send_raw(MessageType.RESISTANCE_TOGGLE, b"\0\0\2\0")
        assert controller.resistance_state is ResistanceState.ENABLED
    finally:
        controller.close()


def test_enter_active_has_no_typed_api() -> None:
    assert not hasattr(Controller, "enter_active")
    assert 0x0003 not in {member.value for member in MessageType}
