"""Deliberately bounded physical motor smoke test.

Run only with the mechanism clear and a physical disconnect within reach.
"""

from __future__ import annotations

import argparse
import time

from swolectl import Controller, Frame, SafetyPolicy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--pounds", type=float, default=5.0)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()

    if args.pounds != 5.0:
        raise SystemExit("smoke test is intentionally fixed at 5.0 lb")
    if not 0.0 < args.seconds <= 2.0:
        raise SystemExit("duration must be greater than zero and at most 2 seconds")

    safety = SafetyPolicy(
        allow_motor_commands=True,
        minimum_resistance_lb=5.0,
        maximum_resistance_lb=5.0,
    )

    def report_frame(frame: Frame) -> None:
        message_type = frame.message_type
        if message_type == 0x1010:
            payload = frame.payload
            text = payload[2:].rstrip(b"\0").decode("ascii", "replace")
            print(f"controller log: {text}", flush=True)
        elif message_type not in (0x1000, 0x1024, 0x1001, 0x1019):
            print(
                f"rx type=0x{message_type:04x} "
                f"src={frame.source} dst={frame.destination} "
                f"len={len(frame.payload)}",
                flush=True,
            )

    with Controller(args.port, safety=safety) as controller:
        controller.add_frame_callback(report_frame)
        print("waiting for bring-up", flush=True)
        controller.bring_up(timeout=5.0)
        print("bring-up complete", flush=True)
        controller.set_resistance(args.pounds)
        print("5 lb profile sent", flush=True)
        try:
            controller.enable_resistance()
            print("resistance enabled", flush=True)
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                telemetry = controller.telemetry
                if telemetry is not None:
                    print(
                        "telemetry "
                        f"applied={telemetry.applied_resistance_lb:.1f}lb "
                        f"base={telemetry.base_weight_lb:.1f}lb "
                        f"rack={telemetry.rack_weight_lb:.1f}lb "
                        f"active={telemetry.active}",
                        flush=True,
                    )
                time.sleep(0.2)
        finally:
            controller.disable_resistance()
            print("resistance disabled", flush=True)


if __name__ == "__main__":
    main()
