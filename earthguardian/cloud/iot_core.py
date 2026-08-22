"""The AWS IoT Core for LoRaWAN uplink envelope.

Every uplink the platform ingests is wrapped in the structure AWS IoT Core for
LoRaWAN actually publishes: a base64 ``PayloadData`` and a ``WirelessMetadata``
block carrying the device identity, the radio parameters and per-gateway signal
quality. The decoder that unwraps it is the local stand-in for the Lambda an
IoT Rule would invoke.

Two conventions from the AWS documentation are worth honouring rather than
smoothing over, because code written against real uplinks has to handle them:

* ``Battery`` is a byte with reserved values - 0 means the device is on external
  power and 255 means it cannot measure - so it is not a percentage and cannot
  be scaled as one.
* ``Margin`` is demodulation SNR in whole decibels, clamped to -32..+31.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta, timezone

#: Values 0 and 255 in the Battery byte are flags, not levels.
BATTERY_EXTERNAL_POWER = 0
BATTERY_UNMEASURABLE = 255


def device_identity(plot_id: str) -> tuple[str, str, str]:
    """Stable ``(WirelessDeviceId, DevEui, DevAddr)`` for a plot.

    Derived from the plot id so the same plot keeps the same device identity
    across runs - a device that changes its DevEui every time the simulation
    restarts would make the raw zone impossible to reason about.
    """
    digest = hashlib.sha256(plot_id.encode()).hexdigest()
    wireless_id = str(uuid.UUID(digest[:32]))
    dev_eui = digest[32:48]
    dev_addr = digest[48:56]
    return wireless_id, dev_eui, dev_addr


def encode_battery(percent: float) -> int:
    """Percent to the LoRaWAN battery byte, avoiding the reserved values."""
    return max(1, min(254, round(percent / 100.0 * 253) + 1))


def decode_battery(value: int) -> float | None:
    """Battery byte back to percent. ``None`` where the byte is a flag."""
    if value in (BATTERY_EXTERNAL_POWER, BATTERY_UNMEASURABLE):
        return None
    return (value - 1) / 253.0 * 100.0


def wrap_uplink(
    plot_id: str,
    payload: bytes,
    *,
    timestamp: datetime,
    spreading_factor: int,
    data_rate: int,
    rssi: float,
    battery_pct: float,
    frame_counter: int,
    timezone_offset_h: int = 0,
    gateway_eui: str = "80029cfffe5cf1cc",
    frequency_hz: int = 916_800_000,
    fport: int = 1,
) -> dict:
    """Build the uplink exactly as AWS IoT Core for LoRaWAN would publish it.

    ``timestamp`` is the node's local wall clock and ``timezone_offset_h`` its
    offset from UTC. The envelope carries UTC, because that is what AWS emits
    and because a fleet spanning time zones that stores local time is a fleet
    whose data cannot be compared with itself. Passing a naive timestamp with no
    offset and letting it be read as UTC is the single most common way this goes
    wrong, so the conversion is explicit here rather than implied.
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone(timedelta(hours=timezone_offset_h)))

    wireless_id, dev_eui, dev_addr = device_identity(plot_id)
    # Demodulation margin: SNR in whole dB, and the band is clamped by the spec.
    margin = max(-32, min(31, round(rssi + 130.0)))

    return {
        "WirelessDeviceId": wireless_id,
        "PayloadData": base64.b64encode(payload).decode("ascii"),
        "WirelessMetadata": {
            "LoRaWAN": {
                "ADR": True,
                "Bandwidth": 125,
                "ClassB": False,
                "CodeRate": "4/5",
                "DataRate": str(data_rate),
                "DevAddr": dev_addr,
                "DevEui": dev_eui,
                "FCnt": frame_counter,
                "FPort": fport,
                "Frequency": str(frequency_hz),
                "Battery": encode_battery(battery_pct),
                "Margin": margin,
                "Gateways": [
                    {
                        "GatewayEui": gateway_eui,
                        "Snr": round(margin + 0.0, 2),
                        "Rssi": round(rssi, 2),
                    }
                ],
                "MType": "UnconfirmedDataUp",
                "Major": "LoRaWANR1",
                "Modulation": "LORA",
                "PolarizationInversion": False,
                "SpreadingFactor": spreading_factor,
                "Timestamp": timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        },
    }


def decode_uplink(envelope: dict) -> dict:
    """Unwrap an uplink into a flat record - what the Lambda hands downstream.

    Imports the payload codec the device firmware writes with, so a change to
    the wire format cannot be made on one side only.
    """
    from earthguardian.edge.lorawan import decode_payload

    lorawan = envelope["WirelessMetadata"]["LoRaWAN"]
    measurements = decode_payload(base64.b64decode(envelope["PayloadData"]))

    gateways = lorawan.get("Gateways") or [{}]
    battery_byte = lorawan.get("Battery", BATTERY_UNMEASURABLE)

    record = {
        "wireless_device_id": envelope["WirelessDeviceId"],
        "dev_eui": lorawan["DevEui"],
        "timestamp": lorawan["Timestamp"],
        "frame_counter": lorawan.get("FCnt"),
        "data_rate": int(lorawan["DataRate"]),
        "spreading_factor": lorawan.get("SpreadingFactor"),
        "frequency_hz": int(lorawan["Frequency"]),
        "rssi_dbm": gateways[0].get("Rssi"),
        "snr_db": gateways[0].get("Snr"),
        "gateway_eui": gateways[0].get("GatewayEui"),
        # The payload carries a percentage; the envelope carries the coarse
        # LoRaWAN byte. Keep both - they disagree, and which one is right is a
        # question worth being able to ask.
        "battery_pct_envelope": decode_battery(battery_byte),
    }
    record.update(measurements)
    return record
