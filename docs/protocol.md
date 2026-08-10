# Serial protocol specification

Last reviewed against the implementation: 2026-08-10.

This document describes the serial protocol. Confidence labels mean:

- **Verified:** repeated testing establishes the behavior.
- **Inferred:** evidence is strong, but physical meaning is not fully isolated.
- **Unknown:** preserved for compatibility; do not assign semantics yet.

This specification records protocol facts for interoperability.

## Hardware scope

All validated behavior in this document applies to a Tonal 1 with
motor-controller board `501-0100_rev004` running motor-controller application
firmware `5.2.18.0`. Other firmware versions and Tonal 1 board revisions are
unverified. Tonal 2 support is unknown and no compatibility claim is made.
Electrical interfaces, transport, framing, commands, safety limits, or behavior
may differ. Do not use this specification or library with another firmware,
revision, or Tonal 2 without a separate investigation and controlled validation.

The version above is the motor-controller application firmware version. It does
not identify an Android system/application release or establish compatibility
with the motor controller's recovery bootloader.

## Physical transport

| Property | Value | Confidence |
| --- | --- | --- |
| Motor-controller board | `501-0100_rev004` | Verified |
| Motor-controller firmware | `5.2.18.0` | Verified |
| USB VID:PID | `1cbe:00a1` | Verified |
| USB class | CDC ACM | Verified |
| Linux device | `/dev/ttyACM*` | Verified |
| Line coding | raw 115200, 8 data bits, no parity, 1 stop bit | Verified |
| USB speed | Full Speed, 12 Mbit/s | Verified |

The device is self-powered but uses USB VBUS as host presence. Applying VBUS
can cause initialization and mechanical movement before any protocol command.

### Power-on order

The tested controller does not reliably start normally when USB is connected
before machine power is applied. Use this sequence:

1. Leave USB disconnected.
2. Apply machine power and wait for controller initialization.
3. Connect USB to the host.

If the machine was powered on with USB attached, disconnect USB and fully
power-cycle the machine before reconnecting it. This behavior is verified only
on board `501-0100_rev004` with motor-controller firmware `5.2.18.0`.

### USB suspend and wake

Machine sleep is a USB power-management event, not a known serial frame. On the
tested Linux host, the `cdc_acm` interface advertises runtime-autosuspend
support. The controller sleeps after resistance is disabled, the ACM port is
closed, and the USB device enters runtime suspend. Opening the port resumes the
device. Linux may require the supplied udev rule to set the USB device's
`power/control` to `auto`; many systems default it to `on`.

Do not substitute `ENTER_ACTIVE` (`0x0003`) for wake. That message is not part of
the validated wake path and previously coincided with a controller fault.

## Frame layout

All multibyte integers are little-endian.

| Offset | Size | Field |
| ---: | ---: | --- |
| `0x00` | 4 | Magic `aa 55 a0 1a` (`0x1aa055aa`) |
| `0x04` | 2 | Message type |
| `0x06` | 2 | Payload length, maximum 496 bytes |
| `0x08` | 2 | Route |
| `0x0a` | 2 | CRC |
| `0x0c` | 4 | Sender timestamp in milliseconds |
| `0x10` | variable | Payload |

Route packing:

```text
bits  0..7   sequence number
bits  8..11  source component
bits 12..15  destination component
```

The tablet/server source component is 7. Controller traffic primarily
uses component 1, with components 2 and 9 involved in device routing.

## CRC

CRC-16/BUYPASS: polynomial `0x8005`, initial value 0, no reflection, xor-out 0.
Set header bytes `0x0a..0x0b` to zero and calculate across the complete header
and payload.

## Unpaired state

After direct USB enumeration, the controller sends `0x1000` length-66 device
announcements from component 1 to destination 8 at approximately 10 Hz. Known
payload strings include part number, serial number, build date, and revision.
It continues until a host begins negotiation.

The first four payload bytes encode the motor-controller application firmware
version in reverse display order. The prefix `00 12 02 05` therefore
represents `5.2.18.0`. This field is available before the host transmits any
bring-up command and is used for the library's fail-closed compatibility check.

