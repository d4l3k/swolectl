"""High-level controller, safety policy, and experimental bring-up."""

from __future__ import annotations

import contextlib
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .frame import Frame, FrameStream
from .messages import (
    ArmPosition,
    CartPosition,
    ColumnRotation,
    DeviceAnnouncement,
    ElectricalTelemetry,
    MessageType,
    MotorTelemetry,
    ResistanceProfile,
    ResistanceState,
    encode_resistance_toggle,
)
from .transport import SerialTransport, Transport


class ControllerError(RuntimeError):
    pass


class SafetyError(ControllerError):
    pass


class BringUpError(ControllerError):
    pass


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """Required opt-in and bounds for commands capable of producing force."""

    allow_motor_commands: bool = False
    minimum_resistance_lb: float = 5.0
    maximum_resistance_lb: float = 200.0
    disable_on_close: bool = True
    compatible_firmware_versions: frozenset[str] = frozenset({"5.2.18.0"})
    allow_unverified_firmware: bool = False

    def require_motor_commands(self) -> None:
        if not self.allow_motor_commands:
            raise SafetyError("motor commands are disabled by SafetyPolicy")

    def validate_resistance(self, pounds: float) -> None:
        self.require_motor_commands()
        if not math.isfinite(pounds):
            raise SafetyError("resistance must be finite")
        if not self.minimum_resistance_lb <= pounds <= self.maximum_resistance_lb:
            raise SafetyError(
                f"resistance {pounds} lb is outside "
                f"[{self.minimum_resistance_lb}, {self.maximum_resistance_lb}]"
            )


# Message types that can produce force or change motor state. ``send_raw``
# applies the same SafetyPolicy checks to these as the typed methods do.
# 0x0003 has no typed API; see docs/protocol.md before sending it.
_MOTOR_MESSAGE_TYPES = frozenset(
    {MessageType.RESISTANCE_PROFILE, MessageType.RESISTANCE_TOGGLE, 0x0003}
)


@dataclass(frozen=True, slots=True)
class BringUpProfile:
    """Negotiation constants; symbolic meanings remain under study."""

    message_1005: bytes = bytes.fromhex("a5 3a 00 00 66 00 01 00 02 00 01 00")
    component_1: int = 1
    router_component: int = 9


