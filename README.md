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

## Photos

<img width="2814" height="1710" alt="20260810_00h06m44s_grim" src="https://github.com/user-attachments/assets/3ee100c5-727d-4ed9-8481-3d245dd33f1f" />

<img width="1323" height="1757" alt="image" src="https://github.com/user-attachments/assets/6aa2d68b-1b7b-4ab2-a4f6-dee7df2b6217" />


## Connecting to Motor Controller

To open the Tonal unit there are 4 security hex screws on the right side of the unit hidden by the arm. Rotating it to the forward position with the arm extended is the easiest way to access them.

It is strongly recommended to power off your unit before doing anything to it.

The Android computer on the back of the screen  has a white 5-pin connector "tablet" on the bottom right. This connector exposes USB as well as a wake signal to the Android computer. You can disconnect that connector and use a USB connector instead. It's also recommended to unplug the "tablet power" connector.

<img width="605" height="1116" alt="image" src="https://github.com/user-attachments/assets/19e024d5-860e-46b0-b76f-626fd4cd5a41" />

<img width="1135" height="937" alt="tonal-android-connectors" src="https://github.com/user-attachments/assets/25390399-d84d-47d1-8ef1-7d4b95a7df0f" />



## Current status

Implemented:

- USB serial transport at raw 115200 8N1
- 16-byte wire header and CRC-16/BUYPASS
- Incremental stream decoding and resynchronization
- Device-announcement decoding
- Experimental bring-up prefix
- Basic resistance profile and enable/disable commands
- Signed load contributions, cable channels, rep count, and enable feedback
- Receive-only diagnostics
- Local web control panel with live telemetry and emergency disable
- Explicit motor-command opt-in and resistance bounds

Not yet established:

- Electrical-telemetry subscription/routing during abbreviated bring-up
- Complete symbolic names for bring-up messages
- Every advanced mode and firmware variant

The public telemetry model exposes cable distance and watts as `None` until
controlled experiments prove their encoding. See [the protocol document](docs/protocol.md).

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

> [!WARNING]
> Do not leave USB connected while switching on the machine. On the tested
> hardware, connecting USB before machine power can prevent normal controller
> startup. Power on the machine first, wait for it to initialize, and only then
> connect USB. If it starts with USB already attached, disconnect USB and fully
> power-cycle the machine before trying again.

## Install and test

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
```

Start the controller with the standard 200 lb ceiling, no workout-duration
limit, and a 10-minute inactivity sleep timer:

```bash
uv run swolectl --port /dev/ttyACM0
```

The controller runs until stopped. `--max-resistance` can impose a lower ceiling,
and `--sleep-timeout 0` disables inactivity sleep. To use another idle interval,
pass seconds such as `--sleep-timeout 900`.

Receive-only inspection remains available separately:

```bash
uv run swolectl-diagnose --port /dev/ttyACM0 --seconds 5
```

`swolectl` starts the normal controller and local web interface.

Then open <http://127.0.0.1:8080>. To expose it on a trusted LAN, add
`--host 0.0.0.0`. The web app has no authentication; never expose it to an
untrusted network or the public internet. The server attempts to disable
resistance during normal shutdown, but this does not replace a physical power
disconnect.

### USB sleep setup

Sleep is implemented by disabling resistance, closing `/dev/ttyACM*`, and
allowing Linux to runtime-suspend the USB device. Install the included udev rule
once so the kernel is allowed to suspend this controller:

```bash
sudo install -m 0644 contrib/99-swolectl.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

Apply the rule on the next safe USB reconnect. Remember the required startup
order above: power the machine first and connect USB only after initialization.
The Sleep button and inactivity timer release USB; Reconnect / wake reopens the
port and resumes it. Without the udev rule, resistance is still disabled and the
port is closed, but the host may keep the USB link active instead of suspending
it.

The mode picker includes Basic, Spotter, Drop Sets, Chains, Eccentric, and Smart
Flex. Drop Sets use the controller's stock threshold and 8% reduction defaults;
the controller performs each reduction without additional host commands.
Only base resistance units are verified. Advanced mode names and levels are
high-confidence inferences from isolated UI toggles and are labeled accordingly.
Profiles apply automatically after weight, mode, or level changes. The live
exercise graph records resistance immediately and is ready for draw and watt
series once those telemetry fields are established.

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