## Experimental bring-up

Ordering is verified; symbolic meanings and portability across firmware are
incomplete.

```text
server -> 1  type 0x0000  empty
1 -> server  type 0x1000  device announcement
server -> 9  type 0x1005  a5 3a 00 00 66 00 01 00 02 00 01 00
server -> 1  type 0x0001  empty
1 -> server  type 0x1001  00 00 01 00
server -> 1  type 0x0001  empty
1 -> server  type 0x1001  00 00 01 00
server -> 1  type 0x0002  empty, twice
1 -> server  type 0x1001  00 00 02 00
server -> 9  type 0x1026  09 00
```

Device-information messages (`0x1022`) and `0x102a`/`0x102c` exchanges follow.
Telemetry is interleaved with negotiation. `Controller.bring_up()` implements
the prefix and declares success only after `0x1024` telemetry arrives.
If telemetry is already streaming, it recognizes the existing paired state and
does not restart negotiation. Because the pre-active firmware announcement has
already passed in that state, the compatibility guard records the session as
`already-active`; it does not claim to have read a firmware version from normal
telemetry. Bring-up is experimental and may stop with `BringUpError` on other
firmware.

## Message inventory

| Type | Direction | Length | Meaning/confidence |
| ---: | --- | ---: | --- |
| `0x0000` | host -> device | 0 | Bring-up step, symbolic meaning unknown |
| `0x0001` | host -> device | 0 | Bring-up step, symbolic meaning unknown |
| `0x0002` | host -> device | 0 | Bring-up step, symbolic meaning unknown |
| `0x0013` | host -> device | 24 | Resistance profile, verified |
| `0x1000` | device -> host | 66 | Unpaired device announcement, verified |
| `0x1001` | device -> host | 4 | Connection/status state, inferred |
| `0x1005` | host -> router | 12 | Bring-up step, unknown fields |
| `0x1010` | device -> host | variable | Human-readable controller log |
| `0x1018` | device -> host | 14 | Slow status, exact fields unknown |
| `0x1019` | device -> host | 4 | Periodic status, exact fields unknown |
| `0x1020` | device -> host | 2 | Left/right vertical cart (height) positions |
| `0x1021` | device -> host | 4 | Left/right column rotation and unlock states |
| `0x1022` | both | variable | Device information/routing, inferred |
| `0x1023` | device -> host | 24 | Resistance profile echo, verified |
| `0x1024` | device -> host | 50 | High-rate resistance/motor telemetry |
| `0x1025` | device -> host | 4 | Arm position and lock state |
| `0x1026` | host -> device | 0 or 2 | Bring-up/routing operation |
| `0x1028` | host -> device | 4 | Resistance enable/disable, verified |
| `0x102a` | both | variable | Bring-up operation, unknown fields |
| `0x102c` | device -> host | variable | Bring-up response, unknown fields |
| `0x102e` | device -> host | >= 56 | Candidate electrical/mechanical telemetry; not live-verified |

Unknown messages can be represented by `Frame` and sent through `send_raw`,
but the library intentionally does not invent typed commands for them.

The replacement web UI reports power only if the candidate dedicated electrical
stream is received. An earlier experiment multiplying `0x1024` motor tension by
motor speed produced implausible
values, indicating that those fields cannot safely be combined as cable-output
power without an additional coordinate/scale conversion. When `0x102e` is not
subscribed, watts are therefore unavailable rather than estimated.

### Arm adjustment positions: `0x1020`, `0x1021`, and `0x1025`

The protocol models the three physical adjustments separately:

- `0x1020`: two bytes, left and right vertical cart positions (height).
- `0x1021`: four bytes: left rotation position, left unlock state, right
  rotation position, and right unlock state.
- `0x1025`: per-side telescope/arm setting, angle value, and lock flags.

The four-byte little-endian arm event is packed as follows:

| Bits | Field | Confidence |
| ---: | --- | --- |
| 0–7 | Telescope/arm-position setting | High-confidence inference |
| 8–15 | Arm angle value | High-confidence inference |
| 16–19 | Side (`0` left, nonzero right) | Verified from event formatting |
| 20 | Rotation locked | High-confidence inference |
| 21 | Rotation and telescope locked | High-confidence inference |
| 22–31 | Unknown/reserved | Unknown |