class Controller:
    """Threaded protocol endpoint.

    Constructing the object opens no device. ``open`` starts receive processing,
    while motor writes require an explicit permissive ``SafetyPolicy``.
    """

    def __init__(
        self,
        port: str = "/dev/ttyACM0",
        *,
        safety: SafetyPolicy | None = None,
        source: int = 7,
        transport_factory: Callable[[str], Transport] = SerialTransport,
    ) -> None:
        self.port = port
        self.safety = safety or SafetyPolicy()
        self.source = source
        self._transport_factory = transport_factory
        self._transport: Transport | None = None
        self._stream = FrameStream()
        self._sequence = 0
        self._started_at = time.monotonic()
        self._frames: queue.Queue[Frame] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._callbacks: list[Callable[[Frame], None]] = []
        self.last_frame: Frame | None = None
        self.last_announcement: DeviceAnnouncement | None = None
        self.telemetry: MotorTelemetry | None = None
        self.electrical_telemetry: ElectricalTelemetry | None = None
        self.arm_positions: dict[str, ArmPosition] = {}
        self.cart_position: CartPosition | None = None
        self.column_rotation: ColumnRotation | None = None
        self._accepted_active_session = False
        self.resistance_state = ResistanceState.DISABLED
        self.configured_profile: ResistanceProfile | None = None

    @property
    def is_open(self) -> bool:
        return self._transport is not None

    def open(self) -> None:
        if self._transport is not None:
            return
        self._stream = FrameStream()
        while not self._frames.empty():
            with contextlib.suppress(queue.Empty):
                self._frames.get_nowait()
        self.last_frame = None
        self.last_announcement = None
        self.telemetry = None
        self.electrical_telemetry = None
        self.arm_positions = {}
        self.cart_position = None
        self.column_rotation = None
        self._accepted_active_session = False
        self.configured_profile = None
        self.resistance_state = ResistanceState.DISABLED
        self._transport = self._transport_factory(self.port)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._reader,
            name="swolectl-rx",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        transport = self._transport
        if transport is None:
            return
        if self.safety.disable_on_close and self.safety.allow_motor_commands:
            with contextlib.suppress(Exception):
                self.disable_resistance()
        self._stop.set()
        transport.close()
        self._transport = None
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None

    def __enter__(self) -> Controller:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def add_frame_callback(self, callback: Callable[[Frame], None]) -> None:
        self._callbacks.append(callback)

    def _reader(self) -> None:
        while not self._stop.is_set():
            transport = self._transport
            if transport is None:
                return
            try:
                data = transport.read(512)
            except Exception:
                if not self._stop.is_set():
                    self._stop.set()
                return
            if not data:
                continue
            for frame in self._stream.feed(data):
                self._handle(frame)

    def _handle(self, frame: Frame) -> None:
        self.last_frame = frame
        if frame.message_type == MessageType.DEVICE_ANNOUNCE:
            self.last_announcement = DeviceAnnouncement.decode(frame.payload)
        elif frame.message_type == MessageType.MOTOR_TELEMETRY:
            self.telemetry = MotorTelemetry.decode(frame.payload)
        elif frame.message_type == MessageType.ELECTRICAL:
            self.electrical_telemetry = ElectricalTelemetry.decode(frame.payload)
        elif frame.message_type == MessageType.ARM_POSITION:
            arm = ArmPosition.decode(frame.payload)
            self.arm_positions[arm.side_name] = arm
        elif frame.message_type == MessageType.CART_POSITION:
            self.cart_position = CartPosition.decode(frame.payload)
        elif frame.message_type == MessageType.COLUMN_ROTATION:
            self.column_rotation = ColumnRotation.decode(frame.payload)
        self._frames.put(frame)
        for callback in tuple(self._callbacks):
            callback(frame)

    def _timestamp(self) -> int:
        return int((time.monotonic() - self._started_at) * 1000) & 0xFFFFFFFF

    def send_raw(
        self,
        message_type: int,
        payload: bytes = b"",
        *,
        destination: int = 1,
    ) -> Frame:
        """Send an arbitrary protocol frame.

        This is an expert API. It does not imply the payload is safe or known.
        Force-producing message types are subject to the same ``SafetyPolicy``
        checks as the typed methods: resistance profiles are bounds-checked,
        and every motor message requires ``allow_motor_commands``.
        """
        if self._transport is None:
            raise ControllerError("controller is not open")
        profile = self._check_raw_command(message_type, payload)
        frame = self._send(message_type, payload, destination=destination)
        if profile is not None:
            self.configured_profile = profile
        elif message_type == MessageType.RESISTANCE_TOGGLE:
            state = int.from_bytes(payload[:2], "little")
            if state in (ResistanceState.ENABLED, ResistanceState.DISABLED):
                self.resistance_state = ResistanceState(state)
        return frame

    def _check_raw_command(self, message_type: int, payload: bytes) -> ResistanceProfile | None:
        if message_type not in _MOTOR_MESSAGE_TYPES:
            return None
        self.safety.require_motor_commands()
        if message_type == MessageType.RESISTANCE_PROFILE:
            try:
                profile = ResistanceProfile.decode(payload)
            except ValueError as exc:
                raise SafetyError(f"malformed resistance profile: {exc}") from exc
            self._check_profile(profile)
            return profile
        if message_type == MessageType.RESISTANCE_TOGGLE:
            if len(payload) != 4:
                raise SafetyError("resistance toggle must be 4 bytes")
            if int.from_bytes(payload[:2], "little") != ResistanceState.DISABLED:
                self._check_enable()
            return None
        self.require_compatible_firmware()
        return None

    def _send(self, message_type: int, payload: bytes = b"", *, destination: int = 1) -> Frame:
        transport = self._transport
        if transport is None:
            raise ControllerError("controller is not open")
        with self._write_lock:
            frame = Frame(
                message_type=message_type,
                payload=payload,
                sequence=self._sequence,
                source=self.source,
                destination=destination,
                timestamp_ms=self._timestamp(),
            )
            encoded = frame.encode()
            written = transport.write(encoded)
            if written != len(encoded):
                raise ControllerError(f"short write: {written}/{len(encoded)}")
            self._sequence = (self._sequence + 1) & 0xFF
            return frame

    def wait_for(
        self,
        predicate: Callable[[Frame], bool],
        *,
        timeout: float = 3.0,
    ) -> Frame:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for protocol frame")
            try:
                frame = self._frames.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("timed out waiting for protocol frame") from exc
            if predicate(frame):
                return frame

    def bring_up(self, *, timeout: float = 3.0, profile: BringUpProfile | None = None) -> None:
        """Run the experimental initial negotiation.

        Device-information routing after this prefix is firmware-dependent. The
        method succeeds only after high-rate motor telemetry is received.
        """
        if self._transport is None:
            self.open()
        selected = profile or BringUpProfile()
        try:
            initial = self.wait_for(
                lambda frame: frame.message_type
                in (MessageType.DEVICE_ANNOUNCE, MessageType.MOTOR_TELEMETRY),
                timeout=timeout,
            )
            if initial.message_type == MessageType.MOTOR_TELEMETRY:
                self._accepted_active_session = True
                return
            self.send_raw(MessageType.BOOT_0, destination=selected.component_1)
            self.send_raw(
                MessageType.BRINGUP_1005,
                selected.message_1005,
                destination=selected.router_component,
            )
            self.send_raw(MessageType.BOOT_1, destination=selected.component_1)
            self.wait_for(
                lambda frame: frame.message_type == MessageType.STATUS
                and frame.payload == b"\x00\x00\x01\x00",
                timeout=timeout,
            )
            self.send_raw(MessageType.BOOT_1, destination=selected.component_1)
            self.wait_for(
                lambda frame: frame.message_type == MessageType.STATUS,
                timeout=timeout,
            )
            self.send_raw(MessageType.BOOT_2, destination=selected.component_1)
            self.send_raw(MessageType.BOOT_2, destination=selected.component_1)
            self.send_raw(
                MessageType.BRINGUP_1026,
                struct_pack_u16(selected.router_component),
                destination=selected.router_component,
            )
            self.wait_for(
                lambda frame: frame.message_type == MessageType.MOTOR_TELEMETRY,
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise BringUpError("bring-up did not reach motor telemetry") from exc

    def configure_resistance(self, profile: ResistanceProfile) -> Frame:
        self._check_profile(profile)
        frame = self._send(MessageType.RESISTANCE_PROFILE, profile.encode(), destination=1)
        self.configured_profile = profile
        return frame

    def _check_profile(self, profile: ResistanceProfile) -> None:
        self.safety.validate_resistance(profile.base_tenths_lb / 10.0)
        self.safety.validate_resistance(profile.peak_tenths_lb / 10.0)
        self.require_compatible_firmware()

    def _check_enable(self) -> None:
        self.safety.require_motor_commands()
        if self.configured_profile is None:
            raise SafetyError("configure resistance before enabling it")
        self.require_compatible_firmware()

    def require_compatible_firmware(self) -> str:
        """Fail closed unless the announcement reports a tested firmware.

        An already-active session (telemetry streaming at connect time) never
        shows the version announcement, so it is treated as unverified firmware.
        """
        announcement = self.last_announcement
        version = announcement.firmware_version if announcement is not None else None
        if self.safety.allow_unverified_firmware:
            if version is None and self._accepted_active_session:
                return "already-active"
            return version or "unknown"
        if version is None and self._accepted_active_session:
            raise SafetyError(
                "motor-controller firmware is unknown: telemetry was already streaming, "
                "so the version announcement was missed; perform a cold bring-up or "
                "explicitly allow unverified firmware"
            )
        if version is None:
            raise SafetyError(
                "motor-controller firmware is unknown; wait for a device announcement "
                "or explicitly allow unverified firmware"
            )
        if version not in self.safety.compatible_firmware_versions:
            expected = ", ".join(sorted(self.safety.compatible_firmware_versions))
            raise SafetyError(
                f"motor-controller firmware {version} is unverified; expected {expected}"
            )
        return version

    def set_resistance(self, pounds: float) -> Frame:
        return self.configure_resistance(ResistanceProfile.basic(pounds))

    def enable_resistance(self, *, mode: int = 2) -> Frame:
        self._check_enable()
        frame = self._send(
            MessageType.RESISTANCE_TOGGLE,
            encode_resistance_toggle(ResistanceState.ENABLED, mode),
            destination=1,
        )
        self.resistance_state = ResistanceState.ENABLED
        return frame

    def disable_resistance(self, *, mode: int = 2) -> Frame:
        self.safety.require_motor_commands()
        frame = self._send(
            MessageType.RESISTANCE_TOGGLE,
            encode_resistance_toggle(ResistanceState.DISABLED, mode),
            destination=1,
        )
        self.resistance_state = ResistanceState.DISABLED
        return frame

    def wait_for_target(
        self,
        *,
        tolerance_lb: float = 1.0,
        timeout: float = 3.0,
    ) -> MotorTelemetry:
        if self.configured_profile is None:
            raise ControllerError("no configured resistance target")
        target = self.configured_profile.base_tenths_lb / 10.0
        frame = self.wait_for(
            lambda candidate: candidate.message_type == MessageType.MOTOR_TELEMETRY
            and abs(MotorTelemetry.decode(candidate.payload).applied_resistance_lb - target)
            <= tolerance_lb,
            timeout=timeout,
        )
        return MotorTelemetry.decode(frame.payload)


def struct_pack_u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError("value must fit uint16")
    return value.to_bytes(2, "little")
