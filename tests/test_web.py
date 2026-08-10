from swolectl.controller import Controller
from swolectl.messages import ElectricalTelemetry, MotorTelemetry
from swolectl.transport import MemoryTransport
from swolectl.web import MotorService


def test_power_is_unavailable_without_electrical_telemetry() -> None:
    controller = Controller(transport_factory=lambda _port: MemoryTransport())
    words = [0] * 25
    words[1] = 100  # 10 lbf
    words[11] = 100  # 10 in/s
    controller.telemetry = MotorTelemetry.decode(MotorTelemetry._STRUCT.pack(*words))
    service = MotorService(controller)

    telemetry = service.status()["telemetry"]
    assert telemetry["power_watts"] is None
    assert telemetry["power_source"] is None


def test_dedicated_electrical_power_takes_precedence() -> None:
    controller = Controller(transport_factory=lambda _port: MemoryTransport())
    controller.telemetry = MotorTelemetry.decode(MotorTelemetry._STRUCT.pack(*([0] * 25)))
    controller.electrical_telemetry = ElectricalTelemetry.decode(
        b"\0" * 8 + (42_000).to_bytes(4, "little", signed=True) + b"\0" * 44
    )
    service = MotorService(controller)

    telemetry = service.status()["telemetry"]
    assert telemetry["power_watts"] == 42.0
    assert telemetry["power_source"] == "electrical_telemetry"