These are raw hardware values. Cart and column positions appear to be discrete
indices; the `0x1025` angle value has not been calibrated as degrees. The
library intentionally exposes the values unchanged until controlled movement
establishes their ranges and physical units.

## Resistance profile: `0x0013`

The payload is twelve 16-bit words:

| Offset | Default | Meaning/confidence |
| ---: | ---: | --- |
| `0x00` | variable | Base resistance in 0.1 lb, verified from 5–20 lb sweep |
| `0x02` | 0 | Eccentric added weight in 0.1 lb |
| `0x04` | 0 | ROM/Chains/Smart Flex weight in 0.1 lb |
| `0x06` | 0 / 8000 | Drop-set speed threshold when auto-spotter mode is 4 |
| `0x08` | 0 / 800 | Drop-set reduction percentage when auto-spotter mode is 4 |
| `0x0a` | 0 | Unknown |
| `0x0c` | 10 | Control mode; 10 is normal weight mode |
| `0x0e` | 0 | ROM mode in bits 0–4 and auto-spotter mode in bits 5–7 |
| `0x10` | 0 | Unknown |
| `0x12` | 2 | Motor location; 2 is virtual differential/bilateral |
| `0x14` | 20 | Frequency in 0.1 Hz; 20 is 2.0 Hz |
| `0x16` | 0 | Unknown |

Basic 5 lb profile:

```text
32 00 00 00 00 00 00 00 00 00 00 00
0a 00 00 00 00 00 02 00 14 00 00 00
```

The device answers with a `0x1023` echo of the 24-byte profile.

### Advanced profiles

The web panel exposes the following mappings. The named mode associations carry
the confidence shown in the table and in the implementation. Drop Set trigger
behavior has not yet been verified during a complete exercise.

| Mode | Offset `0x02` | Offset `0x04` | Flags `0x0e` |
| --- | ---: | ---: | ---: |
| Basic | 0 | 0 | `0x0000` |
| Spotter | 0 | 0 | `0x0040` |
| Drop Set | 0 | 0 | `0x0080` |
| Chains | 0 | 0–100% of base | `0x0000` |
| Eccentric | 0–60% of base | 0 | `0x0000` |
| Smart Flex | 0 | 0–60% of base | `0x0004` |

The UI defaults each adjustable mode to 25% and converts the result to the
nearest 0.1 lb. It rejects profiles whose peak base-plus-modifier load exceeds
the configured safety limit.

The ROM mode values defined by this protocol family are:

| Value | Curve |
| ---: | --- |
| 0 | Chains |
| 1 | Bell with eccentric |
| 2 | Reverse chains with eccentric |
| 3 | Chains with eccentric |
| 4 | Flat with eccentric (Smart Flex mapping) |
| 5 | Quadratic ascending with eccentric |
| 6 | Perturbation |
| 7 | Reverse chains |
| 8 | Eccentric reduction |

Only modes exposed by the normal web panel have established safety bounds.
Modes 1–3 and 5–8 remain unavailable through typed controls.

### Drop sets

Drop sets use controller auto-spotter mode 4. They are not implemented by the
host repeatedly sending lower base weights. Two additional profile words tune
the controller algorithm:

| Offset | Default value | Meaning |
| ---: | ---: | --- |
| `0x06` | `8000` | Drop-set speed threshold (`80` encoded × 100) |
| `0x08` | `800` | Reduction percent (`8` encoded × 100) |
| `0x0e` | `0x0080` | Auto-spotter mode 4 in bits 5–7 |

The defaults are therefore a threshold setting of 80 and an 8% reduction
per controller-triggered drop. The precise physical unit and filtering window
for the threshold remain unknown. The MCB applies the reductions internally and
needs no extra serial command after the configured profile is enabled. Which
`0x1024` contribution field most reliably represents each drop still requires a
complete drop-to-failure test.

Further testing is needed to verify trigger timing and whether repeated
reductions have a firmware-defined floor on `5.2.18.0`.

