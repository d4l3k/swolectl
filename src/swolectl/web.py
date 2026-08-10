"""Local web control panel for the motor protocol."""

from __future__ import annotations

import argparse
import contextlib
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .controller import Controller, SafetyPolicy
from .frame import Frame
from .messages import MessageType, ResistanceMode, ResistanceProfile, ResistanceState

STATIC_DIR = Path(__file__).with_name("static")


class ProfileRequest(BaseModel):
    pounds: float = Field(ge=5.0, le=200.0)
    mode: ResistanceMode = ResistanceMode.BASIC
    intensity_percent: float = Field(default=25.0, ge=0.0, le=100.0)


class MotorService:
    def __init__(self, controller: Controller, *, sleep_timeout_seconds: float = 600.0) -> None:
        self.controller = controller
        self.sleep_timeout_seconds = sleep_timeout_seconds
        self.lock = threading.RLock()
        self.error: str | None = None
        self.capture_active = False
        self.capture_started_at = 0.0
        self.capture_samples: list[dict[str, Any]] = []
        self.session_volume_lb = 0.0
        self.last_rep_count: int | None = None
        self.last_activity_at = time.monotonic()
        self.sleeping = False
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        controller.add_frame_callback(self._capture_frame)

    def _capture_frame(self, frame: Frame) -> None:
        if frame.message_type == MessageType.MOTOR_TELEMETRY and self.controller.telemetry:
            telemetry = self.controller.telemetry
            if telemetry.device_enabled or abs(telemetry.left_cable_speed) > 0.1 or abs(
                telemetry.right_cable_speed
            ) > 0.1:
                self.note_activity()
            if self.last_rep_count is None or telemetry.rep_count < self.last_rep_count:
                self.last_rep_count = telemetry.rep_count
            elif telemetry.rep_count > self.last_rep_count:
                completed = telemetry.rep_count - self.last_rep_count
                self.session_volume_lb += completed * telemetry.total_weight_lb
                self.last_rep_count = telemetry.rep_count
        if not self.capture_active or frame.message_type not in (
            MessageType.MOTOR_TELEMETRY,
            MessageType.ELECTRICAL,
            MessageType.SLOW_STATUS,
            MessageType.TEXT_LOG,
        ):
            return
        elapsed = time.monotonic() - self.capture_started_at
        sample: dict[str, Any] = {
            "seconds": round(elapsed, 6),
            "type": frame.message_type,
            "payload_hex": frame.payload.hex(),
        }
        if frame.message_type == MessageType.MOTOR_TELEMETRY:
            current_telemetry = self.controller.telemetry
            sample["words"] = list(current_telemetry.raw_words) if current_telemetry else []
        self.capture_samples.append(sample)

    def connect(self) -> None:
        with self.lock:
            try:
                self.controller.open()
                self.controller.bring_up(timeout=5.0)
                self.sleeping = False
                self.note_activity()
                self.error = None
            except Exception as exc:
                self.error = str(exc)
                raise

    def status(self) -> dict[str, Any]:
        telemetry = self.controller.telemetry
        electrical = self.controller.electrical_telemetry
        profile = self.controller.configured_profile
        left_arm = self.controller.arm_positions.get("left")
        right_arm = self.controller.arm_positions.get("right")
        cart = self.controller.cart_position
        column = self.controller.column_rotation
        return {
            "connected": self.controller.is_open,
            "sleeping": self.sleeping,
            "sleep_timeout_seconds": self.sleep_timeout_seconds,
            "idle_seconds": time.monotonic() - self.last_activity_at,
            "firmware_version": None
            if self.controller.last_announcement is None
            else self.controller.last_announcement.firmware_version,
            "firmware_session": "already_active"
            if self.controller.last_announcement is None
            and self.controller.telemetry is not None
            else "observed",
            "arms": {
                "left": self._arm_status(
                    left_arm,
                    height=None if cart is None else cart.left,
                    rotation=None if column is None else column.left_position,
                    rotation_unlocked=False if column is None else column.left_unlocked,
                ),
                "right": self._arm_status(
                    right_arm,
                    height=None if cart is None else cart.right,
                    rotation=None if column is None else column.right_position,
                    rotation_unlocked=False if column is None else column.right_unlocked,
                ),
            },
            "error": self.error,
            "commanded_enabled": self.controller.resistance_state
            is ResistanceState.ENABLED,
            "configured_lb": None if profile is None else profile.base_tenths_lb / 10.0,
            "session_volume_lb": self.session_volume_lb,
            "telemetry": None
            if telemetry is None
            else {
                "applied_lb": telemetry.applied_resistance_lb,
                "base_lb": telemetry.base_weight_lb,
                "eccentric_lb": telemetry.eccentric_weight_lb,
                "rom_lb": telemetry.rom_weight_lb,
                "spotted_lb": telemetry.spotted_weight_lb,
                "ramp_lb": telemetry.ramp_weight_lb,
                "rack_lb": telemetry.rack_weight_lb,
                "rep_count": telemetry.rep_count,
                "active": telemetry.active,
                "device_enabled": telemetry.device_enabled,
                "is_racked": telemetry.is_racked,
                "is_grounded": telemetry.is_grounded,
                "left_position": telemetry.left_cable_position,
                "left_speed": telemetry.left_cable_speed,
                "right_position": telemetry.right_cable_position,
                "right_speed": telemetry.right_cable_speed,
                "cable_mismatch": telemetry.is_racked
                and abs(telemetry.left_cable_position - telemetry.right_cable_position) > 2.0,
                "power_watts": None
                if electrical is None
                else abs(electrical.motor_mechanical_power_w),
                "power_source": None if electrical is None else "electrical_telemetry",
                "electrical_power_watts": None
                if electrical is None
                else electrical.motor_electrical_power_w,
            },
        }

    @staticmethod
    def _arm_status(
        arm: Any,
        *,
        height: int | None,
        rotation: int | None,
        rotation_unlocked: bool,
    ) -> dict[str, Any] | None:
        if arm is None and height is None and rotation is None:
            return None
        return {
            "height": height,
            "arm_position": None if arm is None else arm.telescope_setting,
            "angle": None if arm is None else arm.angle,
            "rotation": rotation,
            "rotation_unlocked": rotation_unlocked,
            "angle_locked": False if arm is None else arm.is_rotation_locked,
            "fully_locked": False
            if arm is None
            else arm.is_rotation_and_telescope_locked,
        }

    def configure(self, request: ProfileRequest) -> None:
        with self.lock:
            self.note_activity()
            profile = ResistanceProfile.for_mode(
                request.pounds,
                request.mode,
                intensity_percent=request.intensity_percent,
            )
            self.controller.configure_resistance(profile)

    def enable(self) -> None:
        with self.lock:
            self.note_activity()
            self.controller.enable_resistance()

    def disable(self) -> None:
        with self.lock:
            self.note_activity()
            self.controller.disable_resistance()

    def note_activity(self) -> None:
        self.last_activity_at = time.monotonic()

    def sleep(self) -> None:
        """Disable force and release USB so the host can runtime-suspend it."""
        with self.lock:
            self.controller.close()
            self.sleeping = True

    def start_monitor(self) -> None:
        if self.sleep_timeout_seconds <= 0 or self._monitor_thread is not None:
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor,
            name="swolectl-idle",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor(self) -> None:
        while not self._monitor_stop.wait(1.0):
            if (
                self.controller.is_open
                and time.monotonic() - self.last_activity_at >= self.sleep_timeout_seconds
            ):
                self.sleep()

    def close(self) -> None:
        self._monitor_stop.set()
        thread = self._monitor_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._monitor_thread = None
        with self.lock:
            self.controller.close()

    def start_capture(self) -> None:
        with self.lock:
            self.capture_samples = []
            self.capture_started_at = time.monotonic()
            self.capture_active = True

    def stop_capture(self) -> dict[str, Any]:
        with self.lock:
            self.capture_active = False
            return {
                "duration_seconds": time.monotonic() - self.capture_started_at,
                "samples": self.capture_samples,
            }


