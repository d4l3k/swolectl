"""Receive-only command-line diagnostics."""

from __future__ import annotations

import argparse
import time

from .controller import Controller


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent controller diagnostics")
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    counts: dict[int, int] = {}

    def count_frame(message_type: int) -> None:
        counts[message_type] = counts.get(message_type, 0) + 1

    with Controller(args.port) as controller:
        controller.add_frame_callback(lambda frame: count_frame(frame.message_type))
        time.sleep(args.seconds)
        announcement = controller.last_announcement

    if announcement:
        print(
            f"firmware={announcement.firmware_version} "
            f"part={announcement.part_number} serial={announcement.serial_number}"
        )
    for message_type, count in sorted(counts.items()):
        print(f"type=0x{message_type:04x} count={count}")