## Resistance toggle: `0x1028`

Two little-endian 16-bit words:

```text
00 00 02 00  enable
01 00 02 00  disable
```

The meaning of the second word is not proven. It is preserved as `mode=2`.

## High-rate telemetry: `0x1024`

The payload is 25 little-endian 16-bit words at approximately 50 Hz.

| Offset | Public field | Units | Confidence |
| ---: | --- | --- | --- |
| `0x00` | `control_mode` | raw enum | Inferred |
| `0x02` | `motor_tension_lb` | signed 0.1 lb | Motor tension |
| `0x04` | `total_weight_lb` | signed 0.1 lb | Total applied weight |
| `0x06` | `base_weight_lb` | signed 0.1 lb | Base contribution |
| `0x08` | `eccentric_weight_lb` | signed 0.1 lb | Eccentric contribution |
| `0x0a` | `rom_weight_lb` | signed 0.1 lb | ROM contribution |
| `0x0c` | `rom_weight_mode` | raw enum | Inferred |
| `0x0e` | `spotted_weight_lb` | signed 0.1 lb | Spotter contribution |
| `0x10` | `ramp_weight_lb` | signed 0.1 lb | Ramp contribution |
| `0x12` | `rack_weight_lb` | signed 0.1 lb | Rack contribution |
| `0x14` | `motor_position` | signed 0.1 reported unit | Motor position |
| `0x16` | `motor_speed` | signed 0.1 reported unit/s | Motor speed |
| `0x18` | `left_cable_position` | signed 0.1 reported unit | Left cable position |
| `0x1a` | `left_cable_speed` | signed 0.1 reported unit/s | Left cable speed |
| `0x1c` | `right_cable_position` | signed 0.1 reported unit | Right cable position |
| `0x1e` | `right_cable_speed` | signed 0.1 reported unit/s | Right cable speed |
| `0x20` | `status` | bit field | Enable latch and other state bits, inferred |
| `0x22` | `rep_count` | integer | Rep counter, high-confidence inference |
| `0x24–0x30` | `raw_words[18:25]` | unknown | Preserved, undecoded |

All load contributions are signed. For example, wire value `0xff9c` is -100,
or -10.0 lb; interpreting it as unsigned produces the invalid 6543.6 lb value.

Telemetry word 16 at offset `0x20` carries a device enable latch in its low
bit. Across controlled ON/OFF transitions, the bit was 0 while enabled and 1
while disabled, even when other bits in the word varied. The library exposes
this as `device_enabled`; unlike `active`, it remains meaningful at the 5 lb
standby level.

### Candidate electrical telemetry: `0x102e`

The replacement bring-up has not produced this stream, so its message ID and
layout are not verified on firmware `5.2.18.0`. The definitive message ID may
be `0x102e` or `0x102f`. The library currently recognizes `0x102e` only and
requires at least 56 payload bytes; it does not synthesize watts when absent.

The candidate payload has an eight-byte prefix followed by signed little-endian
32-bit values scaled by 1/1000:

| Offset | Public field |
| ---: | --- |
| `0x08` | `motor_mechanical_power_w` |
| `0x0c` | `motor_electrical_power_w` |
| `0x10` | `motor_thermal_power_w` |
| `0x14` | `supply_power_w` |
| `0x18–0x20` | phase A/B/C current |
| `0x24–0x2c` | phase A/B/C voltage |
| `0x30` | DC-link current |
| `0x34` | DC-link voltage |

Its subscription/routing exchange and definitive firmware-specific message ID
remain under investigation.

Type `0x0003` is believed to be an active-state transition, but sending it to
an already paired/active controller coincided with a fast-blinking red fault,
fan shutdown, and loss of standby resistance. It is excluded from automatic
bring-up and must not be sent without a verified state precondition.

## Safety requirements

- Treat USB VBUS connection as capable of causing motion.
- Complete bring-up before relying on telemetry.
- Configure a bounded resistance before enabling it.
- Monitor applied resistance and enforce a timeout.
- Send disable on normal shutdown and provide a physical power disconnect.
- Never replay an arbitrary raw frame.
