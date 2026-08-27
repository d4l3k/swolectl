# swolectl

[![CI](https://github.com/d4l3k/swolectl/actions/workflows/ci.yml/badge.svg)](https://github.com/d4l3k/swolectl/actions/workflows/ci.yml)

An unofficial Python implementation for communicating directly with compatible
Tonal motor-controller hardware over USB CDC ACM.

> [!IMPORTANT]
> This work is based only on a **Tonal 1 with the `501-0100_rev004` motor
> controller board running motor-controller firmware `5.2.18.0`**. Other
> firmware versions and Tonal 1 board revisions are unverified. Tonal 2 support
> is unknown. Do not connect this software to unverified hardware or assume its
> transport, framing, commands, limits, or safety behavior match.

> [!CAUTION]
> This is experimental, unofficial software. It can command machinery capable
> of producing substantial force. Incorrect wiring, malformed commands,
> software defects, or incomplete protocol knowledge may cause unexpected
> motion, trapped fingers, cable release, equipment damage, permanent hardware
> failure, loss of calibration, or an unrecoverable/bricked controller. Using
> it may void your hardware warranty, violate service terms, or make vendor
> support unavailable. You assume all risk. Keep people and objects clear of
> the mechanism, provide a physical emergency disconnect, and never test alone.

This project is not affiliated with, endorsed by, or supported by Tonal. The
Tonal name is used only to identify hardware compatibility.

## Full setup

<img width="2814" height="1710" alt="Full swolectl hardware setup" src="https://github.com/user-attachments/assets/3ee100c5-727d-4ed9-8481-3d245dd33f1f" />

## Features

- USB serial transport at raw 115200 8N1
- 16-byte wire header and CRC-16/BUYPASS
- Incremental stream decoding and resynchronization
- Device-announcement decoding
- Experimental bring-up prefix
- Basic resistance profile and enable/disable commands
- Signed load contributions, cable channels, rep count, and enable feedback
- Receive-only diagnostics
- Explicit motor-command opt-in and resistance bounds

## Hardware

### Compatibility

| Component | Tested version | Status |
| --- | --- | --- |
| Product generation | Tonal 1 | Tested |
| Motor-controller board | `501-0100_rev004` | Tested |
| Motor-controller application firmware | `5.2.18.0` | Tested |
| Other Tonal 1 firmware or board revisions | — | Unverified |
| Tonal 2 | — | Unknown and unsupported |
| Android application/system image | — | Not required by this library; compatibility not evaluated |

The firmware version refers to the application running on the motor controller,
not the Android tablet software or the controller's recovery bootloader. A
matching board revision alone does not establish compatibility if its firmware
differs.

The controller reports this version in its unsolicited startup announcement.
The library decodes it and, by default, refuses force-producing commands unless
the reported version is in `SafetyPolicy.compatible_firmware_versions`. Missing
or different versions fail closed during a cold bring-up. If valid high-rate
motor telemetry is already streaming when the library connects, the controller
is treated as an already-negotiated active session even though its one-time
version announcement was missed. Expert users can explicitly set
`allow_unverified_firmware=True`, accepting that protocol and safety behavior may
differ. Emergency disable remains available without a version match.

Observed device interface:

```text
Board:       501-0100_rev004
USB VID:PID: 1cbe:00a1
USB class:   CDC ACM
Linux node:  /dev/ttyACM0
Speed:       USB full speed (12 Mbit/s)
```

The controller reports itself as self-powered. USB VBUS still acts as host
presence and can trigger controller initialization or arm movement. Do not
connect or disconnect it with anyone near the mechanism.

## Install and test

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
```

Inspect received frames for five seconds:

```bash
uv run swolectl --port /dev/ttyACM0 --seconds 5
```

The CLI is receive-only. Force-producing operations are available through the
Python API and require an explicit `SafetyPolicy`.

## Python usage

Receive only:

```python
from swolectl import Controller

with Controller("/dev/ttyACM0") as controller:
    frame = controller.wait_for(lambda item: item.message_type == 0x1000)
    print(frame)
```

Motor commands require deliberate opt-in and explicit limits:

```python
from swolectl import Controller, SafetyPolicy

safety = SafetyPolicy(
    allow_motor_commands=True,
    minimum_resistance_lb=5,
    maximum_resistance_lb=20,
)

with Controller("/dev/ttyACM0", safety=safety) as controller:
    controller.bring_up()
    controller.set_resistance(5)
    controller.enable_resistance()
    telemetry = controller.wait_for_target()
    print(telemetry.applied_resistance_lb)
    controller.disable_resistance()
```

Do not run the motor example until bring-up has been validated for your exact
firmware and a physical emergency disconnect is immediately accessible.

## License

GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