def create_app(service: MotorService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        with contextlib.suppress(Exception):
            service.connect()
        service.start_monitor()
        yield
        service.close()

    app = FastAPI(title="swolectl", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return service.status()

    @app.post("/api/connect")
    def connect() -> dict[str, Any]:
        try:
            service.connect()
            return service.status()
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/profile")
    def configure(request: ProfileRequest) -> dict[str, Any]:
        try:
            service.configure(request)
            return service.status()
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/enable")
    def enable() -> dict[str, Any]:
        try:
            service.enable()
            return service.status()
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/disable")
    def disable() -> dict[str, Any]:
        try:
            service.disable()
            return service.status()
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.post("/api/sleep")
    def sleep() -> dict[str, Any]:
        service.sleep()
        return service.status()

    @app.post("/api/capture/start")
    def capture_start() -> dict[str, bool]:
        service.start_capture()
        return {"capturing": True}

    @app.post("/api/capture/stop")
    def capture_stop() -> dict[str, Any]:
        return service.stop_capture()

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="swolectl motor control panel")
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--max-resistance", type=float, default=200.0)
    parser.add_argument(
        "--sleep-timeout",
        type=float,
        default=600.0,
        help="idle seconds before USB sleep; use 0 to disable",
    )
    args = parser.parse_args()
    safety = SafetyPolicy(
        allow_motor_commands=True,
        minimum_resistance_lb=5.0,
        maximum_resistance_lb=args.max_resistance,
    )
    service = MotorService(
        Controller(args.port, safety=safety),
        sleep_timeout_seconds=args.sleep_timeout,
    )
    uvicorn.run(create_app(service), host=args.host, port=args.web_port)


if __name__ == "__main__":
    main()
